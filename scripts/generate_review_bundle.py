#!/usr/bin/env python3
"""Generate the Learn integration's Learning Source Review Bundle (closure gate).

This is the closure-gate generator for the Logic-Loom Learn integration (output
directory ``plan_4_logic_loom_integration``). It produces the reviewer-facing
Learning Source Review Bundle **through the
backend/plugin path that Logic-Loom itself calls** — the FastAPI ``/api/learn/*``
route layer (``routes/learn.py`` → ``adapters/learn_adapter`` → ``akms_learn``) —
NOT a private helper that bypasses the integration surface.

Why the route layer is the integration surface
-----------------------------------------------
The Logic-Loom frontend never imports ``akms_learn``. It speaks HTTP to the
sidecar's ``/api/learn/*`` routes (see ``apps/desktop/src/api/*``). The sidecar
serves ``logic_loom_api.main.api`` — the exact ASGI app this generator drives
via ``TestClient``. So calling ``GET /api/learn/status`` / ``POST
/api/learn/compile`` / ``GET /api/learn/artifacts/...`` here exercises the SAME
code path the desktop app does, with zero Tauri/GUI involvement. The generated
HTML/packet/markdown come from the real ``akms_learn`` exporters invoked through
that route — they are read out of the compile RESPONSE + artifact endpoint, never
hand-constructed (the generator is the thing under test).

Three learning modes (manifest ``learning_modes_used``)
-------------------------------------------------------
* ``plugin_status``     → ``GET  /api/learn/status``   (plugin availability /
                          capabilities / version / unavailable exporters).
* ``logic_loom_preview``→ ``POST /api/learn/compile``  (the markdown preview the
                          Logic-Loom UI renders — captured as the compile payload).
* ``export_flow``       → ``POST /api/learn/compile`` with ``exporters=[...]``
                          (produces the HTML + packet + bundle artifacts on disk,
                          then read back via ``GET /api/learn/artifacts/...``).

Reproducibility
---------------
The explicit ``graph_slice`` is ``akms_learn.fixture_graph()`` — a deterministic
slice independent of the developer's specific graph state — so the bundle
regenerates byte-for-stable across machines. ``traceability.md`` embeds the exact
``CompileRequest`` payload + graph hash + node ids + the endpoints exercised, so
the bundle is reproducible via the API alone, without launching Logic-Loom
(seeds the headless-reproducibility check in
``tests/learn/test_learn_interface_closure.py``).

Usage
-----
    PYTHONPATH=services/logic_loom_api/src \
        ../../../.venv/bin/python scripts/generate_review_bundle.py [--output-dir DIR]

Default output dir: ``artifacts/review_bundles/plan_4_logic_loom_integration/``
(relative to the repo root — the directory containing ``services/`` and
``scripts/``). The bundle is a committed deliverable.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# --- Bundle contract constants ---------------------------------------------

PLAN_ID = "plan_4_logic_loom_integration"
BUNDLE_STATUS = "review_bundle_generated"
GENERATOR_NAME = "akms-learn"
GENERATOR_DIST = "akms-learn"  # importlib.metadata distribution name

LEARNING_MODES = ["logic_loom_preview", "plugin_status", "export_flow"]

#: The six required artifact filenames, order preserved.
REQUIRED_ARTIFACTS = [
    "logic_loom_preview_payload.json",
    "generated_preview.html",
    "source_packet.json",
    "traceability.md",
    "warnings.md",
    "feedback_form.md",
]

#: The export flow requests these exporters. ``html`` produces the
#: generated_preview.html artifact; ``markdown`` + ``bundle`` produce the
#: lesson + canonical bundle layout (manifest/concept_map/provenance).
EXPORT_FLOW_EXPORTERS = ["markdown", "html", "bundle"]

#: Deterministic, reproducible compile inputs. The graph_slice is supplied
#: separately from akms_learn.fixture_graph() at runtime.
COMPILE_TOPIC = "j2 return mapping algorithm"
COMPILE_GOAL = "Understand how the j2 return-mapping algorithm is structured and taught"
COMPILE_MODE = "deterministic_outline"

#: Feedback form seed (the reviewer questionnaire, kept verbatim).
FEEDBACK_FORM_SEED = """# Review Feedback: plan_4_logic_loom_integration

## Reviewer background

- Role:
- Familiarity with topic:
- Familiarity with AKMS / Logic-Loom:

## Learning value

1. What was clear?
2. What was confusing?
3. What felt missing?
4. What felt unnecessary?

## Technical correctness

1. Did you notice mathematical, implementation, or provenance errors?
2. Were assumptions stated clearly?
3. Were generated examples believable and useful?

