"""Tests for the Learning Source Review Bundle generator (closure gate).

Covers:
  1. Bundle generated via the integration surface (the backend/plugin path
     Logic-Loom calls — routes/learn.py + learn_adapter), NOT a private bypass.
  2. manifest.json records all required fields with status review_bundle_generated.
  3. Bundle validates against the review-bundle manifest contract.

Output: artifacts/review_bundles/plan_4_logic_loom_integration/ with the six
required artifacts (logic_loom_preview_payload.json, generated_preview.html,
source_packet.json, traceability.md, warnings.md, feedback_form.md) + manifest.json.
learning_modes_used = [logic_loom_preview, plugin_status, export_flow].

NOTE: the generator is the thing under test — these tests validate a bundle
produced by the real compile/export path against a real graph (akms_learn +
akms_repo Nodes_Vault / the deterministic fixture graph), not hand-written
fixtures. Tier: regression.

Test design
-----------
* The COMMITTED bundle (under ``artifacts/review_bundles/plan_4_logic_loom_integration/``)
  is validated by ``test_bundle_artifacts_present`` + ``test_manifest_validates``.
* ``test_generated_via_integration_path`` REGENERATES the bundle into a tmp dir
  via the same ``generate_review_bundle`` entrypoint (headless — no Tauri/GUI),
  then proves the HTML/packet artifacts are produced from a ``POST
  /api/learn/compile`` RESPONSE (read back via the artifact route), NOT hand-built:
  it re-drives the route layer directly with the SAME deterministic request and
  asserts the bundle's html/packet match the route's exported artifacts byte-for-
  byte. This seeds the headless-reproducibility check in
  test_learn_interface_closure.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Make the generator script importable (it lives under <repo>/scripts/).
# ---------------------------------------------------------------------------

# tests/learn/ -> tests -> logic_loom_api -> services -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import generate_review_bundle as grb  # noqa: E402

HAS_AKMS_LEARN = importlib.util.find_spec("akms_learn") is not None

#: The committed bundle directory (the closure deliverable).
_COMMITTED_BUNDLE_DIR = grb.default_output_dir()

#: Required artifact filenames + the manifest.
_REQUIRED_ARTIFACTS = grb.REQUIRED_ARTIFACTS
_MANIFEST = "manifest.json"


def _require_committed_bundle() -> None:
    """Skip when the committed bundle is not in this tree.

    `artifacts/` is an internal deliverable and is left out of the published
    source tree; `test_generated_via_integration_path` still regenerates and
    checks a bundle there. In a tree that carries `artifacts/`, a missing
    bundle stays a failure.
    """
    if not (_REPO_ROOT / "artifacts").is_dir():
        pytest.skip("artifacts/ is internal and not part of the published source tree")


def _load_manifest(bundle_dir: Path) -> dict:
    return json.loads((bundle_dir / _MANIFEST).read_text(encoding="utf-8"))


def _norm_newlines(text: str) -> str:
    """Normalise CRLF/CR → LF so byte-for-byte content comparisons are
    platform-robust. The committed bundle is written with LF (generated on a
    POSIX host); a live regenerate on Windows yields CRLF from the OS-level text
    write. Line endings are NOT part of the 'is this the real exporter output'
    contract these assertions check, so we compare content modulo newline style."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


