"""Consolidated Learn backend suite.

This file consolidates the 6 core backend test cases for the Learn integration:

1. missing plugin -> unavailable  (status payload exact shape, no metadata leakage)
2. plugin -> capabilities          (available payload, all required status keys)
3. compile rejects missing graph cache cleanly (structured 412, never 500)
4. compile writes to learning cache (<cache_path>/learning/{artifact_id}/)
5. artifact endpoint reads generated bundle metadata + rejects path traversal
6. no graph-route regression        (graph routes unaffected when learn plugin absent)

Also pins the compile-response CONTRACT shape:
  warnings: list[{severity, code, message, source_ref}]
  provenance_summary: {request_hash, graph_hash, topic, generation_option,
                       node_count, edge_count}
  cache_dir: str | None, packet_path: str | None, exports: list[{path, type}]

Patterns reused from the per-feature Learn tests:
  - Plugin-absence idiom: monkeypatch ``learn_adapter.akms_learn_available``
    (NOT a real uninstall — the akms-learn extra IS installed in dev venv).
    See: tests/learn/test_learn_status_payload.py
  - tmp-cache TestClient fixture: patch.dict env → reload_settings → TestClient.
    See: tests/learn/test_learn_compile_contract.py, test_learn_cache_policy.py
  - Artifact-dir assertions: cache_path / "learning" / artifact_id.
    See: tests/learn/test_learn_cache_policy.py
  - Graph status endpoint as non-regression probe:
    See: tests/test_graph.py::TestGraphStatus
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.adapters import learn_adapter

# ---------------------------------------------------------------------------
# Module-level availability check
# ---------------------------------------------------------------------------

HAS_AKMS_LEARN = importlib.util.find_spec("akms_learn") is not None


# ---------------------------------------------------------------------------
# Shared fixtures (mirror the compile-contract / cache-policy test idioms exactly)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    """TestClient with tmp-path settings so no real filesystem state is touched.

    Mirrors tests/learn/test_learn_compile_contract.py::client and
    test_learn_cache_policy.py::client.
    """
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
    """Minimal dict matching akms_learn.fixture_graph() serialised to GraphSlicePayload shape."""
    from akms_learn import fixture_graph  # type: ignore[import-not-found]

    gs = fixture_graph()
    return {
        "nodes": list(gs.nodes),
        "edges": list(gs.edges),
        "metadata": dict(gs.metadata),
    }


def _base_compile_body(**overrides) -> dict:
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
# Case 1 — missing plugin → unavailable
# ---------------------------------------------------------------------------


class TestMissingPluginUnavailable:
    """Case 1: missing plugin -> {akms_learn_available: false, reason, capabilities: []}."""

    @pytest.mark.unit
    def test_missing_plugin_returns_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: when akms_learn is absent the status payload has exactly 3 keys.

        Plugin-absence idiom (test_learn_status_payload.py): monkeypatch ``akms_learn_available``
        on the adapter module — no real uninstall, no import-state leakage.

        Exact unavailable shape: {akms_learn_available: false, reason: str, capabilities: []}.
        Key set must be EXACTLY 3 — null metadata fields (version, plugin_api, …)
        MUST NOT appear on the unavailable path.
        """
        monkeypatch.setattr(learn_adapter, "akms_learn_available", lambda: False)

        status = learn_adapter.build_status()
        payload = status.to_payload()

        assert set(payload.keys()) == {
            "akms_learn_available",
            "reason",
            "capabilities",
        }, (
            f"Unavailable payload must have EXACTLY 3 keys, "
            f"got: {set(payload.keys())}"
        )
        assert payload["akms_learn_available"] is False
        assert isinstance(payload["reason"], str) and payload["reason"], (
            "reason must be a non-empty string on the unavailable path"
        )
        assert payload["capabilities"] == [], (
            "capabilities must be [] on the unavailable path"
        )


# ---------------------------------------------------------------------------
# Case 2 — plugin present → capabilities
# ---------------------------------------------------------------------------


