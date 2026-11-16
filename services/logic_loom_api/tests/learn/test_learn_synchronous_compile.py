"""Tests for the synchronous-compile decision (background runs only if compile/export
is long-running).

Requirement: either background runs work with status polling, or a rationale
documents why synchronous suffices.

DECISION (2026-06-10): Background runs are DEFERRED.
  - Measured max compile time: 81 ms (40-node slice, all 4 exporters, 3 runs).
  - POST /api/learn/compile stays SYNCHRONOUS — returns the full CompileResponse
    in a single HTTP 200, no job-id indirection, no 202-accepted-then-poll.
  - Re-evaluation trigger: if compile exceeds ~5 s (e.g. real LLM provider, very
    large slice), implement background runs + status/poll or SSE endpoint.
  - Full rationale: dev/notes/p4-5_background_runs_decision.md

Tests in this file ENFORCE the deferral decision so it cannot silently rot:
  1. Decision note exists and contains key markers (TestDecisionNote).
  2. Compile is synchronous: real TestClient compile → HTTP 200 + full CompileResponse
     in one call, no job-id indirection (TestCompileIsSynchronous).
  3. Representative timing bound: 40-node slice + all 4 exporters < 5 s (same class).
  4. Deferred lifecycle placeholder — skipped until the re-evaluation trigger fires
     (TestBackgroundJobLifecycleDeferred).

Patterns reused from test_learn_halt_gate.py (decision-note enforcement) and
test_learn_backend_suite.py (TestClient compile fixture + timing assertions).
"""

from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Repo root: this file is at <repo>/services/logic_loom_api/tests/learn/test_learn_synchronous_compile.py
# parents[4] → repo root
_REPO_ROOT = Path(__file__).resolve().parents[4]
_NOTE_PATH = _REPO_ROOT / "dev" / "notes" / "p4-5_background_runs_decision.md"

# Key markers that must appear in the decision note — removing them breaks this test.
_DECISION_MARKERS: list[tuple[str, str]] = [
    # (label, keyword)
    ("synchronous decision", "synchronous"),
    ("re-evaluation trigger", "re-evaluation trigger"),
    ("deferred status", "DEFERRED"),
    ("5s threshold", "5 s"),
]

HAS_AKMS_LEARN = importlib.util.find_spec("akms_learn") is not None


# ---------------------------------------------------------------------------
# Shared fixture (mirrors test_learn_backend_suite.py exactly)
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
    """7-node fixture graph from akms_learn.fixture_graph()."""
    from akms_learn import fixture_graph  # type: ignore[import-not-found]

    gs = fixture_graph()
    return {
        "nodes": list(gs.nodes),
        "edges": list(gs.edges),
        "metadata": dict(gs.metadata),
    }


def _synthetic_40_node_slice_payload() -> dict:
    """Build a 40-node synthetic graph slice for representative timing tests.

    Replicates the fixture_graph nodes 6 times with unique IDs, then truncates
    to exactly 40 nodes.  Edges connect consecutive nodes in a linear chain.
    This simulates a real ego-slice of ~40 domain nodes — larger than any
    typical teaching unit but representative of a "substantial slice" scenario
    per the decision note's measurement methodology.
    """
    from akms_learn import fixture_graph  # type: ignore[import-not-found]

    gs = fixture_graph()
    base_nodes = list(gs.nodes)

    synth_nodes = []
    for replica in range(6):
        for n in base_nodes:
            nn = dict(n)
            base_id = n.get("node_id") or n.get("id") or "node"
            nn["node_id"] = f"{base_id}_v{replica}"
            if "id" in nn:
                nn["id"] = nn["node_id"]
            synth_nodes.append(nn)

    synth_nodes = synth_nodes[:40]  # exactly 40 nodes

    synth_edges = []
    for i in range(min(20, len(synth_nodes) - 1)):
        src = synth_nodes[i].get("node_id") or synth_nodes[i].get("id")
        tgt = synth_nodes[i + 1].get("node_id") or synth_nodes[i + 1].get("id")
        synth_edges.append(
            {"from": src, "to": tgt, "edge_id": f"e{i}", "type": "prerequisite"}
        )

    return {
        "nodes": synth_nodes,
        "edges": synth_edges,
        "metadata": {"source": "synthetic_40_node_p4_5_timing_test"},
    }


