"""Tests for the MechDSL executable-bridge plugin routes.

The plugin's optional dependencies (`compmech_reference_pack`, `mechdsl`,
`akms_learn`) are not installed in the core test env, so the availability state
is forced deterministically via ``sys.modules`` injection rather than relying on
what happens to be installed:

  - mapping a module name to ``None`` makes ``import <name>`` raise ImportError
    (the plugin-absent path),
  - injecting a stub ``ModuleType`` makes the import succeed (the present path).

This keeps the suite stable whether or not the packages are vendored/installed.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from logic_loom_api.deps import get_settings, reload_settings
from logic_loom_api.main import api
from logic_loom_api.routes.mechdsl import (
    _build_artifact_id,
    _mechdsl_cache_root,
    _safe_artifact_dir,
    _write_artifact,
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Settings pointed at tmp_path; cache_path too so artifacts stay sandboxed."""
    cfg_path = tmp_path / "config.toml"
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg_path),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
        },
    ):
        reload_settings()
        yield TestClient(api)
        reload_settings()


def _absent(*names: str) -> dict[str, None]:
    """sys.modules overrides that make ``import name`` raise ImportError."""
    return {name: None for name in names}


def _present_plugin(capabilities: dict) -> dict[str, types.ModuleType]:
    """Stub modules so status' import probes succeed and capabilities() returns."""
    compmech = types.ModuleType("compmech_reference_pack")
    mechdsl = types.ModuleType("mechdsl")
    integration = types.ModuleType("mechdsl.integration")
    integration.capabilities = lambda: capabilities  # type: ignore[attr-defined]
    mechdsl.integration = integration  # type: ignore[attr-defined]
    return {
        "compmech_reference_pack": compmech,
        "mechdsl": mechdsl,
        "mechdsl.integration": integration,
    }


# A build_executable-shaped result (the 8-key emit-only contract).
_FAKE_RESULT = {
    "adapter": "compmech.mechdsl_runner",
    "status": "ok",
    "emitted_source": "import taichi as ti  # emitted\n",
    "transpiled": {"valid_python": True, "entry_point": "radial_return_j2"},
    "normalized_input": "\\begin{algorithmic}...\\end{algorithmic}",
    "provenance": {
        "compmech.node.radial_return_j2": {"sections": ["x"], "references": ["Simo & Hughes"]}
    },
    "vv_plan": {"benchmark": "necking_bar", "citation": "Simo & Hughes (1998)"},
    "warnings": [],
}


class TestMechdslStatus:
    def test_unavailable_when_plugin_absent(self, client: TestClient) -> None:
        with patch.dict(sys.modules, _absent("compmech_reference_pack", "mechdsl")):
            r = client.get("/api/mechdsl/status")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert body["package_installed"] is False
        assert body["capabilities"] is None
        assert "mechdsl' extra" in body["reason"]

    def test_available_reports_capabilities_when_present(self, client: TestClient) -> None:
        caps = {
            "version": "0.1.0",
            "backends": ["taichi"],
            "actions": ["emit", "transpile", "verify"],
        }
        with patch.dict(sys.modules, _present_plugin(caps)):
            r = client.get("/api/mechdsl/status")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert body["package_installed"] is True
        assert body["mechdsl_installed"] is True
        assert body["capabilities"] == caps
        assert body["reason"] is None


class TestMechdslCompile:
    def test_requires_node_or_slice(self, client: TestClient) -> None:
        r = client.post("/api/mechdsl/compile", json={})
        assert r.status_code == 400
        assert "node_id or graph_slice" in r.json()["error"]["message"]

    def test_503_when_plugin_absent(self, client: TestClient) -> None:
        # akms_learn is imported first in the compile seam; absence -> 503.
        with patch.dict(sys.modules, _absent("akms_learn", "compmech_reference_pack")):
            r = client.post("/api/mechdsl/compile", json={"node_id": "n1"})
        assert r.status_code == 503
        assert "unavailable" in r.json()["error"]["message"].lower()


class TestMechdslArtifacts:
    def test_unknown_artifact_404(self, client: TestClient) -> None:
        r = client.get("/api/mechdsl/artifacts/deadbeef")
        assert r.status_code == 404

    def test_artifact_roundtrip(self, client: TestClient) -> None:
        # Write a cached artifact through the same helper the route uses, then
        # serve it back. Exercises the cache-write + traversal-guarded read.
        cache_root = _mechdsl_cache_root(get_settings())
        artifact_id = _write_artifact(cache_root, _FAKE_RESULT)

        # Cached under <cache_path>/mechdsl/<id>/ — never outside the cache root.
        art_dir = cache_root / artifact_id
        assert (art_dir / "result.json").is_file()
        assert (art_dir / "emitted_source.py").read_text() == _FAKE_RESULT["emitted_source"]
        assert art_dir.resolve().is_relative_to(cache_root.resolve())

        r = client.get(f"/api/mechdsl/artifacts/{artifact_id}")
        assert r.status_code == 200
        assert r.json()["adapter"] == "compmech.mechdsl_runner"
        assert r.json()["vv_plan"]["citation"].startswith("Simo & Hughes")


class TestArtifactHelpers:
    def test_build_artifact_id_is_deterministic_and_content_addressed(self) -> None:
        id1 = _build_artifact_id(_FAKE_RESULT)
        id2 = _build_artifact_id(dict(_FAKE_RESULT))
        assert id1 == id2 and len(id1) == 16
        mutated = {**_FAKE_RESULT, "emitted_source": "different\n"}
        assert _build_artifact_id(mutated) != id1
        # warnings are not part of the content hash (advisory only).
        noisy = {**_FAKE_RESULT, "warnings": ["a", "b"]}
        assert _build_artifact_id(noisy) == id1

    def test_safe_artifact_dir_blocks_traversal(self, tmp_path: Path) -> None:
        from fastapi import HTTPException

        cache_root = tmp_path / "cache" / "mechdsl"
        cache_root.mkdir(parents=True)
        with pytest.raises(HTTPException) as exc:
            _safe_artifact_dir(cache_root, "../../etc/passwd")
        assert exc.value.status_code == 403

    def test_write_artifact_handles_missing_emitted_source(self, tmp_path: Path) -> None:
        cache_root = tmp_path / "mechdsl"
        result = {**_FAKE_RESULT, "emitted_source": None}
        artifact_id = _write_artifact(cache_root, result)
        art_dir = cache_root / artifact_id
        assert (art_dir / "result.json").is_file()
        assert not (art_dir / "emitted_source.py").exists()