class TestPluginAvailableCapabilities:
    """Case 2: plugin present -> full available status payload."""

    @pytest.mark.unit
    def test_plugin_available_reports_capabilities(self) -> None:
        """Verifies: when akms_learn IS installed, status has all required keys.

        Reuses the assertion logic from test_learn_status_payload.py::test_available_payload_required_keys_present.
        Skipped when akms_learn is not installed — this test proves the happy path.
        """
        if not HAS_AKMS_LEARN:
            pytest.skip("akms_learn not installed — cannot test available path")

        from akms_learn.plugin import get_plugin  # type: ignore[import-not-found]

        status = learn_adapter.build_status()
        payload = status.to_payload()

        assert payload["akms_learn_available"] is True, (
            "akms_learn must be available in the test environment for this test"
        )

        required_keys = {
            "akms_learn_available",
            "version",
            "plugin_api",
            "learning_packet_schema",
            "supported_akms_schema_min",
            "supported_akms_schema_max",
            "capabilities",
        }
        missing = required_keys - set(payload.keys())
        assert not missing, f"Available payload missing required keys: {missing}"

        # reason must be ABSENT on the available path
        assert "reason" not in payload, (
            "reason must be absent from the available payload"
        )

        assert isinstance(payload["version"], str) and payload["version"], (
            "version must be a non-empty string"
        )
        assert payload["plugin_api"] == "akms-learn-plugin/v1", (
            f"plugin_api mismatch: {payload['plugin_api']!r}"
        )
        assert payload["learning_packet_schema"] == "learn/v1", (
            f"learning_packet_schema mismatch: {payload['learning_packet_schema']!r}"
        )
        assert payload["supported_akms_schema_min"] == "v2", (
            f"supported_akms_schema_min mismatch: {payload['supported_akms_schema_min']!r}"
        )
        assert payload["supported_akms_schema_max"] == "v2", (
            f"supported_akms_schema_max mismatch: {payload['supported_akms_schema_max']!r}"
        )

        expected_caps = list(get_plugin().capabilities())
        assert isinstance(payload["capabilities"], list) and payload["capabilities"], (
            "capabilities must be a non-empty list on the available path"
        )
        assert payload["capabilities"] == expected_caps, (
            f"capabilities must match plugin.capabilities() verbatim: "
            f"{payload['capabilities']!r} != {expected_caps!r}"
        )


# ---------------------------------------------------------------------------
# Case 3 — compile rejects missing graph cache with structured 412
# ---------------------------------------------------------------------------


class TestCompileMissingGraphCache:
    """Case 3: compile rejects missing graph cache with structured 412 (never 500)."""

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_compile_rejects_missing_graph_cache_cleanly(
        self, client: TestClient
    ) -> None:
        """Verifies: POST /api/learn/compile with node/depth and missing graph cache
        returns 412 precondition error, never 500.

        Reuses the patching idiom from test_learn_compile_contract.py::test_missing_graph_cache_returns_structured_412:
        monkeypatches ``load_cached_graph`` in the learn route module to raise
        ``GraphAdapterError``, simulating a missing graph cache without needing
        a full AKMS repo.

        Error envelope: {code: 'graph_unavailable',
                                     detail.graph_available: false}.
        """
        from logic_loom_api.adapters.graph_adapter import (  # type: ignore[import-not-found]
            GraphAdapterError,
        )
        from logic_loom_api.routes import learn as learn_route_module  # type: ignore[import-not-found]

        body = {
            "generation_option": "deterministic_outline",
            "topic": "test topic",
            "goal": "test goal",
            "node": "some-node",
        }

        with patch.object(
            learn_route_module,
            "load_cached_graph",
            side_effect=GraphAdapterError("Graph cache missing: /some/path/graph.json"),
        ):
            r = client.post("/api/learn/compile", json=body)

        assert r.status_code == 412, (
            f"Expected 412 (graph_unavailable), got {r.status_code}: {r.text}"
        )
        data = r.json()
        assert "error" in data, f"Response must carry 'error' envelope, got: {data}"
        assert data["error"]["code"] == "graph_unavailable", (
            f"error.code must be 'graph_unavailable', got: {data['error']['code']!r}"
        )
        assert data["error"]["detail"]["graph_available"] is False, (
            "error.detail.graph_available must be False on the missing-cache path"
        )


