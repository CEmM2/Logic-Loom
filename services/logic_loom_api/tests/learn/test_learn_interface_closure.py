"""Tests for interface-support closure (no reimplementation, reproducible).

Interface-support behaviours (referenced below as behaviour 1-4):
  1. Logic-Loom calls the SAME backend request model as CLI/API.
  2. No reimplementation of graph traversal / learning ordering / section
     extraction / LSP construction / validation / exporter semantics / adapter
     execution / closure-gate validation in Logic-Loom.
  3. Bundle embeds the backend request payload or a stable path to it.
  4. Bundle is reproducible + validatable via CLI/API WITHOUT launching Logic-Loom.

Tier: regression. A mix of audit (assert the route/adapter layer delegates to
``akms_learn`` and reimplements none of the listed concerns) + a headless
reproducibility test (regenerate the bundle through the API/adapter path with
no Tauri/GUI and assert it embeds the request payload + matches the committed
deterministic artifacts).

Interface boundary applied (the load-bearing nuance)
---------------------------------------------------
The interface-support policy prohibits Logic-Loom from reimplementing the **learning compiler's**
concerns: graph-traversal *of the resolved slice*, learning ordering, section
extraction, LSP construction, LSP validation, exporter semantics, adapter
execution, and closure-gate (packet/bundle) validation. ALL of those live in
``akms_learn.compiler`` (``order_nodes`` / ``extract_sections`` /
``validate_packet`` / ``load_graph`` / ``compute_graph_hash`` / ``KNOWN_EXPORTERS``)
and are invoked *inside* ``compile_learning_source`` — the single entrypoint the
adapter delegates to.

Logic-Loom legitimately owns **graph SELECTION** — node-depth ego-slicing of ITS
OWN compiled graph (``graph_adapter.ego_slice``) to turn a ``(node, max_depth,
direction)`` selection into a concrete ``GraphSlice`` before delegating. That is
NOT the compiler's graph traversal (the compiler does no node-rooted expansion;
it only seed-tag/budget-filters an already-resolved slice). So the audit asserts
the prohibited *compiler* symbols never appear as call sites in the route/adapter
layer, while explicitly allowing ``ego_slice`` selection.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Make the review-bundle generator script importable (it lives under <repo>/scripts/).
# ---------------------------------------------------------------------------

# tests/learn/ -> tests -> logic_loom_api -> services -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import generate_review_bundle as grb  # noqa: E402
from logic_loom_api.adapters import learn_adapter  # noqa: E402
from logic_loom_api.routes import learn as learn_route  # noqa: E402

HAS_AKMS_LEARN = importlib.util.find_spec("akms_learn") is not None

#: The committed bundle directory (the closure deliverable).
_COMMITTED_BUNDLE_DIR = grb.default_output_dir()

_REQUIRED_ARTIFACTS = grb.REQUIRED_ARTIFACTS
_MANIFEST = "manifest.json"


def _norm_newlines(text: str) -> str:
    """Normalise CRLF/CR → LF so reproducibility comparisons are platform-robust.
    The committed bundle is LF (generated on a POSIX host); a Windows regenerate
    yields CRLF from the OS-level text write. Line ending style is not part of the
    reproducibility contract, so deterministic-artifact comparisons normalise it."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ---------------------------------------------------------------------------
# Source-level helpers — used by the no-reimplementation audit.
# ---------------------------------------------------------------------------


def _collect_called_names(module_obj: object) -> set[str]:
    """Return every ``foo`` / ``obj.foo`` *call-site* name in a module's source.

    Parses the module's source with ``ast`` and walks ``ast.Call`` nodes,
    collecting the simple/attribute name being called. This targets REAL call
    delegation (not comments/docstrings/strings) so the audit does not
    false-positive on prose that merely mentions ``order_nodes`` etc. in a
    docstring (which is exactly the brittleness the task warns against).
    """
    source = inspect.getsource(module_obj)  # type: ignore[arg-type]
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _collect_imported_names(module_obj: object) -> set[str]:
    """Return every name imported FROM ``akms_learn*`` in a module's source.

    Collects the bound names of ``from akms_learn... import X`` statements
    (including aliases' original names) so the audit can assert the adapter
    binds to the canonical plugin entrypoints rather than re-deriving them.
    """
    source = inspect.getsource(module_obj)  # type: ignore[arg-type]
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "akms_learn" or mod.startswith("akms_learn."):
                for alias in node.names:
                    names.add(alias.name)
    return names


