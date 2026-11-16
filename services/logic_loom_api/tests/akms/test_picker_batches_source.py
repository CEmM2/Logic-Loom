"""Picker adapter wired onto batches.json.

Covers:
  With a populated batches.json, the picker renders correctly
         (the adapter materialises the legacy markdown from it).
  With only the legacy generation_plan.md, the picker still
         renders (fallback path).
  + degrade-gracefully — neither source present => main.py mount logs
    a warning and the rest of the API stays up.

Tier: integration. The tests poke the adapter's data-source resolver
(``_resolve_plan_md``) directly so they don't need the upstream
``akms_nodes_gen`` package installed — that keeps them runnable in
environments where the AKMS sibling repo may not be present.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.adapters import picker_adapter
from logic_loom_api.batches import BatchNode, BatchStore, EditableBatch
from logic_loom_api.config import Settings


def _seed_batches_json(workspace: Path) -> Path:
    """Write a minimal but legacy-compatible batches.json under workspace."""
    workspace.mkdir(parents=True, exist_ok=True)
    store = BatchStore(
        batches=[
            EditableBatch(
                id="R1_B1",
                title="Tensor Operations",
                round=1,
                round_title="Tensor Algebra",
                round_theme="Foundations.",
                sources_text="Belytschko Ch. 2-3",
                nodes=[
                    BatchNode(
                        node_id="tensor-index-notation",
                        title="Index Notation",
                        tags=["small"],
                    )
                ],
            )
        ]
    )
    return store.save(workspace)


def _seed_legacy_plan(repo_root: Path) -> Path:
    """Write a legacy generation_plan.md under repo_root/Packages/AKMS_nodes_gen/."""
    legacy_dir = repo_root / "Packages" / "AKMS_nodes_gen"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    plan_path = legacy_dir / "generation_plan.md"
    plan_path.write_text(
        "# Round 1: Legacy Round\n\n"
        "## R1_B1 — Legacy Batch (1 nodes)\n"
        "**Sources:** legacy source\n\n"
        "| # | Node ID | Title | Size |\n"
        "|---|---------|-------|------|\n"
        "| 1 | `legacy-node` | Legacy Node | small |\n",
        encoding="utf-8",
    )
    return plan_path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """An isolated AKMS workspace under tmp_path (no batches.json yet)."""
    ws = tmp_path / "akms-workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """An isolated AKMS repo_root under tmp_path (no legacy plan yet)."""
    rr = tmp_path / "akms-repo"
    rr.mkdir(parents=True, exist_ok=True)
    return rr


def _settings(workspace: Path, repo_root: Path) -> Settings:
    return Settings.load(akms_workspace=workspace, akms_repo=repo_root)


class TestPickerPrefersBatchesJson:
    """batches.json wins when present."""

    def test_resolve_plan_md_uses_batches_json(
        self, workspace: Path, repo_root: Path
    ):
        _seed_batches_json(workspace)
        # Also seed the legacy file so we can prove batches.json wins.
        _seed_legacy_plan(repo_root)
        s = _settings(workspace, repo_root)
        plan = picker_adapter._resolve_plan_md(s)
        # The chosen path lives under the workspace cache, not the legacy
        # location.
        assert plan.parent == workspace / ".cache"
        assert plan.name == "generation_plan.md"
        assert plan.exists()
        # The materialised text carries the workspace batch, not the
        # legacy one.
        body = plan.read_text(encoding="utf-8")
        assert "## R1_B1 — Tensor Operations" in body
        assert "legacy-node" not in body

    def test_materialised_file_round_trips_through_upstream_parser_regex(
        self, workspace: Path, repo_root: Path
    ):
        """The materialised plan must satisfy the upstream parser's
        header regex — that is the contract that lets the picker read it.
        """
        _seed_batches_json(workspace)
        s = _settings(workspace, repo_root)
        plan = picker_adapter._resolve_plan_md(s)
        body = plan.read_text(encoding="utf-8")
        # Mirror akms_nodes_gen.batch_picker.plan_parser._BATCH_HEADER.
        import re

        assert re.search(r"^## R\d+_B\d+ — .+ \(\d+ nodes?\)$", body, re.M)


class TestPickerFallsBackToLegacyPlan:
    """When batches.json is absent, the legacy file is used."""

    def test_legacy_fallback_used_when_no_batches_json(
        self, workspace: Path, repo_root: Path
    ):
        # No batches.json; legacy plan only.
        legacy = _seed_legacy_plan(repo_root)
        s = _settings(workspace, repo_root)
        plan = picker_adapter._resolve_plan_md(s)
        assert plan == legacy
        # No .cache materialisation happened.
        assert not (workspace / ".cache" / "generation_plan.md").exists()


class TestLegacyPlanNeedsAkmsCheckout:
    """The legacy plan is consulted only when an akms_repo checkout is
    present; without one the workspace batches.json is the only source."""

    def test_batches_json_resolves_without_akms_checkout(
        self, workspace: Path, tmp_path: Path
    ):
        _seed_batches_json(workspace)
        s = _settings(workspace, tmp_path / "no-akms-source-checkout")
        plan = picker_adapter._resolve_plan_md(s)
        assert plan == workspace / ".cache" / "generation_plan.md"
        assert "## R1_B1 — Tensor Operations" in plan.read_text(encoding="utf-8")

    def test_missing_checkout_is_not_probed_for_legacy_plan(
        self, workspace: Path, tmp_path: Path
    ):
        """No batches.json and no checkout → FileNotFoundError that names
        the workspace source and says no akms_repo is configured, rather
        than a path under the missing checkout."""
        missing_repo = tmp_path / "no-akms-source-checkout"
        s = _settings(workspace, missing_repo)
        with pytest.raises(FileNotFoundError) as ei:
            picker_adapter._resolve_plan_md(s)
        msg = str(ei.value)
        assert "batches.json" in msg
        assert "no akms_repo checkout configured" in msg
        assert str(missing_repo) not in msg


class TestPickerMountContinuesWhenBothMissing:
    """Neither source => FileNotFoundError, mount
    logs and the rest of the API stays up.
    """

    def test_resolve_plan_md_raises_when_both_missing(
        self, workspace: Path, repo_root: Path
    ):
        # Both files absent.
        s = _settings(workspace, repo_root)
        with pytest.raises(FileNotFoundError) as ei:
            picker_adapter._resolve_plan_md(s)
        # Error message names both candidate paths so the operator can act.
        msg = str(ei.value)
        assert "batches.json" in msg
        assert "generation_plan.md" in msg

    def test_main_app_mounts_when_both_missing(
        self, workspace: Path, repo_root: Path, tmp_path: Path, monkeypatch, caplog
    ):
        """End-to-end: main.py's degrade-gracefully wrapper catches
        FileNotFoundError and the rest of the API stays reachable.
        """
        # Isolate from the developer's real ~/.config/logic-loom/config.toml.
        # main.py calls Settings.load() with no config_path, so without this
        # the user's TOML (which sets a real akms_repo containing a
        # generation_plan.md) takes effect and the picker mounts — masking the
        # degrade-gracefully path this test exercises.
        monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(tmp_path / "missing.toml"))
        monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(workspace))
        monkeypatch.setenv("LOGIC_LOOM_AKMS_REPO", str(repo_root))

        import logic_loom_api.main as main_mod

        with caplog.at_level(logging.WARNING):
            importlib.reload(main_mod)

        client = TestClient(main_mod.api)
        # /api/app/health is the universal degrade-gracefully sentinel.
        r = client.get("/api/app/health")
        assert r.status_code == 200
        # Picker namespace 404s (mount was skipped).
        r = client.get("/api/picker/api/papers")
        assert r.status_code == 404
        # Warning was logged at mount time.
        assert any("picker mount skipped" in rec.message for rec in caplog.records)

    def test_first_launch_mounts_the_picker_once_the_workspace_exists(
        self, tmp_path: Path, monkeypatch
    ):
        """First launch of a fresh install: no workspace exists at import time.

        The import-time mount is skipped because nothing has seeded
        ``batches.json`` yet. The lifespan bootstraps the workspace and retries,
        so the picker is available without restarting the app.
        """
        fresh = tmp_path / "fresh-home" / "AKMS-Workspace"
        monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(tmp_path / "missing.toml"))
        monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(fresh))
        monkeypatch.setenv("LOGIC_LOOM_CACHE_PATH", str(tmp_path / "cache"))
        monkeypatch.delenv("LOGIC_LOOM_AKMS_REPO", raising=False)

        import logic_loom_api.main as main_mod

        reloaded = importlib.reload(main_mod)
        assert reloaded._picker_mounted is False
        assert not (fresh / "batches.json").exists()

        with TestClient(reloaded.api) as client:  # runs the lifespan
            assert (fresh / "batches.json").is_file()
            assert reloaded._picker_mounted is True
            assert client.get("/api/picker/api/batches").status_code == 200


class TestBatchesJsonMaterialisationIsIdempotent:
    """Sanity: calling _resolve_plan_md twice on the same state is safe
    and stable (no churn on the cache file).
    """

    def test_two_calls_produce_identical_cache(
        self, workspace: Path, repo_root: Path
    ):
        _seed_batches_json(workspace)
        s = _settings(workspace, repo_root)
        plan_a = picker_adapter._resolve_plan_md(s)
        body_a = plan_a.read_text(encoding="utf-8")
        plan_b = picker_adapter._resolve_plan_md(s)
        body_b = plan_b.read_text(encoding="utf-8")
        assert plan_a == plan_b
        assert body_a == body_b
