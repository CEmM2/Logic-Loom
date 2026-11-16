"""No AKMS / zotero-summarizer source checkout is required.

``akms_repo``, ``zsum_repo`` and ``logic_loom_root`` are optional,
development-only overrides. These tests pin each reader's behaviour when the
checkout is absent: either it resolves from the installed packages / the
Logic-Loom workspace, or (for the few developer-script routes that genuinely
run code from a checkout) it fails with the same clean 4xx as before — never a
500 and never a path under a developer's home layout.

Every test runs with the suite's isolated config (see ``conftest.py``); the
"missing" checkouts are tmp paths that do not exist, and "unset" means the
``LOGIC_LOOM_*`` variable is removed.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.config import (
    Settings,
    detect_paths,
    first_run_status,
)
from logic_loom_api.deps import reload_settings


def _load(tmp_path: Path, **overrides) -> Settings:
    return Settings.load(config_path=tmp_path / "missing.toml", **overrides)


# ── Settings helpers ────────────────────────────────────────────────────────


class TestRepoDirHelpers:
    def test_missing_checkouts_resolve_to_none(self, tmp_path: Path) -> None:
        s = _load(
            tmp_path,
            akms_repo=tmp_path / "no-akms",
            zsum_repo=tmp_path / "no-zsum",
        )
        assert s.akms_repo_dir() is None
        assert s.zsum_repo_dir() is None

    def test_present_checkouts_resolve(self, tmp_path: Path) -> None:
        (tmp_path / "AKMS").mkdir()
        (tmp_path / "zsum").mkdir()
        s = _load(tmp_path, akms_repo=tmp_path / "AKMS", zsum_repo=tmp_path / "zsum")
        assert s.akms_repo_dir() == tmp_path / "AKMS"
        assert s.zsum_repo_dir() == tmp_path / "zsum"

    def test_a_file_is_not_a_checkout(self, tmp_path: Path) -> None:
        f = tmp_path / "AKMS"
        f.write_text("not a dir")
        assert _load(tmp_path, akms_repo=f).akms_repo_dir() is None


class TestConfigReadersWithoutCheckout:
    def test_global_vault_skips_missing_repo(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("AKMS_GLOBAL_VAULT", raising=False)
        s = _load(tmp_path, akms_repo=tmp_path / "no-akms")
        assert s.effective_akms_global_vault() == Path("~/.claude/akms/nodes").expanduser()

    def test_domain_pack_root_is_none_without_repo(self, tmp_path: Path) -> None:
        s = _load(tmp_path, akms_repo=tmp_path / "no-akms")
        assert s.learn_domain_packs_search_root() is None

    def test_domain_pack_root_uses_present_repo(self, tmp_path: Path) -> None:
        (tmp_path / "AKMS" / "Packages").mkdir(parents=True)
        s = _load(tmp_path, akms_repo=tmp_path / "AKMS")
        assert s.learn_domain_packs_search_root() == tmp_path / "AKMS" / "Packages"


class TestFirstRun:
    def test_only_vault_path_is_required(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "config.toml"
        # TOML literal string: a Windows path's backslashes are not escapes.
        cfg.write_text(f"vault_path = '{tmp_path / 'vault'}'\n")
        monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(cfg))
        status = first_run_status(_load(tmp_path))
        assert status["needs_setup"] is False
        assert status["missing_or_placeholder"] == []

    def test_template_placeholders_only_flag_the_vault(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            'akms_repo = "<AKMS_REPO_PATH>"\n'
            'zsum_repo = "<ZSUM_REPO_PATH>"\n'
            'vault_path = "<VAULT_PATH>"\n'
        )
        monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(cfg))
        status = first_run_status(_load(tmp_path))
        assert status["missing_or_placeholder"] == ["vault_path"]

    def test_seeded_template_needs_only_the_vault(self, tmp_path: Path, monkeypatch) -> None:
        """The shipped template leaves the checkouts commented out, so a
        fresh install asks for the vault and nothing else."""
        from logic_loom_api.config import ensure_user_config_exists

        cfg = tmp_path / "config.toml"
        monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(cfg))
        ensure_user_config_exists()
        assert cfg.exists()
        s = _load(tmp_path)
        assert first_run_status(s)["missing_or_placeholder"] == ["vault_path"]
        import tomllib

        raw = tomllib.loads(cfg.read_text())
        assert "akms_repo" not in raw and "zsum_repo" not in raw

    def test_first_run_save_with_only_the_vault(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        with patch.dict(os.environ, {"LOGIC_LOOM_CONFIG": str(cfg)}):
            reload_settings()
            from logic_loom_api.main import api

            r = TestClient(api).post(
                "/api/app/config/first-run",
                json={"vault_path": str(tmp_path / "vault"), "akms_repo": "", "zsum_repo": ""},
            )
        reload_settings()
        assert r.status_code == 200, r.text
        import tomllib

        data = tomllib.loads(cfg.read_text())
        assert data == {"vault_path": str(tmp_path / "vault"), "search_backend": "ripgrep"}

    def test_detect_paths_is_optional_convenience(self, tmp_path: Path, monkeypatch) -> None:
        """With no ~/Github/Personal siblings every detection is None."""
        monkeypatch.setenv("HOME", str(tmp_path))
        assert detect_paths() == {
            "akms_repo": None,
            "akms_global_vault": None,
            "zsum_repo": None,
            "vault_path": None,
        }


# ── HTTP readers ─────────────────────────────────────────────────────────────


@pytest.fixture
def no_checkout_client(tmp_path: Path):
    """The real app with both checkouts pointing at paths that do not exist."""
    env = {
        "LOGIC_LOOM_CONFIG": str(tmp_path / "config.toml"),
        "LOGIC_LOOM_VAULT_PATH": str(tmp_path / "vault"),
        "LOGIC_LOOM_AKMS_REPO": str(tmp_path / "no-akms"),
        "LOGIC_LOOM_ZSUM_REPO": str(tmp_path / "no-zsum"),
        "LOGIC_LOOM_AKMS_WORKSPACE": str(tmp_path / "ws"),
    }
    with patch.dict(os.environ, env):
        reload_settings()
        from logic_loom_api.main import api

        yield TestClient(api)
    reload_settings()


class TestAkmsStatusWithoutCheckout:
    def test_akms_available_without_repo(self, no_checkout_client: TestClient) -> None:
        pytest.importorskip("akms_nodes_gen")
        body = no_checkout_client.get("/api/app/akms-status").json()
        assert body["repo_exists"] is False
        assert body["available"] is True


class TestDevScriptRoutesWithoutCheckout:
    """Routes that run code from an AKMS checkout keep their clean 4xx."""

    def test_nlm_routes_answer_412(self, no_checkout_client: TestClient) -> None:
        r = no_checkout_client.post("/api/nlm/generate", json={"batch_id": "R1_B1"})
        assert r.status_code == 412
        assert "akms_repo" in r.text

    def test_promote_stream_answers_404(self, no_checkout_client: TestClient, tmp_path: Path) -> None:
        r = no_checkout_client.get(
            "/api/nodes/promote/stream", params={"source": str(tmp_path)}
        )
        assert r.status_code == 404
        assert "akms_repo" in r.text

    def test_claude_asset_import_needs_source_dir(self, no_checkout_client: TestClient) -> None:
        r = no_checkout_client.post(
            "/api/prompts/import-akms-claude-assets", json={"mode": "preview"}
        )
        assert r.status_code == 400
        assert "source_dir is required" in r.text

    def test_zsum_prompt_import_needs_source_dir(self, no_checkout_client: TestClient) -> None:
        r = no_checkout_client.post(
            "/api/prompts/import-zsum-prompts", json={"mode": "preview"}
        )
        assert r.status_code == 400
        assert "source_dir is required" in r.text

    def test_workspace_import_needs_source(self, no_checkout_client: TestClient) -> None:
        r = no_checkout_client.post(
            "/api/workspace/import-akms-repo", json={"mode": "preview"}
        )
        assert r.status_code == 400


class TestZsumWithoutCheckout:
    def test_cwd_falls_back_to_config_dir(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.zsum import _resolve_cache_dir, _zsum_cwd

        cfg = tmp_path / "cfg" / "zsum.config.yaml"
        cfg.parent.mkdir()
        cfg.write_text("{}\n")
        s = _load(tmp_path, zsum_repo=tmp_path / "no-zsum", zsum_config_path=cfg)
        assert _zsum_cwd(s, cfg) == cfg.parent
        assert _resolve_cache_dir(s) == cfg.parent / "cache"

    def test_present_checkout_stays_the_cwd(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.zsum import _zsum_cwd

        (tmp_path / "zsum").mkdir()
        cfg = tmp_path / "cfg" / "zsum.config.yaml"
        s = _load(tmp_path, zsum_repo=tmp_path / "zsum", zsum_config_path=cfg)
        assert _zsum_cwd(s, cfg) == tmp_path / "zsum"

    def test_run_submits_from_config_dir(self, tmp_path: Path) -> None:
        """POST /api/zsum/runs no longer needs a zsum checkout: the run is
        launched from the config's directory."""
        cfg = tmp_path / "cfg" / "zsum.config.yaml"
        cfg.parent.mkdir()
        cfg.write_text("{}\n")
        env = {
            "LOGIC_LOOM_CONFIG": str(tmp_path / "config.toml"),
            "LOGIC_LOOM_ZSUM_REPO": str(tmp_path / "no-zsum"),
            "LOGIC_LOOM_ZSUM_CONFIG_PATH": str(cfg),
        }
        fake_registry = AsyncMock()
        fake_registry.submit.return_value.model_dump = lambda: {"id": "r1"}
        with patch.dict(os.environ, env):
            reload_settings()
            from logic_loom_api.main import api

            with patch("logic_loom_api.routes.zsum.get_registry", return_value=fake_registry):
                r = TestClient(api).post("/api/zsum/runs", json={"stage": "stats"})
        reload_settings()
        assert r.status_code == 200, r.text
        assert fake_registry.submit.await_args.kwargs["cwd_override"] == cfg.parent


