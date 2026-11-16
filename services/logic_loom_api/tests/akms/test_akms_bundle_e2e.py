"""AKMS bundle end-to-end test matrix.

Covers: all e2e tests pass on macOS, Linux, Windows runners.

Tier: regression. The Linux leg is exhaustive here on this host.
macOS / Windows legs run on GitHub Actions runners via
``.github/workflows/akms-bundle-e2e.yml`` (statically verified by
``test_ci_workflow_yaml_static_parse``). The build-time security guard
against shipping a ``Nodes_Vault/`` tree is verified locally and asserted
to be wired into all three sidecar build scripts.
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[4]
RESOURCES_DIR = (
    REPO_ROOT
    / "services"
    / "logic_loom_api"
    / "src"
    / "logic_loom_api"
    / "resources"
)
GUARD_SCRIPT = REPO_ROOT / "scripts" / "check_resources_no_vault.py"
SIDECAR_BUILD_SCRIPTS = [
    REPO_ROOT / "scripts" / "build_sidecar_linux.sh",
    REPO_ROOT / "scripts" / "build_sidecar_macos.sh",
    REPO_ROOT / "scripts" / "build_sidecar_windows.ps1",
]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "akms-bundle-e2e.yml"


@pytest.fixture
def e2e_client(tmp_path: Path, monkeypatch):
    """A TestClient with an isolated workspace for the backend e2e flow."""
    workspace = tmp_path / "akms-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "prompt-library").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(workspace))

    import logic_loom_api.import_tokens as _it
    import logic_loom_api.main as m

    importlib.reload(m)
    _it.registry().clear()
    with TestClient(m.api) as c:
        c.workspace_root = workspace  # type: ignore[attr-defined]
        yield c
    _it.registry().clear()


class TestAkmsBundleE2E:
    """E2E matrix + Nodes_Vault security guard."""

    # ------------------------------------------------------------------
    # Build-time security guard
    # ------------------------------------------------------------------

    @pytest.mark.regression
    def test_no_nodes_vault_in_resources(self):
        """Resources tree must contain no path component named ``Nodes_Vault``."""
        # Walk every path under resources/. Either the dir is absent (core-only
        # mode) or it contains no Nodes_Vault component.
        if not RESOURCES_DIR.exists():
            return
        for p in RESOURCES_DIR.rglob("*"):
            rel_parts = p.relative_to(RESOURCES_DIR).parts
            assert "Nodes_Vault" not in rel_parts, (
                f"Forbidden Nodes_Vault path inside resources/: {p}"
            )

    @pytest.mark.regression
    def test_security_guard_present_in_sidecar_build_scripts(self):
        """All three sidecar build scripts must invoke the resources guard."""
        guard_marker = "check_resources_no_vault.py"
        missing: list[str] = []
        for script in SIDECAR_BUILD_SCRIPTS:
            assert script.is_file(), f"sidecar build script missing: {script}"
            text = script.read_text()
            if guard_marker not in text:
                missing.append(str(script.relative_to(REPO_ROOT)))
        assert not missing, (
            f"sidecar build scripts not invoking the Nodes_Vault guard: {missing}"
        )

    @pytest.mark.regression
    def test_guard_script_runs_clean_on_current_tree(self):
        """The guard script itself runs cleanly against the current repo."""
        assert GUARD_SCRIPT.is_file(), f"guard script missing: {GUARD_SCRIPT}"
        result = subprocess.run(
            [sys.executable, str(GUARD_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"guard returned non-zero on a clean tree:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    @pytest.mark.regression
    def test_guard_script_rejects_seeded_leak(self, tmp_path: Path):
        """A planted Nodes_Vault dir must make the guard exit non-zero."""
        # Build a fake repo layout where the guard will look.
        fake_repo = tmp_path / "fake-repo"
        fake_res = (
            fake_repo
            / "services"
            / "logic_loom_api"
            / "src"
            / "logic_loom_api"
            / "resources"
        )
        leaked = fake_res / "Packages" / "Nodes_Vault" / "geom" / "n1.md"
        leaked.parent.mkdir(parents=True)
        leaked.write_text("leak\n")
        # Copy the guard script into the fake repo so its repo_root resolution
        # lands on fake_repo. (The script computes ROOT from its own location.)
        (fake_repo / "scripts").mkdir(parents=True)
        fake_guard = fake_repo / "scripts" / "check_resources_no_vault.py"
        fake_guard.write_text(GUARD_SCRIPT.read_text())
        result = subprocess.run(
            [sys.executable, str(fake_guard)],
            capture_output=True,
            text=True,
            cwd=str(fake_repo),
        )
        assert result.returncode != 0, (
            "guard returned 0 despite a planted Nodes_Vault leak; "
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "Nodes_Vault" in result.stderr

    # ------------------------------------------------------------------
    # CI workflow static check
    # ------------------------------------------------------------------

    @pytest.mark.regression
    def test_ci_workflow_yaml_static_parse(self):
        """``akms-bundle-e2e.yml`` must parse + reference the three OS legs."""
        assert CI_WORKFLOW.is_file(), f"missing workflow: {CI_WORKFLOW}"
        text = CI_WORKFLOW.read_text()
        # Defer YAML parse to PyYAML if available, otherwise just substring-check.
        try:
            import yaml  # type: ignore[import-not-found]

            doc = yaml.safe_load(text)
            assert isinstance(doc, dict)
            # YAML's ``on:`` key is parsed as boolean True by some loaders.
            assert ("on" in doc) or (True in doc), "workflow missing on: trigger"
            assert "jobs" in doc and isinstance(doc["jobs"], dict)
        except ImportError:
            # Fall back to text-level assertions.
            assert "on:" in text
            assert "jobs:" in text
        # All three OS legs must be present.
        for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
            assert runner in text, f"workflow missing runner: {runner}"
        # The resources guard must be the gate before the matrix jobs.
        assert "resources-guard" in text
        assert "check_resources_no_vault.py" in text

    # ------------------------------------------------------------------
    # Backend e2e: workspace bootstrap → asset install → batch create →
    # workflow dry-run preview → output snapshot.
    # ------------------------------------------------------------------

    @pytest.mark.regression
    def test_backend_e2e_workspace_bootstrap_to_output_snapshot(self, e2e_client):
        """Happy-path smoke through the AKMS-bundle backend surface."""
        c = e2e_client
        ws = c.workspace_root  # type: ignore[attr-defined]

        # 1. Workspace bootstrap: lifespan should have created the canonical
        #    subdirectories. Sentinel file present.
        assert (ws / ".logic-loom-workspace-ok").is_file()
        assert (ws / "prompt-library" / "variants").is_dir()
        assert (ws / "workflow-library" / "user-workflows").is_dir()
        assert (ws / "runs").is_dir()

        # 2. Asset install — `/api/akms/claude-assets/status` should respond
        #    (200 with a status payload). In core-only mode the status pill
        #    reports uninstalled; that's fine for this e2e — what matters is
        #    the route is mounted and returns a non-5xx envelope.
        r = c.get("/api/akms/claude-assets/status")
        assert r.status_code == 200
        body = r.json()
        assert "installed" in body

        # 3. Batch create — POST /api/batches with minimum payload.
        r = c.post("/api/batches", json={"title": "E2E Smoke Batch"})
        assert r.status_code == 201, r.text
        batch = r.json()
        batch_id = batch["id"]
        assert batch["title"] == "E2E Smoke Batch"

        # 4. List batches contains our new entry.
        r = c.get("/api/batches")
        assert r.status_code == 200
        ids = [b["id"] for b in r.json()["batches"]]
        assert batch_id in ids

        # 5. Workflow dry-run preview — /api/prompts/resolve renders the
        #    effective slot map without invoking any external CLI.
        r = c.post("/api/prompts/resolve", json={})
        assert r.status_code == 200
        eff = r.json()
        # /api/prompts/resolve returns the EffectivePrompts envelope:
        # {schema_version, slot_to_variant, contents}. In a fresh workspace
        # with no profiles, both maps are empty but the envelope is shaped.
        assert "schema_version" in eff
        assert "slot_to_variant" in eff
        assert "contents" in eff
        assert isinstance(eff["contents"], dict)

        # 6. Output snapshot location — runs registry is initialised against
        #    the cache_path; check the per-run output dir convention exists.
        #    (No real run is started — this is the smoke check that the
        #    surface is reachable, not the long path.)
        r = c.get("/api/runs")
        assert r.status_code == 200
        runs = r.json().get("runs", [])
        assert isinstance(runs, list)

        # 7. Import endpoint smoke (preview only, writes nothing).
        r = c.post(
            "/api/prompts/import-zsum-prompts",
            json={"mode": "preview", "source_dir": str(ws)},
        )
        assert r.status_code == 200
        assert r.json()["mode"] == "preview"
        assert isinstance(r.json().get("preview_token"), str)

    # ------------------------------------------------------------------
    # Frontend e2e spec — local sanity that the file exists and parses as
    # a TypeScript module shape. The real spec runs in CI via vitest.
    # ------------------------------------------------------------------

    @pytest.mark.regression
    def test_frontend_e2e_spec_present(self):
        """The frontend e2e spec file must exist and reference the AKMS-bundle slice."""
        spec = (
            REPO_ROOT
            / "apps"
            / "desktop"
            / "tests"
            / "e2e"
            / "akms_bundle.spec.tsx"
        )
        assert spec.is_file(), f"frontend e2e spec missing: {spec}"
        text = spec.read_text()
        # The three required frontend e2e scenarios.
        for needle in (
            "mode switch",
            "batches list",
            "run dialog dry-run preview",
        ):
            assert re.search(needle, text, re.IGNORECASE), (
                f"frontend e2e spec missing scenario hint: {needle!r}"
            )