class TestReviewBundle:
    # -----------------------------------------------------------------------
    # Artifacts present — the committed bundle has all six + manifest,
    # each non-empty / well-formed.
    # -----------------------------------------------------------------------
    def test_bundle_artifacts_present(self) -> None:
        """All six artifacts + manifest.json exist and are non-empty / well-formed.

        Validated against the COMMITTED bundle deliverable (the plan-overview
        closure checkbox asserts it exists on disk).
        """
        _require_committed_bundle()
        bundle = _COMMITTED_BUNDLE_DIR
        assert bundle.is_dir(), (
            f"committed review bundle dir must exist at {bundle} "
            "(run scripts/generate_review_bundle.py)"
        )

        # Every required artifact + manifest present and non-empty.
        for name in (*_REQUIRED_ARTIFACTS, _MANIFEST):
            f = bundle / name
            assert f.is_file(), f"required bundle artifact missing: {name}"
            assert f.stat().st_size > 0, f"bundle artifact must be non-empty: {name}"

        # JSON artifacts parse.
        preview = json.loads((bundle / "logic_loom_preview_payload.json").read_text("utf-8"))
        assert isinstance(preview, dict) and preview, "preview payload must be a non-empty object"
        # It is the compile RESPONSE shape (route CompileResponse).
        for key in ("artifact_id", "markdown", "warnings", "provenance_summary"):
            assert key in preview, f"preview payload must carry compile-response key {key!r}"
        assert isinstance(preview["markdown"], str) and preview["markdown"].strip(), (
            "preview payload markdown (the UI preview) must be non-empty"
        )

        packet = json.loads((bundle / "source_packet.json").read_text("utf-8"))
        assert isinstance(packet, dict) and packet, "source packet must be a non-empty object"
        # Canonical LearningSourcePacket shape (real akms_learn packet).
        for key in ("akms_learning_schema", "body", "request", "source"):
            assert key in packet, f"source packet must carry canonical LSP key {key!r}"

        # HTML is non-trivial (a real exported document, not a stub).
        html = (bundle / "generated_preview.html").read_text("utf-8")
        assert "<html" in html.lower() and len(html) > 500, (
            f"generated_preview.html must be a non-trivial HTML document; len={len(html)}"
        )

        # Markdown artifacts have their expected headings.
        feedback = (bundle / "feedback_form.md").read_text("utf-8")
        assert "Review Feedback: plan_4_logic_loom_integration" in feedback, (
            "feedback_form.md must be the feedback-form seed verbatim"
        )
        assert "## Verdict" in feedback and "Ship as-is" in feedback

        warnings_md = (bundle / "warnings.md").read_text("utf-8")
        assert warnings_md.strip(), "warnings.md must be non-empty (rendered warnings[])"

        traceability = (bundle / "traceability.md").read_text("utf-8")
        # Provenance trace: backend request payload + graph hash + endpoints.
        assert "POST /api/learn/compile" in traceability, (
            "traceability.md must name the backend compile endpoint exercised"
        )
        assert "graph_hash" in traceability, "traceability.md must embed the graph hash"
        assert '"generation_option"' in traceability, (
            "traceability.md must embed the exact backend CompileRequest payload"
        )

    # -----------------------------------------------------------------------
    # Manifest validates — every required field present with the right
    # value; artifacts list matches the files on disk.
    # -----------------------------------------------------------------------
    def test_manifest_validates(self) -> None:
        """manifest.json records all required fields with correct values."""
        _require_committed_bundle()
        bundle = _COMMITTED_BUNDLE_DIR
        manifest = _load_manifest(bundle)

        # All required keys present.
        required_keys = {
            "plan_id",
            "status",
            "generator",
            "generator_version",
            "command",
            "learning_modes_used",
            "artifacts",
            "warnings",
            "unavailable_capabilities",
        }
        assert required_keys <= set(manifest.keys()), (
            f"manifest missing required fields: {required_keys - set(manifest.keys())}"
        )

        # Exact-value contracts.
        assert manifest["plan_id"] == "plan_4_logic_loom_integration"
        assert manifest["status"] == "review_bundle_generated", (
            f"status must be review_bundle_generated; got {manifest['status']!r}"
        )
        assert manifest["generator"] == "akms-learn", (
            f"generator must be akms-learn; got {manifest['generator']!r}"
        )
        assert isinstance(manifest["generator_version"], str) and manifest["generator_version"], (
            "generator_version must be a non-empty string (the installed akms_learn version)"
        )

        # The three learning modes (set equality — order not contractual).
        assert set(manifest["learning_modes_used"]) == {
            "logic_loom_preview",
            "plugin_status",
            "export_flow",
        }, f"learning_modes_used must be the three modes; got {manifest['learning_modes_used']}"

        # command records the exact API path / invocation used.
        assert isinstance(manifest["command"], str) and manifest["command"].strip()
        assert "/api/learn/compile" in manifest["command"], (
            "manifest.command must record the /api/learn/compile route used to generate"
        )
        assert "generate_review_bundle" in manifest["command"], (
            "manifest.command must record the generator invocation"
        )

        # artifacts list matches the six required filenames AND the files on disk.
        assert manifest["artifacts"] == list(_REQUIRED_ARTIFACTS), (
            f"manifest.artifacts must list the six required artifacts in order; "
            f"got {manifest['artifacts']}"
        )
        for name in manifest["artifacts"]:
            assert (bundle / name).is_file(), (
                f"manifest lists artifact {name!r} but it is not on disk"
            )

        # warnings + unavailable_capabilities are lists (possibly empty).
        assert isinstance(manifest["warnings"], list)
        assert isinstance(manifest["unavailable_capabilities"], list)

    # -----------------------------------------------------------------------
    # Generated via integration path — regenerate headlessly via the same
    # entrypoint and prove the HTML/packet come from a POST /api/learn/compile
    # RESPONSE, not hand-built.
    # -----------------------------------------------------------------------
    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_generated_via_integration_path(self, tmp_path: Path) -> None:
        """The generator drives the route layer; HTML/packet match the compile RESPONSE.

        Steps:
          1. Regenerate the bundle into a tmp dir via ``generate_review_bundle``
             (headless — TestClient over logic_loom_api.main.api, NO Tauri/GUI).
             This proves the generator runs without a desktop shell (seeds the
             headless-reproducibility check).
          2. Independently re-drive the SAME route layer with the SAME deterministic
             request (the export_flow body) and read the HTML + packet back through
             the artifact route. Assert the regenerated bundle's
             generated_preview.html / source_packet.json EQUAL those route outputs.
             A hand-written artifact would NOT match the live exporter output —
             so equality is proof the artifacts were produced by the integration
             surface, not synthesized.
        """
        import os

        from fastapi.testclient import TestClient

        # --- Step 1: headless regeneration via the generator entrypoint ---
        out = tmp_path / "regen_bundle"
        manifest = grb.generate_bundle(out)

        assert manifest["status"] == "review_bundle_generated"
        assert set(manifest["learning_modes_used"]) == {
            "logic_loom_preview",
            "plugin_status",
            "export_flow",
        }
        for name in (*_REQUIRED_ARTIFACTS, _MANIFEST):
            assert (out / name).is_file(), f"regenerated bundle missing {name}"

        regen_html = (out / "generated_preview.html").read_text("utf-8")
        regen_packet = json.loads((out / "source_packet.json").read_text("utf-8"))

        # --- Step 2: independently drive the route layer with the same request ---
        # Pin a separate tmp cache so this compile is isolated from the user's cache.
        cache_dir = tmp_path / "verify_cache"
        prev = os.environ.get("LOGIC_LOOM_CACHE_PATH")
        os.environ["LOGIC_LOOM_CACHE_PATH"] = str(cache_dir)
        try:
            from logic_loom_api.deps import reload_settings  # type: ignore[import-not-found]

            reload_settings()
            from logic_loom_api.main import api  # type: ignore[import-not-found]

            graph_slice = grb.fixture_graph_slice()
            export_body = grb.compile_request_body(
                graph_slice, exporters=grb.EXPORT_FLOW_EXPORTERS
            )

            with TestClient(api) as client:
                # The compile is a real route call — the integration surface.
                resp = client.post("/api/learn/compile", json=export_body)
                assert resp.status_code == 200, f"compile failed: {resp.status_code}: {resp.text}"
                payload = resp.json()
                artifact_id = payload["artifact_id"]
                exports = payload["exports"]

                # HTML produced by the html exporter, read via the artifact route.
                html_exports = [e for e in exports if e["type"] == "html"]
                assert html_exports, "export_flow must produce an HTML artifact"
                html_rel = Path(html_exports[0]["path"]).name
                route_html = client.get(
                    f"/api/learn/artifacts/{artifact_id}/{html_rel}"
                ).text

                # Canonical packet, read via the artifact route.
                packet_rel = Path(payload["packet_path"]).name
                route_packet = client.get(
                    f"/api/learn/artifacts/{artifact_id}/{packet_rel}"
                ).json()
        finally:
            if prev is None:
                os.environ.pop("LOGIC_LOOM_CACHE_PATH", None)
            else:
                os.environ["LOGIC_LOOM_CACHE_PATH"] = prev
            from logic_loom_api.deps import reload_settings  # type: ignore[import-not-found]

            reload_settings()

        # The bundle's HTML/packet MUST equal the live route exporter output.
        # (Identical deterministic inputs → identical exporter output. A
        # hand-built artifact would diverge.)
        assert _norm_newlines(regen_html) == _norm_newlines(route_html), (
            "generated_preview.html in the bundle must be the EXACT html the "
            "POST /api/learn/compile export flow produced (read via the artifact "
            "route) — not a hand-written/synthesized document (compared modulo "
            "platform line endings)"
        )
        # The packet carries a wall-clock ``created_at`` that varies per compile;
        # everything else (schema, body, request, source/graph_hash, provenance,
        # validation) is deterministic for identical inputs. Compare with
        # ``created_at`` excluded so the (volatile) timestamp does not mask the
        # contract: the bundle packet IS the live route packet modulo timestamp.
        def _drop_created_at(p: dict) -> dict:
            return {k: v for k, v in p.items() if k != "created_at"}

        assert _drop_created_at(regen_packet) == _drop_created_at(route_packet), (
            "source_packet.json in the bundle must be the EXACT canonical packet "
            "the compile/export flow wrote (read via the artifact route), modulo "
            "the per-compile created_at timestamp"
        )
        # The provenance hash (request_hash:graph_hash) MUST be identical — that
        # is the deterministic identity of the packet, independent of wall-clock.
        assert regen_packet["source"]["graph_hash"] == route_packet["source"]["graph_hash"]
        assert regen_packet["request"]["request_hash"] == route_packet["request"]["request_hash"]

        # The preview payload artifact must carry the compile-RESPONSE provenance
        # (proves it came from POST /api/learn/compile, not a fabricated dict).
        regen_preview = json.loads(
            (out / "logic_loom_preview_payload.json").read_text("utf-8")
        )
        assert regen_preview["provenance_summary"]["graph_hash"] == (
            payload["provenance_summary"]["graph_hash"]
        ), "preview payload graph_hash must match the live compile response"