class TestLearnProtectedRoots:
    def test_configured_missing_repo_stays_protected(self, tmp_path: Path) -> None:
        from logic_loom_api.adapters.learn_adapter import _protected_roots

        s = _load(tmp_path, akms_repo=tmp_path / "no-akms")
        assert (tmp_path / "no-akms").resolve() in _protected_roots(s)


# ── Unset (None) — the default after the field flip ─────────────────────────


@pytest.fixture
def unset_checkouts(monkeypatch):
    """Remove every checkout variable, including the suite's pinned
    LOGIC_LOOM_AKMS_REPO, so the fields take their real default."""
    for var in ("LOGIC_LOOM_AKMS_REPO", "LOGIC_LOOM_ZSUM_REPO", "LOGIC_LOOM_LOGIC_LOOM_ROOT"):
        monkeypatch.delenv(var, raising=False)


class TestUnsetDefaults:
    def test_fields_default_to_none(self, tmp_path: Path, unset_checkouts) -> None:
        s = _load(tmp_path)
        assert s.akms_repo is None
        assert s.zsum_repo is None
        assert s.logic_loom_root is None
        assert s.akms_repo_dir() is None and s.zsum_repo_dir() is None

    def test_blank_env_means_unset_not_cwd(self, tmp_path: Path, unset_checkouts, monkeypatch) -> None:
        monkeypatch.setenv("LOGIC_LOOM_AKMS_REPO", "")
        monkeypatch.setenv("LOGIC_LOOM_ZSUM_REPO", "  ")
        s = _load(tmp_path)
        assert s.akms_repo is None
        assert s.zsum_repo is None

    def test_env_still_overrides_toml(self, tmp_path: Path, unset_checkouts, monkeypatch) -> None:
        """Precedence is unchanged: kwargs > env > TOML > default (None)."""
        cfg = tmp_path / "config.toml"
        cfg.write_text(f"akms_repo = '{tmp_path / 'from-toml'}'\n")
        assert Settings.load(config_path=cfg).akms_repo == tmp_path / "from-toml"
        monkeypatch.setenv("LOGIC_LOOM_AKMS_REPO", str(tmp_path / "from-env"))
        assert Settings.load(config_path=cfg).akms_repo == tmp_path / "from-env"
        assert (
            Settings.load(config_path=cfg, akms_repo=tmp_path / "kw").akms_repo
            == tmp_path / "kw"
        )

    def test_global_vault_and_domain_packs(self, tmp_path: Path, unset_checkouts, monkeypatch) -> None:
        monkeypatch.delenv("AKMS_GLOBAL_VAULT", raising=False)
        s = _load(tmp_path)
        assert s.effective_akms_global_vault() == Path("~/.claude/akms/nodes").expanduser()
        assert s.learn_domain_packs_search_root() is None