class TestInterfaceClosure:
    # =======================================================================
    # Behaviour 1 — Logic-Loom calls the SAME backend request model as the
    # CLI/API/plugin. The route CompileRequest is translated by the adapter
    # into the plugin's canonical akms_learn.LearningRequest + GraphSlice and
    # delegated to akms_learn.compile_learning_source, whose result is the
    # plugin's CompileResult carrying a LearningSourcePacket — not a forked
    # parallel model.
    # =======================================================================
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_same_backend_request_model(self) -> None:
        """The compile path builds the CANONICAL akms_learn request/result types.

        Asserts against the REAL akms_learn symbols (verified to exist), not
        invented ones:

        * ``learn_adapter.build_learning_request`` returns an instance of the
          plugin's own ``akms_learn.LearningRequest`` — the same request model
          the plugin's CLI/API accept.
        * The route's ``CompileRequest`` fields are a SUBSET of (i.e. map onto)
          ``LearningRequest`` fields — no field the route forwards is foreign to
          the canonical model (the route adds only graph-SELECTION fields —
          ``node`` / ``max_depth`` / ``direction`` / ``graph_slice`` — which are
          Logic-Loom's own concern, not request-model fields).
        * The explicit-slice path constructs the plugin's ``akms_learn.GraphSlice``.
        * A real compile through the adapter returns the plugin's
          ``CompileResult`` whose ``packet`` is the plugin's
          ``LearningSourcePacket`` — the canonical compile output, not a wrapper
          of our own.
        """
        from akms_learn import (
            CompileResult,
            GraphSlice,
            LearningRequest,
            LearningSourcePacket,
            fixture_graph,
        )

        # --- 1a. The adapter builds the plugin's OWN LearningRequest model. ---
        req = learn_adapter.build_learning_request(
            topic="j2 return mapping algorithm",
            goal="Understand the j2 return-mapping algorithm",
            generation_option="deterministic_outline",
        )
        assert isinstance(req, LearningRequest), (
            "build_learning_request must construct the canonical "
            "akms_learn.LearningRequest — the SAME request model the plugin's "
            "CLI/API accept, not a Logic-Loom-local clone"
        )

        # --- 1b. Every LearningRequest field the route forwards is a real field
        # of the canonical model (no forked/foreign request field). ---
        lr_fields = set(LearningRequest.model_fields)
        # The route's CompileRequest fields that feed the LearningRequest (the
        # rest — graph_slice/node/max_depth/direction — are graph SELECTION,
        # owned by Logic-Loom, and are intentionally NOT request-model fields).
        forwarded = {
            "generation_option",
            "topic",
            "goal",
            "audience",
            "depth",
            "seed_tags",
            "exporters",
        }
        compile_req_fields = set(learn_route.CompileRequest.model_fields)
        assert forwarded <= compile_req_fields, (
            "the route CompileRequest must declare every field it forwards to "
            f"the backend; missing {forwarded - compile_req_fields}"
        )
        assert forwarded <= lr_fields, (
            "every CompileRequest field forwarded to the backend must be a real "
            f"akms_learn.LearningRequest field; foreign fields: {forwarded - lr_fields}"
        )

        # --- 1c. The explicit-slice path constructs the plugin's GraphSlice. ---
        gs = fixture_graph()
        assert isinstance(gs, GraphSlice)
        slice_payload = {
            "nodes": list(gs.nodes),
            "edges": list(gs.edges),
            "metadata": dict(gs.metadata),
        }

        # --- 1d. A real compile returns the canonical CompileResult /
        # LearningSourcePacket — the plugin's compile output, not ours. ---
        outcome = learn_adapter.compile(
            topic="j2 return mapping algorithm",
            goal="Understand the j2 return-mapping algorithm",
            generation_option="deterministic_outline",
            graph_slice=slice_payload,
        )
        assert isinstance(outcome.result, CompileResult), (
            "adapter.compile must return the plugin's canonical CompileResult"
        )
        assert isinstance(outcome.result.packet, LearningSourcePacket), (
            "the compile output must be the plugin's LearningSourcePacket — the "
            "canonical compile-result model used by CLI/API, not a fork"
        )

    # =======================================================================
    # Behaviour 2 — no reimplementation of the learning compiler's concerns.
    # The adapter BINDS to the canonical akms_learn entrypoints and the
    # route/adapter source contains NO call site for the prohibited compiler
    # symbols (ordering / section-extraction / LSP validation / exporter
    # registry / closure validation). ego_slice (graph SELECTION) is allowed.
    # =======================================================================
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_no_reimplementation_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The adapter delegates compile/validation/closure/exporter to akms_learn.

        Three complementary checks:

        (a) **Call-site delegation** — the adapter actually CALLS the canonical
            plugin entrypoints (``compile_learning_source``, ``load_graph``,
            ``GraphSlice``, ``LearningRequest``). These are verified to exist on
            ``akms_learn`` (probed below) — no invented symbol.

        (b) **Source-level guard** — the prohibited learning-COMPILER symbols
            (the most regression-prone concerns) never appear as call sites in
            either ``routes/learn.py`` or ``adapters/learn_adapter.py``. The
            check parses ``ast`` and inspects only ``ast.Call`` nodes, so a
            docstring/comment mentioning ``order_nodes`` does NOT trip it — it
            targets real delegation, not prose.

        (c) **Runtime delegation proof** — a static name audit cannot catch
            compiler logic hand-inlined under a *different* name. This closes
            that gap: spy ``akms_learn.compile_learning_source`` and assert a real
            ``learn_adapter.compile(...)`` routes THROUGH it (the packet is the
            plugin's output, not a local reimplementation).
        """
        import akms_learn

        # The canonical compiler-owned symbols (verified to EXIST on the plugin).
        # These live in akms_learn.compiler and are invoked INSIDE
        # compile_learning_source — they are the plugin's job, never ours.
        from akms_learn.compiler import (  # noqa: F401  (existence probe)
            compile_learning_source,
            compute_graph_hash,
            extract_sections,
            load_graph,
            validate_packet,
        )
        from akms_learn.exporters import KNOWN_EXPORTERS  # noqa: F401

        # akms_learn extracted node ordering into a dedicated strategy
        # registry: ``order_nodes`` now lives in ``akms_learn.ordering`` and
        # ``compile_learning_source`` dispatches ordering through
        # ``get_strategy`` (which delegates to ``order_nodes`` for the default
        # strategy). Probe both at their new home so the audit still proves the
        # plugin owns ordering after the refactor.
        from akms_learn.ordering import (  # noqa: F401  (existence probe)
            get_strategy,
            order_nodes,
        )
        from akms_learn.validation import (  # noqa: F401
            validate_packet as _validate_packet_validation,
        )

        # Sanity: compile_learning_source really does own the prohibited concerns
        # internally (so delegating to it == not reimplementing them here).
        compiler_src = inspect.getsource(akms_learn.compiler.compile_learning_source)
        # ``get_strategy`` is the ordering entrypoint compile_learning_source now
        # invokes; it replaces the former direct ``order_nodes``
        # call so this stays a real invocation check, not a comment match.
        for owned in ("get_strategy", "extract_sections", "validate_packet"):
            assert owned in compiler_src, (
                f"precondition: akms_learn.compile_learning_source must invoke "
                f"{owned} (the compiler owns it); got drift in the plugin"
            )

        # --- (a) the adapter binds to + calls the canonical entrypoints ---
        adapter_imports = _collect_imported_names(learn_adapter)
        assert "compile_learning_source" in adapter_imports, (
            "learn_adapter must import the canonical compile entrypoint "
            "akms_learn.compile_learning_source (delegation, not reimplementation)"
        )
        assert {"load_graph", "GraphSlice"} <= adapter_imports, (
            "learn_adapter must resolve graph slices via akms_learn.load_graph / "
            "GraphSlice — no traversal logic re-derived here"
        )
        assert "LearningRequest" in adapter_imports, (
            "learn_adapter must build the canonical akms_learn.LearningRequest"
        )

        adapter_calls = _collect_called_names(learn_adapter)
        assert "compile_learning_source" in adapter_calls, (
            "learn_adapter must CALL compile_learning_source (the single compile "
            "delegation seam), not merely import it"
        )
        # The compiler runs validation/closure inside compile_learning_source, so
        # delegating to it covers LSP validation + closure-gate validation too.

        # --- (b) source-level guard: prohibited COMPILER symbols are never
        # called in the route/adapter layer (they are akms_learn's job). ---
        prohibited_compiler_calls = {
            "order_nodes",  # learning ordering
            "extract_sections",  # section extraction
            "validate_packet",  # LSP validation + closure-gate validation
        }
        for mod, label in (
            (learn_route, "routes/learn.py"),
            (learn_adapter, "adapters/learn_adapter.py"),
        ):
            calls = _collect_called_names(mod)
            leaked = prohibited_compiler_calls & calls
            assert not leaked, (
                f"{label} must NOT reimplement compiler concerns; found call "
                f"site(s) for plugin-owned symbol(s): {sorted(leaked)}. These "
                "belong to akms_learn.compiler and must only be invoked inside "
                "compile_learning_source."
            )

        # The route must NOT build its own exporter registry / iterate
        # KNOWN_EXPORTERS to drive exporter SEMANTICS — exporter availability is
        # surfaced as DATA via capabilities_with_status, and exporting is done by
        # the plugin during compile. Assert the registry constant is not imported
        # into the route/adapter layer (it would only be needed to reimplement
        # exporter dispatch).
        route_imports = _collect_imported_names(learn_route)
        assert "KNOWN_EXPORTERS" not in route_imports, (
            "routes/learn.py must not import the exporter registry "
            "(KNOWN_EXPORTERS) — exporter semantics are owned by akms_learn"
        )
        assert "KNOWN_EXPORTERS" not in adapter_imports, (
            "learn_adapter must not import the exporter registry "
            "(KNOWN_EXPORTERS) — exporter semantics are owned by akms_learn"
        )

        # --- Interface boundary: graph SELECTION (ego_slice) IS Logic-Loom's job
        # and is legitimately present in the route. Assert it is wired (so the
        # guard above is not accidentally banning the allowed concern). ---
        route_calls = _collect_called_names(learn_route)
        assert "ego_slice" in route_calls, (
            "routes/learn.py is expected to call graph_adapter.ego_slice — "
            "node-depth selection of Logic-Loom's OWN graph is allowed (the policy "
            "prohibits the COMPILER's slice traversal, not graph selection)"
        )

        # --- (c) RUNTIME delegation proof ---------------------------------------
        # A static call-site NAME audit cannot catch compiler logic hand-inlined
        # under a different name. Close that gap by proving the real compile path
        # routes THROUGH the canonical plugin entrypoint: spy
        # akms_learn.compile_learning_source (the adapter does a function-local
        # `from akms_learn import compile_learning_source`, so patching the module
        # attribute is exactly what that import resolves at call time), run a real
        # adapter.compile against the fixture slice, and assert the spy was hit and
        # returned the plugin's own packet. If the adapter had reimplemented the
        # compile inline, this entrypoint would never be invoked.
        real_compile = akms_learn.compile_learning_source
        spy_state = {"hit": False}

        def _compile_spy(*args, **kwargs):  # type: ignore[no-untyped-def]
            spy_state["hit"] = True
            return real_compile(*args, **kwargs)

        monkeypatch.setattr(akms_learn, "compile_learning_source", _compile_spy)
        gs = akms_learn.fixture_graph()
        outcome = learn_adapter.compile(
            topic="j2 return mapping algorithm",
            goal="Understand the j2 return-mapping algorithm",
            generation_option="deterministic_outline",
            graph_slice={
                "nodes": list(gs.nodes),
                "edges": list(gs.edges),
                "metadata": dict(gs.metadata),
            },
        )
        assert spy_state["hit"], (
            "adapter.compile must route THROUGH akms_learn.compile_learning_source "
            "at runtime — proves the compile is delegated to the plugin, not "
            "reimplemented inline under another name"
        )
        assert outcome.result.packet is not None, (
            "the delegated compile must yield the plugin's LearningSourcePacket"
        )

    # =======================================================================
    # Behaviours 3 + 4 — the bundle embeds the backend request payload AND
    # is reproducible/validatable headlessly via the API/CLI generator path
    # with NO Tauri/GUI. We regenerate via the review-bundle generator entrypoint into a
    # tmp dir, validate the manifest + artifacts, assert the exact CompileRequest
    # payload is embedded in traceability.md, and assert the deterministic
    # artifacts (HTML) match the committed bundle to prove reproducibility.
    # =======================================================================
    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_bundle_reproducible_headless(self, tmp_path: Path) -> None:
        """Regenerate the bundle headlessly (no GUI); it embeds the request payload
        and reproduces the committed deterministic artifacts (behaviours 3+4).

        Steps:
          1. Drive the review-bundle generator entrypoint (``generate_review_bundle.generate_bundle``)
             into a tmp dir. This path is a pure API/CLI flow — a ``TestClient``
             over ``logic_loom_api.main.api``, NO Tauri/GUI — so success proves
             the bundle is generatable + reproducible WITHOUT launching
             Logic-Loom (behaviour 4).
          2. Assert the regenerated bundle VALIDATES: manifest has the required
             fields + status ``review_bundle_generated``, and all six artifacts
             + manifest are present and non-empty.
          3. Assert the bundle EMBEDS the backend request payload (behaviour 3):
             ``traceability.md`` carries the exact ``CompileRequest`` JSON
             (``generation_option`` / ``topic`` / ``goal`` of the deterministic
             request) plus the graph hash and the compile endpoint.
          4. Assert REPRODUCIBILITY against the committed deliverable: the
             deterministic ``generated_preview.html`` and ``feedback_form.md``
             match the committed bundle byte-for-byte (identical fixture inputs →
             identical exporter output), and the embedded request payload in the
             regenerated ``traceability.md`` matches the committed one.
        """
        # --- Step 1: headless regeneration via the generator entrypoint ---
        out = tmp_path / "regen_bundle"
        manifest = grb.generate_bundle(out)

        # --- Step 2: the regenerated bundle validates ---
        assert manifest["status"] == grb.BUNDLE_STATUS == "review_bundle_generated"
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
        assert required_keys <= set(manifest), (
            f"regenerated manifest missing fields: {required_keys - set(manifest)}"
        )
        assert set(manifest["learning_modes_used"]) == {
            "logic_loom_preview",
            "plugin_status",
            "export_flow",
        }
        # The command field records the API/CLI path used (the bundle
        # records HOW it was generated, so it can be reproduced via CLI/API).
        assert "/api/learn/compile" in manifest["command"], (
            "manifest.command must record the API path so the bundle is "
            "reproducible via CLI/API without Logic-Loom"
        )
        for name in (*_REQUIRED_ARTIFACTS, _MANIFEST):
            f = out / name
            assert f.is_file(), f"regenerated bundle missing {name}"
            assert f.stat().st_size > 0, f"regenerated artifact empty: {name}"

        # --- Step 3: the bundle EMBEDS the backend request payload (behaviour 3) ---
        regen_trace = (out / "traceability.md").read_text("utf-8")
        # The exact CompileRequest fields of the deterministic request.
        assert grb.COMPILE_MODE in regen_trace, (
            "traceability.md must embed the exact generation_option from the "
            f"backend CompileRequest payload ({grb.COMPILE_MODE!r})"
        )
        assert grb.COMPILE_TOPIC in regen_trace, (
            "traceability.md must embed the exact topic from the CompileRequest"
        )
        assert grb.COMPILE_GOAL in regen_trace
        assert '"generation_option"' in regen_trace, (
            "traceability.md must embed the CompileRequest payload as JSON"
        )
        # The endpoint + provenance hash that make it API-reproducible.
        assert "POST /api/learn/compile" in regen_trace
        assert "graph_hash" in regen_trace

        # The preview payload artifact is the compile RESPONSE (proves the
        # payload came from POST /api/learn/compile, not a fabricated dict).
        regen_preview = json.loads((out / "logic_loom_preview_payload.json").read_text("utf-8"))
        for key in ("artifact_id", "markdown", "warnings", "provenance_summary"):
            assert key in regen_preview, (
                f"preview payload must be the compile RESPONSE shape (missing {key!r})"
            )

        # --- Step 4: reproducibility against the committed deliverable ---
        if not _COMMITTED_BUNDLE_DIR.is_dir():  # pragma: no cover - committed with the generator
            pytest.skip("committed bundle not present (generate it with scripts/generate_review_bundle.py)")

        committed_html = (_COMMITTED_BUNDLE_DIR / "generated_preview.html").read_text("utf-8")
        regen_html = (out / "generated_preview.html").read_text("utf-8")
        assert _norm_newlines(regen_html) == _norm_newlines(committed_html), (
            "generated_preview.html must reproduce byte-for-byte from the "
            "deterministic fixture inputs — proves the committed bundle is "
            "reproducible via the API/CLI path (the exporter output is "
            "deterministic for identical inputs; compared modulo platform line endings)"
        )

        committed_feedback = (_COMMITTED_BUNDLE_DIR / "feedback_form.md").read_text("utf-8")
        regen_feedback = (out / "feedback_form.md").read_text("utf-8")
        assert _norm_newlines(regen_feedback) == _norm_newlines(committed_feedback), (
            "feedback_form.md (the verbatim plan seed) must reproduce exactly"
        )

        # The embedded request payload must match the committed bundle's — same
        # backend request → same recorded payload (reproducible provenance).
        committed_trace = (_COMMITTED_BUNDLE_DIR / "traceability.md").read_text("utf-8")

        def _payload_blocks(trace: str) -> list[str]:
            """Extract the ```json fenced CompileRequest payload blocks
            (line-ending normalised so the comparison is platform-robust)."""
            blocks: list[str] = []
            parts = _norm_newlines(trace).split("```json")
            for part in parts[1:]:
                blocks.append(part.split("```", 1)[0].strip())
            return blocks

        regen_blocks = _payload_blocks(regen_trace)
        committed_blocks = _payload_blocks(committed_trace)
        assert regen_blocks and regen_blocks == committed_blocks, (
            "the embedded backend CompileRequest payload(s) in traceability.md "
            "must reproduce exactly from the deterministic request — proving the "
            "bundle records a stable, reproducible backend request payload (behaviour 3)"
        )