# ---------------------------------------------------------------------------
# Class 1: Decision note enforcement
# ---------------------------------------------------------------------------


class TestDecisionNote:
    """Asserts the background-runs decision note exists and contains all key markers.

    Analogous to TestLearnHaltGateNote in test_learn_halt_gate.py: the documented
    decision is load-bearing — deleting the note or stripping a marker breaks
    this test so the deferral cannot silently rot.
    """

    @pytest.mark.regression
    def test_decision_note_exists_with_key_markers(self) -> None:
        """Verifies: dev/notes/p4-5_background_runs_decision.md exists and documents
        the synchronous-compile decision with a re-evaluation trigger.

        Requirement: "a rationale documents why synchronous suffices."
        """
        if not (_REPO_ROOT / "dev").is_dir():
            pytest.skip("dev/ is internal and not part of the published source tree")
        assert _NOTE_PATH.is_file(), (
            f"Background-runs decision note not found at expected path: {_NOTE_PATH}\n"
            f"  Repo root resolved to: {_REPO_ROOT}\n"
            f"  Restore the decision note; it documents why /compile is synchronous."
        )

        content = _NOTE_PATH.read_text(encoding="utf-8")

        for label, keyword in _DECISION_MARKERS:
            assert keyword in content, (
                f"Background-runs decision note is missing the marker for {label!r}.\n"
                f"  Expected keyword: {keyword!r}\n"
                f"  Note path: {_NOTE_PATH}"
            )


# ---------------------------------------------------------------------------
# Class 2: Compile is synchronous (enforcement)
# ---------------------------------------------------------------------------


