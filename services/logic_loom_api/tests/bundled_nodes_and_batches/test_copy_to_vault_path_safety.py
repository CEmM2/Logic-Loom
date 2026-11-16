"""Regression tests: copy-to-vault path safety.

Previously ``copy_to_vault`` built its target path
from the UNTRUSTED frontmatter ``id`` with no allowlist, then used
``exists()`` + a truncating ``open("wb")``:

  * an id containing ``/``, ``\\`` or ``..`` could write OUTSIDE
    ``generated_nodes/`` (path traversal);
  * the ``exists()``→``open()`` gap is a TOCTOU that could truncate a
    symlink/concurrent creator.

AKMS ``GlobalNodeFrontmatter`` only requires ``id: str`` — nothing enforces a
filename-safe format — so this is a genuine defence-in-depth gap on a write
endpoint. These tests plant a malicious established node (via a monkeypatched
established root) and assert the endpoint refuses it with a clean 400 and
writes nothing outside the target dir, plus that the exclusive-create path
still yields the non-destructive 409.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

import frontmatter  # type: ignore[import-not-found,import-untyped]
from fastapi import FastAPI
from fastapi.testclient import TestClient

from logic_loom_api.adapters import graph_adapter as ga
from logic_loom_api.config import Settings
from logic_loom_api.routes import nodes as nodes_mod


# ── Fixture helpers ───────────────────────────────────────────────────────


def _make_settings(tmp_path: Path) -> Settings:
    repo = tmp_path / "AKMS"
    vault = tmp_path / "vault"
    cache = tmp_path / "cache"
    workspace = tmp_path / "workspace"
    for d in (repo, vault, cache, workspace):
        d.mkdir(parents=True, exist_ok=True)
    return Settings(
        akms_repo=repo,
        akms_global_vault=vault,
        cache_path=cache,
        akms_workspace=workspace,
    )


def _make_app(settings: Settings) -> FastAPI:
    from logic_loom_api.deps import get_settings

    app = FastAPI()
    app.include_router(nodes_mod.router, prefix="/api/nodes")
    app.dependency_overrides[get_settings] = lambda: settings
    return app


def _write_established_node(root: Path, filename: str, node_id: str) -> Path:
    """Plant a node under a (monkeypatched) established root with *node_id*."""
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post("# planted\n")
    post.metadata = {
        "id": node_id,
        "title": "Planted Node",
        "domain": "test-domain",
        "tags": [],
        "status": "established",
        "confidence": 0.9,
        "source": "human",
        "edges": [],
        "akms_schema": "v2",
    }
    with path.open("wb") as f:
        f.write(frontmatter.dumps(post).encode("utf-8"))
    return path


# ── Tests ─────────────────────────────────────────────────────────────────


class TestCopyToVaultPathSafety:
    """Path-safety + write-semantics regression for copy-to-vault."""

    @pytest.mark.integration
    @pytest.mark.parametrize(
        "bad_id",
        ["../../evil", "sub/evil", "..", "/abs/evil", "evil\\win", ".", "...", "...."],
    )
    def test_traversal_id_rejected_and_nothing_escapes(
        self, tmp_path, monkeypatch, bad_id
    ):
        """An established node whose id is NOT filename-safe → 400, and nothing
        is written outside generated_nodes/."""
        est_root = tmp_path / "established"
        est_root.mkdir(parents=True)
        planted = _write_established_node(est_root, "planted.md", bad_id)
        monkeypatch.setattr(ga, "established_library_root", lambda: est_root)

        settings = _make_settings(tmp_path)
        target_dir = settings.akms_generated_nodes_dir()

        client = TestClient(_make_app(settings))
        resp = client.post("/api/nodes/copy-to-vault", json={"path": str(planted)})

        assert resp.status_code == 400, (
            f"malicious id {bad_id!r} must be rejected 400, got "
            f"{resp.status_code}: {resp.text}"
        )
        # Nothing written under the target dir …
        if target_dir.exists():
            assert list(target_dir.rglob("*.md")) == [], (
                f"copy wrote a file into the target dir for id {bad_id!r}"
            )
        # … and no stray *.md leaked anywhere else in the tmp tree (traversal).
        leaked = [
            p
            for p in tmp_path.rglob("*-copy.md")
        ]
        assert leaked == [], f"copy escaped to {leaked} for id {bad_id!r}"

    @pytest.mark.integration
    def test_safe_id_happy_path_still_copies(self, tmp_path, monkeypatch):
        """A filename-safe established id still copies to a Tier-B draft."""
        est_root = tmp_path / "established"
        est_root.mkdir(parents=True)
        planted = _write_established_node(est_root, "good.md", "good-node")
        monkeypatch.setattr(ga, "established_library_root", lambda: est_root)

        settings = _make_settings(tmp_path)
        client = TestClient(_make_app(settings))
        resp = client.post("/api/nodes/copy-to-vault", json={"path": str(planted)})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["copy_id"] == "good-node-copy"
        assert body["tier"] == "B"
        copy_path = Path(body["copy_path"])
        assert copy_path.parent.resolve() == settings.akms_generated_nodes_dir().resolve()
        assert copy_path.is_file()

    @pytest.mark.integration
    def test_preexisting_target_is_409_no_clobber(self, tmp_path, monkeypatch):
        """Exclusive-create: a pre-existing target yields 409 and is NOT
        truncated (the TOCTOU/clobber fix)."""
        est_root = tmp_path / "established"
        est_root.mkdir(parents=True)
        planted = _write_established_node(est_root, "good.md", "good-node")
        monkeypatch.setattr(ga, "established_library_root", lambda: est_root)

        settings = _make_settings(tmp_path)
        target_dir = settings.akms_generated_nodes_dir()
        target_dir.mkdir(parents=True, exist_ok=True)

        # Pre-create the exact target with sentinel content.
        existing = target_dir / "good-node-copy.md"
        sentinel = "DO NOT CLOBBER\n"
        existing.write_text(sentinel, encoding="utf-8")

        client = TestClient(_make_app(settings))
        resp = client.post("/api/nodes/copy-to-vault", json={"path": str(planted)})
        assert resp.status_code == 409, (
            f"pre-existing target must be 409, got {resp.status_code}: {resp.text}"
        )
        # Non-destructive: the existing file was NOT truncated/overwritten.
        assert existing.read_text(encoding="utf-8") == sentinel, (
            "exclusive-create failed to protect the pre-existing target"
        )

    @pytest.mark.integration
    def test_symlinked_target_dir_outside_workspace_rejected(self, tmp_path, monkeypatch):
        """If generated_nodes is a symlink pointing OUTSIDE
        the workspace, the write is rejected (containment anchored on the
        workspace root, not on target_dir) and nothing lands in the symlink
        target."""
        est_root = tmp_path / "established"
        est_root.mkdir(parents=True)
        planted = _write_established_node(est_root, "good.md", "good-node")
        monkeypatch.setattr(ga, "established_library_root", lambda: est_root)

        settings = _make_settings(tmp_path)
        # Point generated_nodes at an evil dir OUTSIDE the workspace.
        evil = tmp_path / "evil-outside"
        evil.mkdir()
        gen = settings.akms_generated_nodes_dir()
        gen.parent.mkdir(parents=True, exist_ok=True)
        gen.symlink_to(evil, target_is_directory=True)

        client = TestClient(_make_app(settings))
        resp = client.post("/api/nodes/copy-to-vault", json={"path": str(planted)})
        assert resp.status_code == 400, (
            f"symlinked-outside target dir must be rejected, got "
            f"{resp.status_code}: {resp.text}"
        )
        assert list(evil.glob("*.md")) == [], "copy escaped into the symlink target"

    @pytest.mark.integration
    @pytest.mark.skipif(
        os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"),
        reason="dir_fd/O_NOFOLLOW hardening is POSIX-only",
    )
    def test_symlinked_target_dir_inside_workspace_rejected(self, tmp_path, monkeypatch):
        """Even a symlinked generated_nodes pointing INSIDE the workspace is
        rejected by the O_NOFOLLOW dir open — physical-containment contract."""
        est_root = tmp_path / "established"
        est_root.mkdir(parents=True)
        planted = _write_established_node(est_root, "good.md", "good-node")
        monkeypatch.setattr(ga, "established_library_root", lambda: est_root)

        settings = _make_settings(tmp_path)
        real_gen = settings.akms_workspace / "real_gen"
        real_gen.mkdir(parents=True)
        gen = settings.akms_generated_nodes_dir()
        gen.parent.mkdir(parents=True, exist_ok=True)
        gen.symlink_to(real_gen, target_is_directory=True)

        client = TestClient(_make_app(settings))
        resp = client.post("/api/nodes/copy-to-vault", json={"path": str(planted)})
        assert resp.status_code == 400, (
            f"symlinked target dir must be rejected by O_NOFOLLOW, got "
            f"{resp.status_code}: {resp.text}"
        )
        assert list(real_gen.glob("*.md")) == [], "copy written through symlinked dir"

    @pytest.mark.integration
    @pytest.mark.skipif(
        os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"),
        reason="dir_fd/O_NOFOLLOW hardening is POSIX-only",
    )
    def test_ancestor_swap_root_pinned_by_nofollow(self, tmp_path):
        """The create walks down from the pinned
        workspace ROOT, so an ancestor (the root itself) swapped for a symlink is
        refused by O_NOFOLLOW — not just the leaf generated_nodes.

        Simulates the ancestor-swap end state by passing a symlinked root to the
        helper directly (the race window replaces the workspace root with a
        symlink between containment check and create); it must raise rather than
        follow the link and create under the attacker-controlled target.
        """
        real_root = tmp_path / "real-workspace"
        (real_root / "generated_nodes").mkdir(parents=True)
        evil = tmp_path / "evil"
        evil.mkdir()
        # An attacker swaps the workspace root for a symlink pointing at `evil`.
        swapped_root = tmp_path / "swapped-workspace"
        swapped_root.symlink_to(evil, target_is_directory=True)

        with pytest.raises(OSError):
            nodes_mod._exclusive_write_under_root(
                swapped_root, ("generated_nodes",), "x.md", lambda f: f.write(b"x")
            )
        # Nothing was created through the symlinked root.
        assert list(evil.rglob("*.md")) == [], "create followed a symlinked root"

    @pytest.mark.integration
    @pytest.mark.skipif(
        os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"),
        reason="dir_fd/O_NOFOLLOW hardening is POSIX-only",
    )
    def test_helper_happy_path_creates_and_is_exclusive(self, tmp_path):
        """The pinned-walk helper still creates on the happy path and is
        exclusive (second create → FileExistsError)."""
        root = tmp_path / "workspace"
        (root / "generated_nodes").mkdir(parents=True)
        nodes_mod._exclusive_write_under_root(
            root, ("generated_nodes",), "x.md", lambda f: f.write(b"hello")
        )
        created = root / "generated_nodes" / "x.md"
        assert created.read_bytes() == b"hello"
        with pytest.raises(FileExistsError):
            nodes_mod._exclusive_write_under_root(
                root, ("generated_nodes",), "x.md", lambda f: f.write(b"again")
            )

    @pytest.mark.integration
    @pytest.mark.skipif(
        os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"),
        reason="dir_fd/O_NOFOLLOW hardening is POSIX-only",
    )
    def test_write_failure_cleanup_is_pinned_not_path_based(self, tmp_path):
        """On a write failure, the partial-file
        cleanup must unlink via the pinned directory fd — NOT the mutable path —
        so a generated_nodes swapped for a symlink mid-write cannot redirect the
        unlink and delete an outside file of the same name.

        The write callback itself performs the swap (races in during the write),
        then raises. A path-based cleanup would delete outside/x.md; the pinned
        cleanup unlinks x.md at the ORIGINAL directory inode instead, so the
        outside victim survives.
        """
        root = tmp_path / "workspace"
        gen = root / "generated_nodes"
        gen.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        victim = outside / "x.md"
        victim.write_text("keep me", encoding="utf-8")

        def bad_write(f):
            f.write(b"partial")
            # Attacker swaps generated_nodes → symlink to `outside` mid-write.
            os.rename(str(gen), str(tmp_path / "gen-moved"))
            os.symlink(str(outside), str(gen), target_is_directory=True)
            raise RuntimeError("boom")

        with pytest.raises(nodes_mod._CopyWriteError):
            nodes_mod._exclusive_write_under_root(
                root, ("generated_nodes",), "x.md", bad_write
            )

        # The pinned cleanup removed the created file at its ORIGINAL inode…
        assert not (tmp_path / "gen-moved" / "x.md").exists(), (
            "pinned cleanup did not remove the partial file"
        )
        # …and did NOT follow the swapped symlink to delete the outside victim.
        assert victim.read_text(encoding="utf-8") == "keep me", (
            "cleanup unlinked through the swapped directory — path-based, not pinned!"
        )

    @pytest.mark.integration
    @pytest.mark.skipif(
        os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"),
        reason="dir_fd/O_NOFOLLOW hardening is POSIX-only",
    )
    def test_flush_close_failure_removes_partial(self, tmp_path, monkeypatch):
        """A buffered flush failure surfacing at
        close() — AFTER write_fn returns (ENOSPC/EIO/NFS) — must still unlink the
        partial file and be reported as a write failure, not leave a corrupt
        409-stub. The cleanup guard must wrap close(), not just write_fn.
        """
        root = tmp_path / "workspace"
        (root / "generated_nodes").mkdir(parents=True)

        real_fdopen = os.fdopen

        class _CloseFails:
            def __init__(self, real):
                self._real = real

            def write(self, b):
                return self._real.write(b)

            def close(self):
                # Close the real fd (no leak), then surface a flush-style error.
                self._real.close()
                raise OSError(errno.EIO, "simulated flush/close failure")

        monkeypatch.setattr(
            nodes_mod.os, "fdopen", lambda *a, **k: _CloseFails(real_fdopen(*a, **k))
        )

        with pytest.raises(nodes_mod._CopyWriteError):
            nodes_mod._exclusive_write_under_root(
                root, ("generated_nodes",), "x.md", lambda f: f.write(b"data")
            )
        # The partial file must have been cleaned up despite the failure only
        # surfacing at close().
        assert not (root / "generated_nodes" / "x.md").exists(), (
            "flush/close failure left a partial file behind (stuck-409 stub)"
        )