# ---------------------------------------------------------------------------
# Case 4 — compile writes to learning cache
# ---------------------------------------------------------------------------


class TestCompileWritesCache:
    """Case 4: compile writes artifacts under <cache_path>/learning/{artifact_id}/."""

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_compile_writes_to_learning_cache(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Verifies: after compile, artifacts persist under the learning cache.

        Reuses the filesystem assertions from
        test_learn_cache_policy.py::test_compile_writes_under_learning_artifact_id.

        Checks:
        - <tmp_path>/cache/learning/{artifact_id}/ exists on disk
        - cache_dir in response == that dir
        - packet_path is populated and is a file inside the dir
        - exports is a non-empty list; every exported file exists on disk
        - At least one export is tagged 'packet'
        """
        body = _base_compile_body()
        r = client.post("/api/learn/compile", json=body)
        assert r.status_code == 200, (
            f"Expected 200, got {r.status_code}: {r.text}"
        )

        data = r.json()
        artifact_id = data["artifact_id"]
        assert isinstance(artifact_id, str) and artifact_id, (
            "artifact_id must be a non-empty string"
        )

        expected_dir = tmp_path / "cache" / "learning" / artifact_id
        assert expected_dir.is_dir(), (
            f"Artifact dir {expected_dir} must exist after compile"
        )
        assert data["cache_dir"] == str(expected_dir), (
            f"cache_dir {data['cache_dir']!r} must equal {expected_dir!r}"
        )

        # packet_path populated and resides inside the artifact dir
        assert data["packet_path"] is not None, (
            "packet_path must be populated after compile"
        )
        packet_path = Path(data["packet_path"])
        assert packet_path.is_file(), (
            f"packet file {packet_path} must exist on disk"
        )
        assert packet_path.parent == expected_dir, (
            "packet_path must live directly under the artifact dir"
        )

        # exports non-empty, all files on disk, canonical packet tagged 'packet'
        exports = data.get("exports", [])
        assert isinstance(exports, list) and len(exports) >= 1, (
            "exports must be a non-empty list after compile"
        )
        for export in exports:
            assert "path" in export and "type" in export, (
                f"each export must carry 'path' + 'type', got: {export!r}"
            )
            ep = Path(export["path"])
            assert ep.is_file(), f"exported artifact {ep} must exist on disk"
            assert ep.resolve().is_relative_to(expected_dir.resolve()), (
                f"export {ep} must live under the artifact dir"
            )
        assert any(e["type"] == "packet" for e in exports), (
            "exports must include the canonical packet (type='packet')"
        )

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_compile_response_contract_shape(
        self, client: TestClient
    ) -> None:
        """Pins the compile-response CONTRACT shape.

        A prior bug (commit 46d66d4) found that LearnCompileResponse.warnings and
        provenance_summary had drifted from the backend's actual dict shapes.
        This test asserts the exact field structure on the backend side so the
        contract can't silently drift again.

        Backend shape (routes/learn.py _serialize_warnings / _build_provenance_summary):
          warnings: list[{severity, code, message, source_ref}]
          provenance_summary: {request_hash, graph_hash, topic,
                               generation_option, node_count, edge_count}
          cache_dir: str (non-empty, ends with artifact_id)
          packet_path: str | None
          exports: list[{path, type}]
        """
        body = _base_compile_body()
        r = client.post("/api/learn/compile", json=body)
        assert r.status_code == 200, (
            f"Expected 200, got {r.status_code}: {r.text}"
        )
        data = r.json()

        # --- warnings shape ---
        warnings = data.get("warnings")
        assert isinstance(warnings, list), (
            "'warnings' must be a list"
        )
        for w in warnings:
            for key in ("severity", "code", "message", "source_ref"):
                assert key in w, (
                    f"Each warning dict must carry '{key}'; got keys: {set(w.keys())}"
                )

        # --- provenance_summary shape ---
        prov = data.get("provenance_summary")
        assert isinstance(prov, dict), (
            "'provenance_summary' must be a dict"
        )
        for key in (
            "request_hash",
            "graph_hash",
            "topic",
            "generation_option",
            "node_count",
            "edge_count",
        ):
            assert key in prov, (
                f"provenance_summary must carry '{key}'; got keys: {set(prov.keys())}"
            )

        # --- top-level envelope fields ---
        artifact_id = data["artifact_id"]
        assert isinstance(artifact_id, str) and artifact_id, (
            "artifact_id must be a non-empty string"
        )
        assert isinstance(data.get("cache_dir"), str) and data["cache_dir"], (
            "cache_dir must be a non-empty string"
        )
        assert data["cache_dir"].endswith(artifact_id), (
            f"cache_dir '{data['cache_dir']}' must end with artifact_id '{artifact_id}'"
        )
        # packet_path is str or None
        assert data.get("packet_path") is None or isinstance(data["packet_path"], str), (
            "packet_path must be str or None"
        )
        # exports: list of {path, type}
        assert isinstance(data.get("exports"), list), (
            "'exports' must be a list"
        )
        for e in data["exports"]:
            assert "path" in e and "type" in e, (
                f"each export must carry 'path' + 'type'; got: {set(e.keys())}"
            )


# ---------------------------------------------------------------------------
# Case 5 — artifact endpoint reads generated bundle metadata
# ---------------------------------------------------------------------------


class TestArtifactEndpoint:
    """Case 5: artifact endpoint serves files inside the dir, rejects path traversal."""

    @pytest.mark.integration
    def test_artifact_endpoint_reads_safely(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Verifies: GET /api/learn/artifacts/{artifact_id}/{path} serves a
        legitimate file and rejects ``..``-based path-traversal attempts.

        Reuses the fixture-writing + traversal-probe idiom from
        test_learn_cache_policy.py::test_artifact_endpoint_rejects_path_traversal.

        Does NOT require a full compile — writes a fixture file directly into
        the tmp cache so the test is deterministic even without akms_learn.
        """
        cache_root = tmp_path / "cache"

        # Plant a secret OUTSIDE the learning dir — traversal must never reach it.
        secret = cache_root / "secret.txt"
        cache_root.mkdir(parents=True, exist_ok=True)
        secret.write_text("TOP SECRET", encoding="utf-8")

        # Create a real artifact dir with a known file.
        artifact_id = "cafebabe12345678"
        artifact_dir = cache_root / "learning" / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "lesson.md").write_text("# lesson content", encoding="utf-8")

        # Legitimate read — must succeed with the exact file content.
        ok = client.get(f"/api/learn/artifacts/{artifact_id}/lesson.md")
        assert ok.status_code == 200, (
            f"Legitimate artifact read must return 200, got {ok.status_code}: {ok.text}"
        )
        assert ok.text == "# lesson content", (
            f"File content mismatch: {ok.text!r}"
        )

        # Path-traversal probes — must all be rejected (400 / 403 / 404).
        traversal_paths = [
            "../../secret.txt",
            "../secret.txt",
            "..%2f..%2fsecret.txt",
        ]
        for tp in traversal_paths:
            resp = client.get(f"/api/learn/artifacts/{artifact_id}/{tp}")
            assert resp.status_code in (400, 403, 404), (
                f"Traversal path {tp!r} must be rejected, got {resp.status_code}"
            )
            assert "TOP SECRET" not in resp.text, (
                f"Traversal {tp!r} leaked the secret file"
            )

        # artifact_id that is itself a traversal segment must be rejected.
        bad_id = client.get("/api/learn/artifacts/..%2f..%2fsecret.txt/lesson.md")
        assert bad_id.status_code in (400, 403, 404), (
            f"Traversal artifact_id must be rejected, got {bad_id.status_code}"
        )
        assert "TOP SECRET" not in bad_id.text

        # Missing file inside an existing artifact dir → 404.
        missing = client.get(f"/api/learn/artifacts/{artifact_id}/nope.md")
        assert missing.status_code == 404, (
            f"Missing artifact file must return 404, got {missing.status_code}"
        )

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_artifact_endpoint_reads_compiled_bundle(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Verifies: artifact endpoint serves a file from a REAL compile's output dir.

        Extends case 5 by exercising the full compile → artifact-read round-trip,
        proving the artifact_id in the compile response is a valid key for the
        artifact endpoint.
        """
        # Compile a real packet.
        body = _base_compile_body()
        r = client.post("/api/learn/compile", json=body)
        assert r.status_code == 200, f"Compile failed: {r.status_code}: {r.text}"
        data = r.json()

        artifact_id = data["artifact_id"]
        exports = data.get("exports", [])
        assert exports, "Need at least one export to probe the artifact endpoint"

        # Serve the first exported file via the artifact endpoint (use its basename
        # — the flat artifact layout places all exports directly under the artifact dir).
        first_export_path = exports[0]["path"]
        filename = Path(first_export_path).name

        resp = client.get(f"/api/learn/artifacts/{artifact_id}/{filename}")
        assert resp.status_code == 200, (
            f"Artifact endpoint must serve compiled export; "
            f"got {resp.status_code}: {resp.text[:200]}"
        )
        assert len(resp.content) > 0, (
            "Served artifact must be non-empty"
        )


# ---------------------------------------------------------------------------
# Case 6 — no graph-route regression
# ---------------------------------------------------------------------------


class TestGraphRouteNonRegression:
    """Case 6: graph routes work unchanged even with the learn plugin absent.

    Proves the degrade-gracefully invariant: the optional Learn subsystem
    MUST NOT disturb any graph route, whether the plugin is present or absent.

    Uses a minimal TestClient with no AKMS repo (graph cache does not exist),
    so /api/graph/status is the lightest representative probe — it always
    succeeds because it reports readiness, not data. This is a focused
    non-regression assertion, not a duplicate of test_graph.py.
    """

    @pytest.mark.integration
    def test_graph_status_unaffected_when_learn_plugin_absent(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: /api/graph/status responds correctly when akms_learn is absent.

        Simulates plugin absence via the same monkeypatch idiom as Case 1
        (patching ``akms_learn_available`` on learn_adapter) to confirm that
        graph routes are completely independent of the learn plugin state.
        """
        # Simulate learn plugin absence — must not affect graph routes.
        monkeypatch.setattr(learn_adapter, "akms_learn_available", lambda: False)

        r = client.get("/api/graph/status")
        assert r.status_code == 200, (
            f"Graph status must return 200 even when learn plugin is absent; "
            f"got {r.status_code}: {r.text}"
        )
        body = r.json()

        # Core shape assertions — mirrors test_graph.py::TestGraphStatus.
        assert "cache_exists" in body, "graph/status must carry 'cache_exists'"
        assert "node_count" in body, "graph/status must carry 'node_count'"
        assert "edge_count" in body, "graph/status must carry 'edge_count'"
        assert "graph_available" in body, "graph/status must carry 'graph_available'"

        # The learn plugin state must not bleed into the graph status values.
        assert body["cache_exists"] is False, (
            "cache_exists must be False for a fresh tmp_path client with no graph built"
        )
        assert body["node_count"] == 0, (
            "node_count must be 0 when no graph cache exists"
        )
        assert body["edge_count"] == 0, (
            "edge_count must be 0 when no graph cache exists"
        )

    @pytest.mark.integration
    def test_graph_status_unaffected_when_learn_plugin_present(
        self, client: TestClient
    ) -> None:
        """Verifies: /api/graph/status responds correctly when akms_learn IS present.

        Confirms the graph route works in both plugin states (present and absent)
        so the non-regression is symmetrical — a learn feature flag cannot
        accidentally degrade graph readiness in either direction.
        """
        r = client.get("/api/graph/status")
        assert r.status_code == 200, (
            f"Graph status must return 200 regardless of learn plugin state; "
            f"got {r.status_code}: {r.text}"
        )
        body = r.json()
        assert "cache_exists" in body
        assert "graph_available" in body
        # Learn-specific fields must NOT appear in the graph status response.
        assert "akms_learn_available" not in body, (
            "graph/status must not expose learn plugin state"
        )