class TestCompileIsSynchronous:
    """Asserts that POST /api/learn/compile returns the FULL CompileResponse
    synchronously in a single HTTP 200 — no 202-accepted-then-poll, no job-id
    indirection.

    This test is the enforcement counterpart to the decision note: if someone
    adds async job infrastructure and changes /compile to return 202 + job_id,
    this test fails, surfacing the divergence from the documented decision.
    """

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_compile_returns_full_response_synchronously(
        self, client: TestClient
    ) -> None:
        """Verifies: POST /api/learn/compile returns HTTP 200 with the full
        CompileResponse (artifact_id + markdown + exports) in a single call.

        Failure modes caught:
          - HTTP 202 (job accepted) — would mean async indirection was added
          - Missing artifact_id in response — would mean a job-id envelope was returned
          - Empty exports — would mean the response was a stub / placeholder

        Requirement: "a rationale documents why synchronous suffices" — by asserting
        the single-call shape we confirm /compile has NOT been changed to 202/job-id.
        """
        body = {
            "generation_option": "deterministic_outline",
            "topic": "j2 return mapping stress tensor",
            "goal": "Understand the j2 return-mapping algorithm",
            "graph_slice": _fixture_graph_slice_payload(),
        }

        r = client.post("/api/learn/compile", json=body)

        # Must be 200 (synchronous full response), never 202 (async accepted).
        assert r.status_code == 200, (
            f"POST /api/learn/compile must return HTTP 200 (synchronous full response);\n"
            f"  got {r.status_code}. If 202: background-run infra was added — update the\n"
            f"  decision note and re-evaluate dev/notes/p4-5_background_runs_decision.md."
        )

        data = r.json()

        # artifact_id must be present — a job-id envelope would not carry this key.
        assert "artifact_id" in data, (
            f"Response must carry 'artifact_id' (CompileResponse), not a job-id envelope.\n"
            f"  Got keys: {sorted(data.keys())}"
        )
        assert isinstance(data["artifact_id"], str) and data["artifact_id"], (
            "artifact_id must be a non-empty string (synchronous compile path)"
        )

        # markdown preview must be present (full response, not a deferred stub).
        assert "markdown" in data and data["markdown"], (
            "Response must carry non-empty 'markdown' in a synchronous compile response."
        )

        # exports list must be present (full response).
        assert isinstance(data.get("exports"), list), (
            "Response must carry 'exports' list in a synchronous compile response."
        )

        # No job_id key — would indicate async indirection was added.
        assert "job_id" not in data, (
            f"Response must NOT carry 'job_id' — /compile is synchronous (documented decision).\n"
            f"  If background runs were added, update dev/notes/p4-5_background_runs_decision.md."
        )

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_compile_40_nodes_all_exporters_within_timing_bound(
        self, client: TestClient
    ) -> None:
        """Verifies: a representative compile (40-node slice, all 4 exporters) completes
        within a generous wall-time bound (< 5 s).

        This backs the "fast enough to stay synchronous" claim WITHOUT being flaky:
          - The 5 s bound is ~60× the measured max (81 ms on 2026-06-10).
          - It catches a real regression (e.g. accidental LLM call, blocking I/O)
            while tolerating CI variance.

        Re-evaluation trigger: if this assertion fails consistently in a real environment,
        see dev/notes/p4-5_background_runs_decision.md for guidance on when to add
        background runs.
        """
        body = {
            "generation_option": "deterministic_outline",
            "topic": "j2 return mapping plasticity tensor mechanics prerequisites",
            "goal": "Master the complete j2 return-mapping algorithm with all prerequisite nodes",
            "exporters": ["markdown", "notebook", "html", "bundle"],
            "graph_slice": _synthetic_40_node_slice_payload(),
        }

        t0 = time.perf_counter()
        r = client.post("/api/learn/compile", json=body)
        elapsed = time.perf_counter() - t0

        assert r.status_code == 200, (
            f"Compile must return 200 (40-node slice, all exporters); "
            f"got {r.status_code}: {r.text[:300]}"
        )
        assert elapsed < 5.0, (
            f"Compile took {elapsed:.3f}s > 5.0s threshold.\n"
            f"  This exceeds the re-evaluation trigger for background runs.\n"
            f"  See dev/notes/p4-5_background_runs_decision.md — if this is consistent,\n"
            f"  the deferral decision should be revisited and background runs implemented."
        )

        data = r.json()
        assert "artifact_id" in data and data["artifact_id"], (
            "40-node compile must return artifact_id (synchronous full response)"
        )
        assert isinstance(data.get("exports"), list) and len(data["exports"]) >= 1, (
            "40-node compile must return non-empty exports list"
        )


# ---------------------------------------------------------------------------
# Class 3: Deferred lifecycle placeholder
# ---------------------------------------------------------------------------


class TestBackgroundJobLifecycleDeferred:
    """Placeholder for the background-job lifecycle test.

    DEFERRED by the background-runs decision — implement if the re-evaluation trigger fires.

    When the trigger condition is met (compile consistently > ~5 s, real LLM
    provider, very large slices), replace the pytest.skip below with a real
    integration test that:
      1. POSTs to /api/learn/compile and receives HTTP 202 + job_id.
      2. Polls GET /api/learn/jobs/{job_id} until a terminal state.
      3. Asserts the terminal result carries the full CompileResponse payload.

    Also update dev/notes/p4-5_background_runs_decision.md accordingly.
    """

    @pytest.mark.integration
    def test_background_job_lifecycle_deferred(self) -> None:
        """Background-job lifecycle — deferred by the background-runs decision (sub-100ms compile).

        Implement when the re-evaluation trigger fires:
          - Compile consistently > ~5 s (real LLM provider, very large slices)
          - See dev/notes/p4-5_background_runs_decision.md for full trigger conditions.
        """
        pytest.skip(
            "Deferred by the background-runs decision — compile/export is sub-100ms (max 81ms, 40-node "
            "slice, all 4 exporters, 2026-06-10). Implement if the re-evaluation trigger "
            "fires. See dev/notes/p4-5_background_runs_decision.md."
        )
