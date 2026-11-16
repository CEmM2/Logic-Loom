"""Mode-specific option wiring: the Learn-tab mode panels must actually CONFIGURE
generation, not collect input that goes nowhere.

This pins the route→adapter→akms_learn.LearningRequest chain for the options the
UI mode panels expose:
  - granularity     → multi_granularity (overview / standard / deep_dive)
  - policy          → llm_expanded source-lock policy
  - learner_profile → adaptive_path learner form
  - include_pitfalls / include_code_links / max_nodes → general LearningRequest knobs

Regression guard for the gap found post-Phase-6: CompileRequest did not expose
these, build_learning_request did not forward granularity/policy/learner_profile,
and the route never passed them — so the mode panels were dead UI.
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

HAS_AKMS_LEARN = importlib.util.find_spec("akms_learn") is not None


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(tmp_path / "config.toml"),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
        },
    ):
        from logic_loom_api.deps import reload_settings

        reload_settings()
        from logic_loom_api.main import api

        with TestClient(api) as c:
            yield c
        reload_settings()


def _fixture_slice() -> dict:
    from akms_learn import fixture_graph

    gs = fixture_graph()
    return {"nodes": list(gs.nodes), "edges": list(gs.edges), "metadata": dict(gs.metadata)}


@pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
class TestModeOptionWiring:
    def test_build_learning_request_forwards_mode_options(self) -> None:
        """build_learning_request marshals granularity/policy/learner_profile (+
        include_* / max_nodes) into the canonical akms_learn LearningRequest."""
        req = learn_adapter.build_learning_request(
            topic="T",
            goal="G",
            generation_option="multi_granularity",
            granularity="deep_dive",
            policy="explanatory_only",
            learner_profile={
                "knows": ["linear_algebra"],
                "weak": ["complex_numbers"],
                "goals": ["understand j2"],
                "conservative_mode": False,
            },
            include_code_links=True,
            include_pitfalls=True,
            max_nodes=5,
        )
        assert req.granularity == "deep_dive"
        assert req.policy == "explanatory_only"
        # learner_profile is coerced to the akms_learn LearnerProfile model.
        assert req.learner_profile is not None
        assert req.learner_profile.conservative_mode is False
        assert req.learner_profile.knows == ("linear_algebra",)
        assert req.include_code_links is True
        assert req.include_pitfalls is True
        assert req.max_nodes == 5

    def test_granularity_drives_output_end_to_end(self, client: TestClient) -> None:
        """A real compile through the route: multi_granularity overview vs deep_dive
        must produce DIFFERENT markdown — proving the UI granularity selector
        actually reaches and steers the compiler (not a no-op)."""
        slice_payload = _fixture_slice()

        def compile(granularity: str) -> dict:
            r = client.post(
                "/api/learn/compile",
                json={
                    "generation_option": "multi_granularity",
                    "topic": "T",
                    "goal": "G",
                    "graph_slice": slice_payload,
                    "granularity": granularity,
                },
            )
            assert r.status_code == 200, r.text
            return r.json()

        overview = compile("overview")
        deep_dive = compile("deep_dive")
        assert overview["markdown"] != deep_dive["markdown"], (
            "granularity must steer multi_granularity output — overview and "
            "deep_dive markdown should differ; if identical, the field is not wired"
        )

    def test_unknown_granularity_rejected(self, client: TestClient) -> None:
        """An out-of-vocabulary granularity is rejected at request validation
        (the CompileRequest Literal), not silently dropped."""
        r = client.post(
            "/api/learn/compile",
            json={
                "generation_option": "multi_granularity",
                "topic": "T",
                "goal": "G",
                "graph_slice": _fixture_slice(),
                "granularity": "bogus",
            },
        )
        assert r.status_code == 422, f"bogus granularity must 422; got {r.status_code}"