class TestZsumConfigPath:
    def test_falls_back_to_workspace_runtime(self, tmp_path: Path, unset_checkouts) -> None:
        s = _load(tmp_path, akms_workspace=tmp_path / "ws")
        assert s.effective_zsum_config_path() == (
            tmp_path / "ws" / "zsum-runtime" / "zsum.config.yaml"
        )

    def test_missing_checkout_also_falls_back(self, tmp_path: Path) -> None:
        s = _load(tmp_path, zsum_repo=tmp_path / "no-zsum", akms_workspace=tmp_path / "ws")
        assert s.effective_zsum_config_path() == (
            tmp_path / "ws" / "zsum-runtime" / "zsum.config.yaml"
        )

    def test_present_checkout_then_explicit_win(self, tmp_path: Path) -> None:
        (tmp_path / "zsum").mkdir()
        s = _load(tmp_path, zsum_repo=tmp_path / "zsum")
        assert s.effective_zsum_config_path() == tmp_path / "zsum" / "zsum.config.yaml"
        explicit = tmp_path / "mine.yaml"
        s = _load(tmp_path, zsum_repo=tmp_path / "zsum", zsum_config_path=explicit)
        assert s.effective_zsum_config_path() == explicit


@pytest.fixture
def unset_client(tmp_path: Path, unset_checkouts):
    env = {
        "LOGIC_LOOM_CONFIG": str(tmp_path / "config.toml"),
        "LOGIC_LOOM_VAULT_PATH": str(tmp_path / "vault"),
        "LOGIC_LOOM_AKMS_WORKSPACE": str(tmp_path / "ws"),
        "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
    }
    with patch.dict(os.environ, env):
        reload_settings()
        from logic_loom_api.main import api

        yield TestClient(api)
    reload_settings()