## Structure

1. Was the order of concepts useful?
2. Were transitions between sections clear?
3. Was the artifact too long, too short, or about right?

## Verdict

- [ ] Ship as-is
- [ ] Ship with minor edits
- [ ] Needs another iteration
- [ ] Not useful in current form

## Concrete requested changes

- [ ] ...
"""


# ---------------------------------------------------------------------------
# Repo-root + default output resolution
# ---------------------------------------------------------------------------

def repo_root() -> Path:
    """Return the repo/worktree root (the dir containing ``scripts/`` + ``services/``)."""
    return Path(__file__).resolve().parent.parent


def default_output_dir() -> Path:
    return repo_root() / "artifacts" / "review_bundles" / PLAN_ID


# ---------------------------------------------------------------------------
# Graph slice (reproducible fixture)
# ---------------------------------------------------------------------------

def fixture_graph_slice() -> dict[str, Any]:
    """Return the deterministic explicit graph_slice payload (akms_learn fixture).

    Using the plugin's own ``fixture_graph()`` keeps the bundle reproducible and
    independent of the developer's specific akms_repo graph contents — it is the
    explicit ``graph_slice`` request form A per the route contract.
    """
    from akms_learn import fixture_graph  # type: ignore[import-not-found]

    gs = fixture_graph()
    return {
        "nodes": list(gs.nodes),
        "edges": list(gs.edges),
        "metadata": dict(gs.metadata),
    }


def compile_request_body(graph_slice: dict[str, Any], *, exporters: list[str] | None = None) -> dict[str, Any]:
    """Build a CompileRequest body for POST /api/learn/compile (route contract)."""
    body: dict[str, Any] = {
        "generation_option": COMPILE_MODE,
        "topic": COMPILE_TOPIC,
        "goal": COMPILE_GOAL,
        "graph_slice": graph_slice,
    }
    if exporters is not None:
        body["exporters"] = exporters
    return body


# ---------------------------------------------------------------------------
# Core generation — drives the integration surface
# ---------------------------------------------------------------------------

class GeneratorError(RuntimeError):
    """Raised when the bundle cannot be generated (e.g. plugin unavailable)."""


def generate_bundle(output_dir: Path) -> dict[str, Any]:
    """Generate the review bundle into ``output_dir`` via the /api/learn route layer.

    Returns the manifest dict (also written to ``manifest.json``).

    The compile + export artifacts are written by ``akms_learn`` into a TMP
    Logic-Loom learning cache (``LOGIC_LOOM_CACHE_PATH``) so the generator never
    clobbers the developer's real ``~/.cache/logic-loom`` — exactly as the
    sidecar would, with a per-launch cache. The six review-bundle artifacts are
    then assembled into ``output_dir`` from the compile RESPONSE + the artifact
    endpoint (the HTML/packet are READ from the export flow, not synthesized).
    """
    # Import lazily so a missing PYTHONPATH gives a clean error, not an import
    # error at module load.
    from fastapi.testclient import TestClient

    output_dir.mkdir(parents=True, exist_ok=True)

    # Pin a tmp learning cache so the export flow writes under our control and
    # never touches the user's real cache. The sidecar uses a random per-launch
    # port + the configured cache; here we redirect only the cache.
    cache_tmp = Path(tempfile.mkdtemp(prefix="ll-review-bundle-cache-"))
    prev_cache = os.environ.get("LOGIC_LOOM_CACHE_PATH")
    os.environ["LOGIC_LOOM_CACHE_PATH"] = str(cache_tmp / "cache")

    try:
        # Reload settings so the tmp cache_path takes effect, then build the
        # real ASGI app the sidecar serves.
        from logic_loom_api.deps import reload_settings  # type: ignore[import-not-found]

        reload_settings()
        from logic_loom_api.main import api  # type: ignore[import-not-found]

        graph_slice = fixture_graph_slice()
        node_ids = [n.get("node_id") or n.get("id") for n in graph_slice["nodes"]]

        with TestClient(api) as client:
            # --- mode: plugin_status → GET /api/learn/status ---
            status_resp = client.get("/api/learn/status")
            if status_resp.status_code != 200:
                raise GeneratorError(
                    f"GET /api/learn/status returned {status_resp.status_code}: {status_resp.text}"
                )
            status_payload = status_resp.json()
            if not status_payload.get("akms_learn_available"):
                raise GeneratorError(
                    "akms_learn plugin is unavailable; cannot generate the closure "
                    "bundle (the generator IS the thing under test — a real compile "
                    "is required, hand-written artifacts do not satisfy the gate)."
                )

            # Capture exporter availability (drives unavailable_capabilities).
            exporters_resp = client.get("/api/learn/exporters")
            exporters_payload = exporters_resp.json() if exporters_resp.status_code == 200 else {"exporters": []}
            exporter_entries = exporters_payload.get("exporters", [])
            unavailable_capabilities = sorted(
                e["id"] for e in exporter_entries if e.get("status") != "available"
            )
            available_exporter_ids = {
                e["id"] for e in exporter_entries if e.get("status") == "available"
            }

            # --- mode: logic_loom_preview → POST /api/learn/compile (no exporters) ---
            # This is the markdown preview the Logic-Loom UI shows. We capture the
            # full compile RESPONSE payload as logic_loom_preview_payload.json.
            preview_body = compile_request_body(graph_slice)
            preview_resp = client.post("/api/learn/compile", json=preview_body)
            if preview_resp.status_code != 200:
                raise GeneratorError(
                    f"POST /api/learn/compile (preview) returned "
                    f"{preview_resp.status_code}: {preview_resp.text}"
                )
            preview_payload = preview_resp.json()

            # --- mode: export_flow → POST /api/learn/compile with exporters ---
            # Only request exporters the plugin actually advertises as available
            # (each requested-but-bare name maps to a "<name>_export" capability).
            requested_exporters = [
                name
                for name in EXPORT_FLOW_EXPORTERS
                if f"{name}_export" in available_exporter_ids
            ]
            export_body = compile_request_body(graph_slice, exporters=requested_exporters)
            export_resp = client.post("/api/learn/compile", json=export_body)
            if export_resp.status_code != 200:
                raise GeneratorError(
                    f"POST /api/learn/compile (export_flow) returned "
                    f"{export_resp.status_code}: {export_resp.text}"
                )
            export_payload = export_resp.json()
            artifact_id = export_payload["artifact_id"]
            exports = export_payload.get("exports", [])

            # --- Read the HTML export back through the ARTIFACT route ---
            # The generated_preview.html is produced by the akms_learn html
            # exporter; we read it via GET /api/learn/artifacts/{id}/{path} — the
            # same path-traversal-safe serving route the frontend uses — so the
            # HTML in the bundle is the real exported artifact, not synthesized.
            html_exports = [e for e in exports if e.get("type") == "html"]
            if not html_exports:
                raise GeneratorError(
                    "export_flow produced no HTML artifact; the html exporter must "
                    "be available to generate generated_preview.html "
                    f"(exports: {[e.get('type') for e in exports]})"
                )
            html_rel = Path(html_exports[0]["path"]).name
            html_resp = client.get(f"/api/learn/artifacts/{artifact_id}/{html_rel}")
            if html_resp.status_code != 200:
                raise GeneratorError(
                    f"GET /api/learn/artifacts/{artifact_id}/{html_rel} returned "
                    f"{html_resp.status_code}: {html_resp.text}"
                )
            generated_html = html_resp.text

            # --- Read the canonical packet back through the ARTIFACT route ---
            packet_path = export_payload.get("packet_path")
            if not packet_path:
                raise GeneratorError("export_flow response carried no packet_path")
            packet_rel = Path(packet_path).name
            packet_resp = client.get(f"/api/learn/artifacts/{artifact_id}/{packet_rel}")
            if packet_resp.status_code != 200:
                raise GeneratorError(
                    f"GET /api/learn/artifacts/{artifact_id}/{packet_rel} returned "
                    f"{packet_resp.status_code}: {packet_resp.text}"
                )
            source_packet = packet_resp.json()

            warnings = export_payload.get("warnings", [])

        # ----------------------------------------------------------------
        # Write the six artifacts (assembled from the integration responses)
        # ----------------------------------------------------------------
        # Redact the ephemeral tmp-cache root from the preview payload so the
        # committed deliverable is host-portable (the real artifacts live in this
        # bundle dir; cache_dir/packet_path/exports[].path are provenance display).
        _write_json(
            output_dir / "logic_loom_preview_payload.json",
            _redact_cache_paths(preview_payload, str(cache_tmp)),
        )
        _write_text(output_dir / "generated_preview.html", generated_html)
        _write_json(output_dir / "source_packet.json", source_packet)
        _write_text(output_dir / "warnings.md", _render_warnings_md(warnings))
        _write_text(output_dir / "feedback_form.md", FEEDBACK_FORM_SEED)

        traceability = _render_traceability_md(
            preview_body=preview_body,
            export_body=export_body,
            export_payload=export_payload,
            node_ids=node_ids,
            requested_exporters=requested_exporters,
        )
        _write_text(output_dir / "traceability.md", traceability)

        # ----------------------------------------------------------------
        # Manifest
        # ----------------------------------------------------------------
        generator_version = _generator_version()
        command = (
            "PYTHONPATH=services/logic_loom_api/src python scripts/generate_review_bundle.py"
            " | API: POST /api/learn/compile (logic_loom_api.main.api via TestClient);"
            " modes: GET /api/learn/status, POST /api/learn/compile,"
            " POST /api/learn/compile?exporters, GET /api/learn/artifacts/{id}/{path}"
        )
        manifest = {
            "plan_id": PLAN_ID,
            "status": BUNDLE_STATUS,
            "generator": GENERATOR_NAME,
            "generator_version": generator_version,
            "command": command,
            "learning_modes_used": LEARNING_MODES,
            "artifacts": list(REQUIRED_ARTIFACTS),
            "warnings": warnings,
            "unavailable_capabilities": unavailable_capabilities,
        }
        _write_json(output_dir / "manifest.json", manifest)
        return manifest

    finally:
        # Restore the env so repeated calls in one process (e.g. tests) are clean.
        if prev_cache is None:
            os.environ.pop("LOGIC_LOOM_CACHE_PATH", None)
        else:
            os.environ["LOGIC_LOOM_CACHE_PATH"] = prev_cache
        try:
            from logic_loom_api.deps import reload_settings  # type: ignore[import-not-found]

            reload_settings()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

#: Stable placeholder substituted for the ephemeral tmp learning-cache root so
#: the committed reviewer-facing payload is host-portable (no machine-specific
#: absolute tmp path leaks into the deliverable). The real on-disk artifacts live
#: in the bundle dir itself; these path fields are provenance display only.
_CACHE_PLACEHOLDER = "<learning_cache>"


def _redact_cache_paths(obj: Any, cache_root: str) -> Any:
    """Recursively replace ``cache_root`` with ``_CACHE_PLACEHOLDER`` in all
    string values, so the committed bundle does not bake in the dev's local FS
    layout (e.g. ``/var/folders/.../ll-review-bundle-cache-xxxx/...``)."""
    if isinstance(obj, str):
        return obj.replace(cache_root, _CACHE_PLACEHOLDER)
    if isinstance(obj, list):
        return [_redact_cache_paths(v, cache_root) for v in obj]
    if isinstance(obj, dict):
        return {k: _redact_cache_paths(v, cache_root) for k, v in obj.items()}
    return obj


def _write_text(path: Path, content: str) -> None:
    """Write a text artifact with canonical LF line endings on every platform.

    ``newline="\\n"`` disables the OS-level translation that ``write_text``
    otherwise applies (``\\n`` → ``\\r\\n`` on Windows). Content read back from
    the HTTP artifact route may already carry CRLF; normalising to LF first
    avoids the double-CR (``\\r\\n`` → ``\\r\\r\\n``) that would otherwise make the
    committed bundle non-reproducible across platforms.
    """
    normalised = content.replace("\r\n", "\n").replace("\r", "\n")
    path.write_text(normalised, encoding="utf-8", newline="\n")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _render_warnings_md(warnings: list[dict[str, Any]]) -> str:
    """Render the compile ``warnings[]`` as markdown."""
    lines = ["# Compile Warnings", ""]
    if not warnings:
        lines.append("_No warnings were emitted by the compile/export flow._")
        lines.append("")
        return "\n".join(lines)
    lines.append(f"The compile/export flow emitted {len(warnings)} warning(s):")
    lines.append("")
    for i, w in enumerate(warnings, 1):
        sev = w.get("severity", "?")
        code = w.get("code", "?")
        cat = w.get("category", "?")
        msg = w.get("message", "")
        ref = w.get("source_ref")
        lines.append(f"## {i}. `{code}` ({sev} / {cat})")
        lines.append("")
        lines.append(msg)
        if ref:
            lines.append("")
            lines.append(f"- source_ref: `{ref}`")
        lines.append("")
    return "\n".join(lines)


def _render_traceability_md(
    *,
    preview_body: dict[str, Any],
    export_body: dict[str, Any],
    export_payload: dict[str, Any],
    node_ids: list[str],
    requested_exporters: list[str],
) -> str:
    """Render the provenance trace (headless reproducibility).

    Embeds the EXACT backend request payloads (the CompileRequest JSON), the
    graph hash, the node ids, and the API endpoints exercised — so the bundle is
    reproducible via the API alone, without launching Logic-Loom.

    The graph_slice in the embedded request payloads is summarised (node/edge
    counts) rather than inlined verbatim, to keep the trace readable; the full
    slice is the deterministic ``akms_learn.fixture_graph()`` and the
    source_packet.json artifact carries the resolved nodes/edges.
    """
    prov = export_payload.get("provenance_summary", {})

    def _redacted_body(body: dict[str, Any]) -> dict[str, Any]:
        out = dict(body)
        gs = out.get("graph_slice")
        if isinstance(gs, dict):
            out["graph_slice"] = {
                "__source__": "akms_learn.fixture_graph()",
                "node_count": len(gs.get("nodes", [])),
                "edge_count": len(gs.get("edges", [])),
                "metadata": gs.get("metadata", {}),
            }
        return out

    lines: list[str] = []
    lines.append("# Traceability — plan_4_logic_loom_integration Review Bundle")
    lines.append("")
    lines.append(
        "This bundle was generated through the backend/plugin path Logic-Loom calls "
        "(the FastAPI `/api/learn/*` route layer served by `logic_loom_api.main.api`), "
        "NOT a private helper. The artifacts below are the real output of the "
        "`akms_learn` exporters invoked through that route. The bundle is reproducible "
        "via the API alone, with no Tauri/GUI."
    )
    lines.append("")

    lines.append("## API endpoints exercised")
    lines.append("")
    lines.append("| Learning mode | Endpoint | Produced |")
    lines.append("|---|---|---|")
    lines.append(
        "| `plugin_status` | `GET /api/learn/status` (+ `GET /api/learn/exporters`) | "
        "plugin availability, capabilities, version, `unavailable_capabilities` |"
    )
    lines.append(
        "| `logic_loom_preview` | `POST /api/learn/compile` | "
        "`logic_loom_preview_payload.json` (the markdown preview the UI renders) |"
    )
    lines.append(
        "| `export_flow` | `POST /api/learn/compile` (with `exporters`) | "
        "`generated_preview.html`, `source_packet.json` (read via "
        "`GET /api/learn/artifacts/{id}/{path}`) |"
    )
    lines.append("")

    lines.append("## Provenance (from the compile response `provenance_summary`)")
    lines.append("")
    lines.append(f"- artifact_id: `{export_payload.get('artifact_id')}`")
    lines.append(f"- request_hash: `{prov.get('request_hash')}`")
    lines.append(f"- graph_hash: `{prov.get('graph_hash')}`")
    lines.append(f"- generation_option: `{prov.get('generation_option')}`")
    lines.append(f"- topic: `{prov.get('topic')}`")
    lines.append(f"- node_count: `{prov.get('node_count')}`")
    lines.append(f"- edge_count: `{prov.get('edge_count')}`")
    lines.append("")

    lines.append("## Graph slice node ids (deterministic — `akms_learn.fixture_graph()`)")
    lines.append("")
    for nid in node_ids:
        lines.append(f"- `{nid}`")
    lines.append("")

    lines.append(f"## Requested exporters (export_flow)")
    lines.append("")
    lines.append(f"`{', '.join(requested_exporters) if requested_exporters else '(none available)'}`")
    lines.append("")

    lines.append("## Exact backend request payload — `logic_loom_preview`")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(_redacted_body(preview_body), indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")

    lines.append("## Exact backend request payload — `export_flow`")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(_redacted_body(export_body), indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append(
        "> The `graph_slice` is summarised above; the full slice is the "
        "deterministic `akms_learn.fixture_graph()` and the resolved nodes/edges "
        "are captured in `source_packet.json`. To reproduce: run "
        "`scripts/generate_review_bundle.py`, or POST the payload above (with the "
        "fixture graph_slice expanded) to `POST /api/learn/compile`."
    )
    lines.append("")
    return "\n".join(lines)


def _generator_version() -> str:
    """Read the real installed akms_learn distribution version."""
    try:
        return importlib.metadata.version(GENERATOR_DIST)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directory to write the bundle into. Defaults to "
            "artifacts/review_bundles/plan_4_logic_loom_integration/ under the repo root."
        ),
    )
    args = parser.parse_args(argv)
    output_dir = args.output_dir if args.output_dir is not None else default_output_dir()

    try:
        manifest = generate_bundle(output_dir)
    except GeneratorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Review bundle generated at: {output_dir}")
    print(f"  status:        {manifest['status']}")
    print(f"  generator:     {manifest['generator']} {manifest['generator_version']}")
    print(f"  learning_modes:{manifest['learning_modes_used']}")
    print(f"  artifacts:     {manifest['artifacts']}")
    print(f"  warnings:      {len(manifest['warnings'])}")
    print(f"  unavailable:   {manifest['unavailable_capabilities']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
