"""Tests for the learning-cache policy (no global vault writes).

Covers: artifacts land under <cache_path>/learning/{artifact_id}/; any write
into the global vault is rejected.

Integration tests exercise the real ``akms_learn`` compiler end-to-end and the
path-traversal-safe artifact serving endpoint.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

HAS_AKMS_LEARN = importlib.util.find_spec("akms_learn") is not None


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/learn/test_learn_compile_contract.py — tmp cache_path, no real FS)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    """TestClient with tmp-path settings so no real filesystem state is touched."""
    cfg_path = tmp_path / "config.toml"
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg_path),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
        },
    ):
        from logic_loom_api.deps import reload_settings  # type: ignore[import-not-found]

        reload_settings()
        from logic_loom_api.main import api  # type: ignore[import-not-found]

        with TestClient(api) as c:
            yield c
        reload_settings()


def _fixture_graph_slice_payload() -> dict:
    """Minimal dict matching akms_learn.fixture_graph() in GraphSlicePayload shape."""
    from akms_learn import fixture_graph  # type: ignore[import-not-found]

    gs = fixture_graph()
    return {
        "nodes": list(gs.nodes),
        "edges": list(gs.edges),
        "metadata": dict(gs.metadata),
    }


def _base_request_body(**overrides) -> dict:
    """Return a minimal valid CompileRequest body (GraphSlice path)."""
    body: dict = {
        "generation_option": "deterministic_outline",
        "topic": "j2 return mapping",
        "goal": "Understand the j2 return-mapping algorithm",
        "graph_slice": _fixture_graph_slice_payload(),
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Learning-cache policy tests
# ---------------------------------------------------------------------------


class TestLearningCachePolicy:
    """Integration tests for the learning-cache write policy + global-vault guard."""

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_compile_writes_under_learning_artifact_id(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Verifies: generated artifacts persist under <cache_path>/learning/{artifact_id}/.

        Requirement: 'Artifacts land under <cache_path>/learning/{artifact_id}/.'
        Passes when: after a compile, the packet file (and any exports) exist on
        disk under that path, and the response packet_path/exports point there
        (and are not null/empty).
        """
        body = _base_request_body()
        r = client.post("/api/learn/compile", json=body)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

        data = r.json()
        artifact_id = data["artifact_id"]

        # Expected on-disk artifact dir under the tmp cache_path.
        expected_dir = tmp_path / "cache" / "learning" / artifact_id
        assert expected_dir.is_dir(), (
            f"artifact dir {expected_dir} must exist after compile"
        )

        # cache_dir advertises the created artifact dir.
        assert data["cache_dir"] == str(expected_dir), (
            f"cache_dir {data['cache_dir']!r} must equal {expected_dir!r}"
        )

        # packet_path is now populated and points inside the artifact dir.
        assert data["packet_path"] is not None, "packet_path must be populated"
        packet_path = Path(data["packet_path"])
        assert packet_path.is_file(), f"packet file {packet_path} must exist on disk"
        assert packet_path.parent == expected_dir, (
            "packet_path must live under the artifact dir"
        )

        # exports is now non-empty and every entry exists on disk under the dir.
        assert isinstance(data["exports"], list) and len(data["exports"]) >= 1, (
            "exports must be a non-empty list"
        )
        for export in data["exports"]:
            assert "path" in export and "type" in export, (
                "each export must carry path + type"
            )
            ep = Path(export["path"])
            assert ep.is_file(), f"exported artifact {ep} must exist on disk"
            assert ep.resolve().is_relative_to(expected_dir.resolve()), (
                f"export {ep} must live under the artifact dir"
            )
        # The canonical packet must be tagged 'packet'.
        assert any(e["type"] == "packet" for e in data["exports"]), (
            "exports must include the canonical packet (type='packet')"
        )

    @pytest.mark.integration
    def test_global_vault_write_rejected(self, tmp_path: Path) -> None:
        """Verifies: any write whose target resolves inside the AKMS global vault is rejected.

        Requirement: 'Any attempt to write into the global vault is rejected.'
        Calls the guard directly with a target inside the (tmp) global vault and
        asserts it raises. Also verifies a target inside the AKMS source tree is
        rejected, and that a benign cache target is allowed.
        """
        from logic_loom_api.adapters import learn_adapter  # type: ignore[import-not-found]
        from logic_loom_api.config import Settings  # type: ignore[import-not-found]

        vault = tmp_path / "global_vault"
        vault.mkdir()
        akms_repo = tmp_path / "akms_repo"
        akms_repo.mkdir()
        cache = tmp_path / "cache"

        settings = Settings(
            akms_global_vault=vault,
            akms_repo=akms_repo,
            cache_path=cache,
        )

        # 1) Target directly inside the global vault → rejected.
        with pytest.raises(learn_adapter.VaultWriteGuardError):
            learn_adapter.assert_outside_protected_roots(
                vault / "learning" / "abc" / "lesson.md", settings
            )

        # 2) The vault root itself → rejected.
        with pytest.raises(learn_adapter.VaultWriteGuardError):
            learn_adapter.assert_outside_protected_roots(vault, settings)

        # 3) A `..` traversal that resolves back into the vault → rejected
        #    (proves the check operates on resolved, not raw, paths).
        with pytest.raises(learn_adapter.VaultWriteGuardError):
            learn_adapter.assert_outside_protected_roots(
                cache / ".." / "global_vault" / "sneaky.md", settings
            )

        # 4) Target inside the AKMS source tree → rejected.
        with pytest.raises(learn_adapter.VaultWriteGuardError):
            learn_adapter.assert_outside_protected_roots(
                akms_repo / "Packages" / "evil.md", settings
            )

        # 5) A benign cache target → allowed (returns the resolved path).
        ok_target = cache / "learning" / "abc" / "lesson.md"
        resolved = learn_adapter.assert_outside_protected_roots(ok_target, settings)
        assert resolved == ok_target.resolve(strict=False)

    @pytest.mark.integration
    def test_guard_resolve_failure_fallback_collapses_dotdot(
        self, tmp_path: Path
    ) -> None:
        """Verifies: the OSError resolve-failure fallback still collapses ``..``.

        When ``Path.resolve(strict=False)`` raises ``OSError``, the guard falls
        back to ``os.path.normpath(p.absolute())`` for BOTH the target and the
        protected roots. This proves the fallback is not weaker than ideal: a
        ``..``-containing target that lexically lands inside the global vault is
        still rejected (a plain ``.absolute()`` fallback would NOT collapse the
        ``..`` and could slip the containment check).
        """
        from logic_loom_api.adapters import learn_adapter  # type: ignore[import-not-found]
        from logic_loom_api.config import Settings  # type: ignore[import-not-found]

        vault = tmp_path / "global_vault"
        vault.mkdir()
        akms_repo = tmp_path / "akms_repo"
        akms_repo.mkdir()
        cache = tmp_path / "cache"

        settings = Settings(
            akms_global_vault=vault,
            akms_repo=akms_repo,
            cache_path=cache,
        )

        # Force the OSError fallback branch for every resolve() call.
        def _raise_oserror(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            raise OSError("simulated resolve failure")

        # A ``..`` traversal that lexically normalises back into the vault.
        sneaky = cache / ".." / "global_vault" / "sneaky.md"

        with patch.object(Path, "resolve", _raise_oserror):
            # Fallback path must still reject the normalised-into-vault target.
            with pytest.raises(learn_adapter.VaultWriteGuardError):
                learn_adapter.assert_outside_protected_roots(sneaky, settings)

            # A benign cache target stays allowed through the same fallback.
            ok_target = cache / "learning" / "abc" / "lesson.md"
            resolved = learn_adapter.assert_outside_protected_roots(
                ok_target, settings
            )
            assert resolved == Path(os.path.normpath(ok_target.absolute()))

    @pytest.mark.integration
    def test_artifact_endpoint_rejects_path_traversal(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Verifies: the /artifacts endpoint never serves a file outside the artifact dir.

        Artifact paths are sanitized to prevent path traversal. A
        ``..``-containing request path must NOT escape the artifact dir; it must
        be rejected (400/403/404) and never serve an outside file.
        """
        # Plant a secret OUTSIDE the learning cache (under the tmp cache root).
        cache_root = tmp_path / "cache"
        secret = cache_root / "secret.txt"
        cache_root.mkdir(parents=True, exist_ok=True)
        secret.write_text("TOP SECRET", encoding="utf-8")

        # Create a real artifact dir with a legitimate file.
        artifact_id = "deadbeef"
        artifact_dir = cache_root / "learning" / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "lesson.md").write_text("# lesson", encoding="utf-8")

        # Sanity: the legitimate file IS served.
        ok = client.get(f"/api/learn/artifacts/{artifact_id}/lesson.md")
        assert ok.status_code == 200, f"legit file should serve: {ok.status_code}"
        assert ok.text == "# lesson"

        # Traversal attempts that try to climb out of the artifact dir to read
        # the planted secret must NOT succeed (never 200 with the secret).
        traversal_paths = [
            "../../secret.txt",
            "../secret.txt",
            "..%2f..%2fsecret.txt",
        ]
        for tp in traversal_paths:
            resp = client.get(f"/api/learn/artifacts/{artifact_id}/{tp}")
            assert resp.status_code in (400, 403, 404), (
                f"traversal {tp!r} must be rejected, got {resp.status_code}"
            )
            assert "TOP SECRET" not in resp.text, (
                f"traversal {tp!r} leaked the secret file"
            )

        # An artifact_id that is itself a traversal segment must be rejected.
        bad = client.get("/api/learn/artifacts/..%2f..%2fsecret.txt/lesson.md")
        assert bad.status_code in (400, 403, 404)
        assert "TOP SECRET" not in bad.text

        # A missing file inside an existing artifact dir → 404.
        missing = client.get(f"/api/learn/artifacts/{artifact_id}/nope.md")
        assert missing.status_code == 404