class TestHttpWithUnsetCheckouts:
    def test_config_reports_null(self, unset_client: TestClient, tmp_path: Path) -> None:
        body = unset_client.get("/api/app/config").json()
        assert body["akms_repo"] is None
        assert body["zsum_repo"] is None
        assert body["logic_loom_root"] is None
        assert body["zsum_config_path_effective"] == str(
            tmp_path / "ws" / "zsum-runtime" / "zsum.config.yaml"
        )

    def test_status_routes(self, unset_client: TestClient) -> None:
        assert unset_client.get("/api/zsum/status").json()["zsum_repo"] is None
        akms = unset_client.get("/api/app/akms-status").json()
        assert akms["akms_repo"] is None and akms["repo_exists"] is False
        graph = unset_client.get("/api/graph/status").json()
        assert graph["akms_repo"] is None and graph["repo_graph_path"] is None

    def test_dev_script_routes_keep_4xx(self, unset_client: TestClient, tmp_path: Path) -> None:
        r = unset_client.post("/api/nlm/validate", json={"batch_id": "R1_B1"})
        assert r.status_code == 412 and "(unset)" in r.text
        r = unset_client.get("/api/nodes/promote/stream", params={"source": str(tmp_path)})
        assert r.status_code == 404
        r = unset_client.post("/api/workspace/import-akms-repo", json={"mode": "preview"})
        assert r.status_code == 400 and "source_repo is required" in r.text

    def test_learn_protected_roots_skip_unset_repo(self, tmp_path: Path, unset_checkouts) -> None:
        from logic_loom_api.adapters.learn_adapter import _protected_roots

        s = _load(tmp_path, akms_global_vault=tmp_path / "gv")
        assert _protected_roots(s) == [(tmp_path / "gv").resolve()]


class TestPickerWithUnsetCheckout:
    def test_picker_mounts_with_akms_repo_unset(self, tmp_path: Path, unset_checkouts) -> None:
        pytest.importorskip("akms_nodes_gen")
        from logic_loom_api.adapters.picker_adapter import build_picker_app
        from logic_loom_api.batches.store import BatchStore

        ws = tmp_path / "ws"
        BatchStore().save(ws)
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "zsumbib.json").write_text('{"collections": {}, "items": []}')
        s = _load(tmp_path, akms_workspace=ws, vault_path=vault)
        assert s.akms_repo is None
        r = TestClient(build_picker_app(s)).get("/api/batches")
        assert r.status_code == 200
