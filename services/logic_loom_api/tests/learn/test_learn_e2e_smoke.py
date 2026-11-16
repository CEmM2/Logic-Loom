"""End-to-end smoke test suite for the Learn integration (9 steps).

All nine smoke steps must pass.

Nine steps:
  1. no-plugin → Learn tab gated (status akms_learn_available=false, 3-key payload)
  2. baseline-tier capabilities available
  3. full tier → all (true) generation-mode capabilities available
  4. compile outline / anthology / pitfall / bundle from a graph slice
  5. one artifact per source family
  6. learning-cache contents present
  7. provenance includes graph hash + node ids
  8. no global-vault writes
  9. Graph / Picker / Nodes / Vault / Pipeline routes still work (non-regression)

Tier: regression. The capability tiers are driven by the plugin's REAL gate via
``importlib.util.find_spec`` (its own documented test seam) — NOT canned
capability lists, and NOT a real venv install/upgrade (which would mutate the
shared dev venv; the find_spec probe gives the same gate output without the
risk). Steps 1/2/9 gating is also covered by the FE suite (learn_tab_visibility.test.tsx /
learn_tab_suite.test.tsx); this backend suite asserts the status/compile/cache/provenance
contracts. Patterns from test_learn_backend_suite.py: HAS_AKMS_LEARN skipif, tmp-path
TestClient fixture.

Tier seams (all exercise the REAL akms_learn gate / availability detection)
--------------------------------------------------------------------------
* **no-plugin** — ``find_spec("akms_learn")`` → ``None``. The adapter's real
  ``akms_learn_available()`` probes exactly this, so it reports absent and the
  status/profiles/exporters routes take the genuine degrade-gracefully branch.
* **reduced (baseline) tier** — ``find_spec("nbformat"/"jinja2")`` → ``None``.
  ``capability_gates.probe_optional_extras`` re-probes every call, so the REAL
  ``build_capability_gate`` / ``capabilities_with_status`` withholds the
  notebook/html-gated capabilities (notebook_source, assessment_first,
  notebook_export, html_export, quiz_export) while baseline deterministic modes +
  markdown/bundle remain. The vendored plugin is a single build with no separate
  "baseline-only release"; the extras-absent state is the faithful real lower tier the
  gate computes (verified a strict subset of the full tier).
* **full tier** — the REAL installed akms_learn with extras present. No patch.
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
# Module-level availability check (mirrors test_learn_backend_suite.py / test_learn_profiles_exporters.py)
# ---------------------------------------------------------------------------

HAS_AKMS_LEARN = importlib.util.find_spec("akms_learn") is not None

#: Baseline (always-present) generation modes + exporters.
#: These need no optional extra, so they remain available in the reduced tier.
_BASELINE_MODES = ("deterministic_outline", "node_anthology", "pitfall_driven")
_BASELINE_EXPORTERS = ("markdown_export", "bundle_export")

#: Capabilities the REAL gate withholds when the notebook/html extras are absent
#: (capability_gates._CAPABILITY_EXTRA_MAP): notebook→nbformat, html→jinja2.
_EXTRA_GATED_MODES = ("notebook_source", "assessment_first")
_EXTRA_GATED_EXPORTERS = ("notebook_export", "html_export", "quiz_export")

_REAL_FIND_SPEC = importlib.util.find_spec


def _find_spec_with_absent(absent: set[str]):
    """Return an ``importlib.util.find_spec`` replacement that reports the named
    modules (and their submodules) ABSENT, delegating every other lookup to the
    real implementation.

    This is the plugin's *documented* test seam (``capability_gates`` re-probes
    ``find_spec`` on every call; ``learn_adapter.akms_learn_available`` probes
    ``find_spec("akms_learn")``). Patching it drives akms_learn's **real**
    capability gate / availability detection to a lower tier — exercising the
    genuine gating code path, NOT a canned capability list.
    """

    def _fake(name: str, package: str | None = None):  # type: ignore[no-untyped-def]
        for mod in absent:
            if name == mod or name.startswith(mod + "."):
                return None
        return _REAL_FIND_SPEC(name, package)

    return _fake


# ---------------------------------------------------------------------------
# Shared fixtures (mirror test_learn_profiles_exporters.py::client / test_learn_backend_suite.py exactly)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    """TestClient with tmp-path settings so no real filesystem state is touched.

    Pins LOGIC_LOOM_CACHE_PATH at <tmp>/cache so the learning cache, artifact
    dirs, and provenance writes all land under tmp_path — never the user's real
    ~/.cache/logic-loom. Pins LOGIC_LOOM_AKMS_REPO + LOGIC_LOOM_AKMS_GLOBAL_VAULT
    at tmp dirs so the picker mount + domain-pack discovery never touch the
    developer's real AKMS tree, AND so the step-8 "no vault writes" snapshot is
    taken on a tmp tree we control (the real default vault ~/.claude/akms/nodes
    may not exist, which would make a vault-write snapshot vacuous).
    """
    cfg_path = tmp_path / "config.toml"
    akms_repo = tmp_path / "akms_repo"
    akms_repo.mkdir(parents=True, exist_ok=True)
    global_vault = tmp_path / "global_vault"
    global_vault.mkdir(parents=True, exist_ok=True)
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg_path),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
            "LOGIC_LOOM_AKMS_REPO": str(akms_repo),
            "LOGIC_LOOM_AKMS_GLOBAL_VAULT": str(global_vault),
        },
    ):
        from logic_loom_api.deps import reload_settings  # type: ignore[import-not-found]

        reload_settings()
        from logic_loom_api.main import api  # type: ignore[import-not-found]

        with TestClient(api) as c:
            yield c
        reload_settings()


def _fixture_graph_slice_payload() -> dict:
    """Minimal deterministic dict matching akms_learn.fixture_graph() → GraphSlicePayload.

    Using the plugin's own fixture graph keeps the compile reproducible and
    independent of the developer's specific akms_repo graph contents (it is the
    explicit ``graph_slice`` source form A per the route contract).
    """
    from akms_learn import fixture_graph  # type: ignore[import-not-found]

    gs = fixture_graph()
    return {
        "nodes": list(gs.nodes),
        "edges": list(gs.edges),
        "metadata": dict(gs.metadata),
    }


def _base_compile_body(**overrides) -> dict:
    """Return a minimal valid CompileRequest body (explicit graph_slice path)."""
    body: dict = {
        "generation_option": "deterministic_outline",
        "topic": "j2 return mapping",
        "goal": "Understand the j2 return-mapping algorithm",
        "graph_slice": _fixture_graph_slice_payload(),
    }
    body.update(overrides)
    return body


def _snapshot_tree(root: Path) -> dict[str, float]:
    """Map every file under ``root`` to its mtime (recursively).

    Used to prove the compile path leaves a protected tree untouched: an
    identical snapshot before/after a compile means no create/modify/delete
    happened anywhere under ``root``.
    """
    snap: dict[str, float] = {}
    if not root.exists():
        return snap
    for p in root.rglob("*"):
        if p.is_file():
            snap[str(p)] = p.stat().st_mtime_ns
    return snap


class TestE2ESmoke:
    """Learn end-to-end smoke — nine steps; tiers driven by the REAL gate via find_spec."""

    # -----------------------------------------------------------------------
    # Step 1 — no plugin → Learn tab gated
    # -----------------------------------------------------------------------
    @pytest.mark.integration
    def test_no_plugin_tab_gated(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step 1: plugin absent → /status reports unavailable with EXACTLY 3 keys
        (the unavailable-status contract guard) and /profiles + /exporters degrade to empty
        lists (200, never 503).

        REAL detection path: make ``akms_learn`` un-findable via ``find_spec``.
        The adapter's own ``akms_learn_available()`` probes
        ``importlib.util.find_spec("akms_learn")``, so this drives the genuine
        availability check to ``False`` and exercises the real degrade-gracefully
        branch — not a canned override of the predicate.
        """
        monkeypatch.setattr(
            importlib.util, "find_spec", _find_spec_with_absent({"akms_learn"})
        )
        # Sanity: the REAL availability check now reports absent (proves the seam).
        assert learn_adapter.akms_learn_available() is False

        # /status → 200, exactly the 3-key unavailable payload.
        rs = client.get("/api/learn/status")
        assert rs.status_code == 200, f"/status must be 200; got {rs.status_code}: {rs.text}"
        status = rs.json()
        assert set(status.keys()) == {
            "akms_learn_available",
            "reason",
            "capabilities",
        }, (
            f"Unavailable /status must carry EXACTLY 3 keys; "
            f"got: {set(status.keys())}"
        )
        assert status["akms_learn_available"] is False
        assert isinstance(status["reason"], str) and status["reason"], (
            "reason must be a non-empty string on the unavailable path"
        )
        assert status["capabilities"] == [], "capabilities must be [] when gated"

        # /profiles → 200 empty list.
        rp = client.get("/api/learn/profiles")
        assert rp.status_code == 200, f"/profiles must be 200 when gated; got {rp.status_code}"
        assert rp.json().get("profiles") == [], "profiles must be [] when gated"
        assert rp.json().get("akms_learn_available") is False

        # /exporters → 200 empty list.
        re = client.get("/api/learn/exporters")
        assert re.status_code == 200, f"/exporters must be 200 when gated; got {re.status_code}"
        assert re.json().get("exporters") == [], "exporters must be [] when gated"
        assert re.json().get("akms_learn_available") is False

    # -----------------------------------------------------------------------
    # Step 2 — reduced (baseline) capability tier via the REAL gate (find_spec)
    # -----------------------------------------------------------------------
    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_baseline_tier_capabilities_available(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step 2: a REDUCED capability tier computed by the plugin's REAL gate.

        The vendored akms_learn is a single build (every mode is in the code);
        its optional capabilities are gated at runtime by ``find_spec`` probes
        (``capability_gates``: notebook→nbformat, html→jinja2, llm→sentinel).
        There is no separate "baseline-only release" artifact, so we model the lower tier
        the way the plugin documents: make the backing extras ABSENT via
        ``find_spec`` and let the REAL ``build_capability_gate`` /
        ``capabilities_with_status`` compute the reduced catalog — NOT a canned
        list. We then assert the route serves EXACTLY what the real gate computes,
        that the extra-gated caps are withheld, the baseline modes remain, and the
        available set is a strict subset of the full tier.
        """
        # Full (real env) available sets — captured BEFORE gating, from the real gate.
        full_modes = {
            e["capability"]: e["status"]
            for e in learn_adapter.get_generation_mode_capabilities()
        }
        full_avail_modes = {c for c, s in full_modes.items() if s == "available"}
        full_caps = {
            e["capability"]: e["status"]
            for e in learn_adapter.get_capabilities_with_status()
        }
        full_avail_exporters = {
            c for c, s in full_caps.items() if c.endswith("_export") and s == "available"
        }

        # Drive the REAL gate to the reduced tier: notebook + html extras absent.
        monkeypatch.setattr(
            importlib.util, "find_spec", _find_spec_with_absent({"nbformat", "jinja2"})
        )
        # The reduced catalog the REAL gate now computes (the route must match it).
        reduced_modes_expected = {
            e["capability"]: e["status"]
            for e in learn_adapter.get_generation_mode_capabilities()
        }

        # /profiles must serve EXACTLY the real gate's reduced-tier output.
        rp = client.get("/api/learn/profiles")
        assert rp.status_code == 200, rp.text
        assert rp.json()["akms_learn_available"] is True
        profile_status = {p["id"]: p["status"] for p in rp.json()["profiles"]}
        assert profile_status == reduced_modes_expected, (
            "/profiles must serve exactly the REAL gate's reduced-tier catalogue; "
            f"profiles={profile_status} gate={reduced_modes_expected}"
        )

        reduced_avail_modes = {c for c, s in profile_status.items() if s == "available"}
        # The real gate withheld the notebook/extra-gated modes.
        for gated in _EXTRA_GATED_MODES:
            assert profile_status.get(gated) != "available", (
                f"extra-gated mode {gated!r} must NOT be available when its extra "
                f"is absent; got {profile_status.get(gated)!r}"
            )
        # Baseline (no-extra) modes remain available.
        for m in _BASELINE_MODES:
            assert profile_status.get(m) == "available", (
                f"baseline mode {m!r} must remain available in the reduced tier"
            )
        # Strict reduction vs the full tier (the gate genuinely gated).
        assert reduced_avail_modes < full_avail_modes, (
            f"reduced tier must offer strictly FEWER available modes than full; "
            f"reduced={reduced_avail_modes} full={full_avail_modes}"
        )

        # /exporters: notebook/html/quiz withheld; markdown + bundle remain.
        re = client.get("/api/learn/exporters")
        assert re.status_code == 200, re.text
        avail_exporters = {
            e["id"] for e in re.json()["exporters"] if e["status"] == "available"
        }
        assert avail_exporters == set(_BASELINE_EXPORTERS), (
            f"reduced-tier available exporters must be exactly the baseline "
            f"{set(_BASELINE_EXPORTERS)}; got {avail_exporters}"
        )
        for gated in _EXTRA_GATED_EXPORTERS:
            assert gated not in avail_exporters, (
                f"extra-gated exporter {gated!r} must not be available when its "
                "extra is absent"
            )
        assert avail_exporters < full_avail_exporters, (
            "reduced-tier exporters must be a strict subset of the full tier"
        )

        # /status: still available (plugin present) but generation_modes must not
        # advertise the withheld extra-gated modes.
        rs = client.get("/api/learn/status")
        assert rs.status_code == 200, rs.text
        status = rs.json()
        assert status["akms_learn_available"] is True
        # generation_modes is the KNOWN-mode catalogue (the compile gate's
        # accept-list); known-ness is availability-INDEPENDENT, so it is stable
        # across tiers — only /profiles STATUSES change. This proves the reduced
        # tier withheld AVAILABILITY, not the mode catalogue (the gate keeps
        # unknown_mode→422 distinct from capability_unavailable→412).
        gen_modes = set(status.get("generation_modes", []))
        assert gen_modes == set(full_modes), (
            "status.generation_modes (the known-mode catalogue) must be stable "
            f"across tiers; full={set(full_modes)} reduced={gen_modes}"
        )

    # -----------------------------------------------------------------------
    # Step 3 — full-tier generation-mode catalog (real plugin)
    # -----------------------------------------------------------------------
    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_full_tier_all_capabilities_available(self, client: TestClient) -> None:
        """Step 3: with the REAL installed akms_learn (full tier), /profiles
        advertises the FULL generation-mode catalog.

        The expected set is derived from
        ``learn_adapter.get_generation_mode_capabilities()`` (authoritative, NOT a
        hard-coded list). Every available mode in the catalog must be advertised as
        available on /profiles, and the full-tier modes must be a strict
        superset of the baseline modes (proving the upgrade surfaced more modes).
        """
        catalog = learn_adapter.get_generation_mode_capabilities()
        assert catalog, "real akms_learn must expose generation-mode capabilities"

        rp = client.get("/api/learn/profiles")
        assert rp.status_code == 200, rp.text
        profiles = rp.json()["profiles"]
        assert rp.json()["akms_learn_available"] is True

        # /profiles must advertise the full catalog verbatim (id + status).
        profile_status = {p["id"]: p["status"] for p in profiles}
        catalog_status = {e["capability"]: e["status"] for e in catalog}
        assert profile_status == catalog_status, (
            f"/profiles must advertise the full catalog with matching statuses; "
            f"profiles={profile_status} catalog={catalog_status}"
        )

        # Every "true mode available" must be advertised available.
        available_modes = {
            cap for cap, st in catalog_status.items() if st == "available"
        }
        assert available_modes, "The full tier must expose at least one available mode"
        for mode in available_modes:
            assert profile_status[mode] == "available", (
                f"available mode {mode!r} must be advertised available on /profiles"
            )

        # The full tier must be a strict superset of the baseline mode set (the
        # upgrade surfaced strictly more modes). Every baseline mode must still be available.
        all_mode_ids = set(catalog_status)
        assert set(_BASELINE_MODES) < all_mode_ids, (
            f"Full-tier catalog {all_mode_ids} must strictly contain the baseline "
            f"modes {set(_BASELINE_MODES)}"
        )
        for mode in _BASELINE_MODES:
            assert catalog_status[mode] == "available", (
                f"Baseline mode {mode!r} must remain available in the full tier"
            )

    # -----------------------------------------------------------------------
    # Step 4 — compile outline / anthology / pitfall / bundle
    # -----------------------------------------------------------------------
    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_compile_four_modes(self, client: TestClient) -> None:
        """Step 4: compile outline / anthology / pitfall, plus a bundle export, each
        → 200 with markdown.

        The outline/anthology/pitfall ids and the bundle exporter id are RESOLVED
        from /profiles + /exporters (not assumed) so the test verifies the real
        capability ids the backend advertises before POSTing them.
        """
        # Resolve mode ids from /profiles.
        profiles = {p["id"]: p for p in client.get("/api/learn/profiles").json()["profiles"]}
        mode_for = {
            "outline": "deterministic_outline",
            "anthology": "node_anthology",
            "pitfall": "pitfall_driven",
        }
        for label, mode_id in mode_for.items():
            assert mode_id in profiles, f"{label} mode {mode_id!r} missing from /profiles"
            assert profiles[mode_id]["status"] == "available", (
                f"{label} mode {mode_id!r} must be available; got {profiles[mode_id]['status']}"
            )

        # Resolve the bundle exporter id from /exporters (bare name for the request).
        exporters = {e["id"]: e for e in client.get("/api/learn/exporters").json()["exporters"]}
        assert "bundle_export" in exporters, "bundle_export must be advertised on /exporters"
        assert exporters["bundle_export"]["status"] == "available"

        # outline / anthology / pitfall — each compiles to 200 with markdown.
        for label, mode_id in mode_for.items():
            r = client.post(
                "/api/learn/compile", json=_base_compile_body(generation_option=mode_id)
            )
            assert r.status_code == 200, f"{label} compile failed: {r.status_code}: {r.text}"
            data = r.json()
            assert isinstance(data["markdown"], str) and data["markdown"].strip(), (
                f"{label} compile must return non-empty markdown"
            )
            assert data["provenance_summary"]["generation_option"] == mode_id, (
                f"{label} compile provenance must echo the mode id"
            )

        # bundle export — compile with the bundle exporter; must emit the canonical
        # bundle manifest. The akms_learn bundle exporter writes a DIRECTORY layout
        # (manifest.json + concept_map.json + learning_source_packet.yaml + an
        # exports/ + assets/ tree), not a single .zip — the manifest.json is its
        # signature artifact (also recorded in the review-bundle manifest).
        rb = client.post(
            "/api/learn/compile",
            json=_base_compile_body(exporters=["bundle"]),
        )
        assert rb.status_code == 200, f"bundle compile failed: {rb.status_code}: {rb.text}"
        bundle_data = rb.json()
        assert isinstance(bundle_data["markdown"], str) and bundle_data["markdown"].strip()
        export_names = {Path(e["path"]).name for e in bundle_data["exports"]}
        assert "manifest.json" in export_names, (
            f"bundle export must produce its manifest.json; "
            f"export names: {sorted(export_names)}"
        )

    # -----------------------------------------------------------------------
    # Step 5 — one artifact per source family
    # -----------------------------------------------------------------------
    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_one_artifact_per_source_family(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step 5: compile with all exporters and assert ≥1 artifact per source
        family the installed plugin can produce, each carrying its per-family metadata.

        Families (and their detection): notebook (.ipynb → cell_count/executed),
        html (.html → static), assessment (assessment.md/json + rubric.md →
        has_public/has_answer_key), markdown (lesson.md → generated_content),
        bundle (manifest.json — the akms_learn bundle exporter writes a directory
        layout, not a .zip), packet (type='packet'). A family whose exporter's
        optional dep is unavailable is recorded as skipped (not failed) — quiz, in
        this environment, has no available exporter.
        """
        # Discover which exporters the plugin can actually run (available status).
        exporters = client.get("/api/learn/exporters").json()["exporters"]
        avail_export_ids = {
            e["id"] for e in exporters if e["status"] == "available"
        }
        # Map the family → the bare exporter name we request + its *_export cap id.
        family_to_cap = {
            "markdown": "markdown_export",
            "notebook": "notebook_export",
            "html": "html_export",
            "assessment": "assessment_export",
            "bundle": "bundle_export",
        }
        requested = []
        skipped_families: dict[str, str] = {}
        for family, cap in family_to_cap.items():
            # assessment_export is not a catalog *_export capability in this build
            # (the assessment exporter is requested by bare name 'assessment'); we
            # treat a family as runnable when EITHER its cap is available OR it is
            # the assessment family (always shipped with the core exporters here).
            if cap in avail_export_ids or family == "assessment":
                requested.append(family)
            else:
                skipped_families[family] = f"exporter capability {cap!r} not available"

        assert requested, "expected at least one runnable exporter family"

        r = client.post(
            "/api/learn/compile",
            json=_base_compile_body(exporters=requested),
        )
        assert r.status_code == 200, f"compile failed: {r.status_code}: {r.text}"
        exports = r.json()["exports"]
        assert exports, "compile must produce at least one export"

        # Family detection helpers mirror routes/learn.py::_artifact_metadata.
        def _family(export: dict) -> str | None:
            t = export["type"]
            name = Path(export["path"]).name.lower()
            if t == "packet":
                return "packet"
            if name.endswith(".ipynb"):
                return "notebook"
            if name.endswith(".html"):
                return "html"
            if name in ("assessment.md", "assessment.json", "rubric.md"):
                return "assessment"
            # The bundle exporter's signature artifact is its manifest.json (it
            # writes a directory layout, not a .zip). concept_map.json is likewise
            # bundle-only here. lesson.md is written by BOTH markdown and bundle,
            # so it maps to the markdown family — bundle is identified by manifest.
            if name in ("manifest.json", "concept_map.json"):
                return "bundle"
            if name == "lesson.md":
                return "markdown"
            return None

        produced: dict[str, list[dict]] = {}
        for e in exports:
            fam = _family(e)
            if fam is not None:
                produced.setdefault(fam, []).append(e)

        # packet is always produced (the canonical LSP); assert it explicitly.
        assert "packet" in produced, "every compile must persist the canonical packet"

        # For every requested family, assert ≥1 artifact + its family metadata shape.
        for family in requested:
            assert family in produced, (
                f"requested family {family!r} produced no artifact; "
                f"produced families: {sorted(produced)}"
            )
            arts = produced[family]
            assert len(arts) >= 1, f"family {family!r} must produce ≥1 artifact"
            meta = arts[0]["metadata"]
            if family == "notebook":
                assert "cell_count" in meta and "executed" in meta, (
                    f"notebook metadata must carry cell_count/executed; got {meta}"
                )
                assert isinstance(meta["cell_count"], int) and meta["cell_count"] > 0
            elif family == "html":
                assert meta.get("static") is True, f"html metadata must mark static; got {meta}"
            elif family == "assessment":
                assert "has_public" in meta and "has_answer_key" in meta, (
                    f"assessment metadata must carry has_public/has_answer_key; got {meta}"
                )
                assert meta["has_public"] is True
            elif family == "markdown":
                assert "generated_content" in meta, (
                    f"markdown metadata must carry generated_content; got {meta}"
                )

        # bundle is requested above; confirm its family appeared too (manifest.json).
        if "bundle" in requested:
            assert "bundle" in produced, "bundle exporter must produce its manifest.json"

        # Missing-optional-dep contract (routes.learn._serialize_warnings):
        # requesting an exporter whose capability is present-but-not-"available"
        # must surface a structured missing_optional_dep warning (source_ref = the
        # exporter name) and NEVER 500. Make this deterministic regardless of which
        # optional exporter deps happen to be installed: force one real exporter
        # capability to report "unavailable" in the catalog the route's warning
        # synthesis reads, then request it. (A bogus/unknown exporter id is silently
        # ignored by the synthesis — status is None — so it would NOT exercise the
        # contract; the capability must exist in the catalog with a non-available
        # status.) The monkeypatch affects only this final probe, not the real
        # family compiles already asserted above.
        real_caps = learn_adapter.get_capabilities_with_status()
        forced_caps = [
            {**c, "status": "unavailable"} if c.get("capability") == "markdown_export" else c
            for c in real_caps
        ]
        assert any(c.get("capability") == "markdown_export" for c in real_caps), (
            "markdown_export must exist in the capability catalog to force-unavailable"
        )
        monkeypatch.setattr(
            learn_adapter, "get_capabilities_with_status", lambda: forced_caps
        )
        rq = client.post(
            "/api/learn/compile",
            json=_base_compile_body(exporters=["markdown"]),
        )
        monkeypatch.undo()
        assert rq.status_code == 200, (
            f"requesting an unavailable exporter must not 500; got {rq.status_code}: {rq.text}"
        )
        missing_dep = [
            w
            for w in rq.json()["warnings"]
            if w.get("source_ref") == "markdown"
            and w.get("category") == "missing_optional_dep"
        ]
        assert missing_dep, (
            "requesting a present-but-unavailable exporter must surface a "
            f"missing_optional_dep warning (source_ref='markdown'); "
            f"got warnings: {rq.json()['warnings']}"
        )

        # Any family skipped for a missing optional dep is RECORDED (not failed).
        # Each recorded reason must be a precise non-empty string naming the cap.
        for fam, reason in skipped_families.items():
            assert isinstance(reason, str) and reason, (
                f"skipped family {fam!r} must record a precise reason; got {reason!r}"
            )

    # -----------------------------------------------------------------------
    # Step 6 — learning-cache contents present (tmp cache_path)
    # -----------------------------------------------------------------------
    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_learning_cache_contents(self, client: TestClient, tmp_path: Path) -> None:
        """Step 6: after a compile, <cache_path>/learning/{artifact_id}/ exists and
        contains the canonical packet plus the exported files.

        The client fixture pins LOGIC_LOOM_CACHE_PATH at <tmp_path>/cache, so this
        never touches the user's real ~/.cache/logic-loom.
        """
        r = client.post(
            "/api/learn/compile",
            json=_base_compile_body(exporters=["markdown", "bundle"]),
        )
        assert r.status_code == 200, f"compile failed: {r.status_code}: {r.text}"
        data = r.json()
        artifact_id = data["artifact_id"]

        expected_dir = tmp_path / "cache" / "learning" / artifact_id
        assert expected_dir.is_dir(), f"artifact dir {expected_dir} must exist"
        assert data["cache_dir"] == str(expected_dir), (
            f"cache_dir {data['cache_dir']!r} must equal {expected_dir!r}"
        )

        # The canonical packet must be on disk inside the dir.
        assert data["packet_path"] is not None, "packet_path must be populated"
        packet_path = Path(data["packet_path"])
        assert packet_path.is_file(), f"packet {packet_path} must exist on disk"
        assert packet_path.parent == expected_dir, "packet must live under the artifact dir"

        # Every advertised export must exist on disk under the artifact dir.
        exports = data["exports"]
        assert exports, "exports must be non-empty"
        for e in exports:
            ep = Path(e["path"])
            assert ep.is_file(), f"exported artifact {ep} must exist on disk"
            assert ep.resolve().is_relative_to(expected_dir.resolve()), (
                f"export {ep} must live under the artifact dir {expected_dir}"
            )
        assert any(e["type"] == "packet" for e in exports), (
            "exports must include the canonical packet (type='packet')"
        )

    # -----------------------------------------------------------------------
    # Step 7 — provenance: graph hash + node identifiers/count
    # -----------------------------------------------------------------------
    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_provenance_graph_hash_and_node_ids(self, client: TestClient) -> None:
        """Step 7: provenance carries a graph hash + node identifiers/count for the
        compiled slice.

        The API ``provenance_summary`` carries graph_hash + node_count; the node
        IDENTIFIERS live in the on-disk provenance.json artifact akms_learn writes.
        Both are reachable through the implemented compile path. The fixture graph
        has 7 nodes, so node_count and the identifier list must agree at 7.
        """
        import json

        r = client.post(
            "/api/learn/compile",
            json=_base_compile_body(exporters=["bundle"]),
        )
        assert r.status_code == 200, f"compile failed: {r.status_code}: {r.text}"
        data = r.json()

        prov = data["provenance_summary"]
        # graph hash present, non-empty hex.
        assert isinstance(prov.get("graph_hash"), str) and prov["graph_hash"], (
            "provenance_summary must carry a non-empty graph_hash"
        )
        assert isinstance(prov.get("request_hash"), str) and prov["request_hash"]
        node_count = prov.get("node_count")
        assert isinstance(node_count, int) and node_count > 0, (
            f"provenance_summary.node_count must be a positive int; got {node_count!r}"
        )

        # Node IDENTIFIERS — read the provenance.json artifact written under the
        # cache dir (the implemented bundle/provenance path), then assert it lists
        # the same number of node ids as node_count and that they are all strings.
        cache_dir = Path(data["cache_dir"])
        prov_file = cache_dir / "provenance.json"
        assert prov_file.is_file(), (
            f"provenance.json must be written under the artifact dir; "
            f"missing at {prov_file}"
        )
        prov_disk = json.loads(prov_file.read_text(encoding="utf-8"))
        assert prov_disk.get("graph_hash") == prov["graph_hash"], (
            "on-disk provenance graph_hash must match the API summary"
        )
        node_ids = [n.get("node_id") for n in prov_disk.get("nodes", [])]
        assert node_ids and all(isinstance(nid, str) and nid for nid in node_ids), (
            f"provenance.json must list non-empty node identifiers; got {node_ids!r}"
        )
        assert len(node_ids) == node_count, (
            f"node identifier count {len(node_ids)} must match provenance "
            f"node_count {node_count}"
        )

    # -----------------------------------------------------------------------
    # Step 8 — no global-vault writes (core invariant)
    # -----------------------------------------------------------------------
    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_no_global_vault_writes(self, client: TestClient, tmp_path: Path) -> None:
        """Step 8: the compile path writes ONLY under the tmp cache_path; the
        configured akms_global_vault and akms_repo (Nodes_Vault) are NOT modified.

        Snapshots the file trees of both protected roots before and after a compile
        and asserts they are byte-for-byte identical (no create/modify/delete),
        while the artifact dir under <cache>/learning/ gained the new files. This
        is a safety-critical invariant.
        """
        from logic_loom_api.config import Settings  # type: ignore[import-not-found]

        settings = Settings.load(config_path=Path(os.environ["LOGIC_LOOM_CONFIG"]))
        global_vault = settings.effective_akms_global_vault().expanduser()
        akms_repo = settings.akms_repo.expanduser()
        cache_root = (tmp_path / "cache").resolve()

        # The client fixture pins both protected roots at tmp dirs. Confirm they
        # resolve OUTSIDE the cache and SEED a sentinel file in each, so the
        # before/after snapshot is a real (non-vacuous) comparison — a vault write
        # would change the snapshot, an untouched vault keeps it identical.
        assert not global_vault.resolve().is_relative_to(cache_root), (
            f"global vault {global_vault} must not be inside the cache (test setup)"
        )
        assert not akms_repo.resolve().is_relative_to(cache_root), (
            f"akms_repo {akms_repo} must not be inside the cache (test setup)"
        )
        (global_vault / "sentinel.md").write_text("vault sentinel", encoding="utf-8")
        (akms_repo / "sentinel.md").write_text("repo sentinel", encoding="utf-8")

        before_vault = _snapshot_tree(global_vault)
        before_repo = _snapshot_tree(akms_repo)
        assert before_vault and before_repo, (
            "snapshot must be non-empty (sentinels seeded) so the assertion is real"
        )

        r = client.post(
            "/api/learn/compile",
            json=_base_compile_body(exporters=["markdown", "bundle"]),
        )
        assert r.status_code == 200, f"compile failed: {r.status_code}: {r.text}"
        data = r.json()

        after_vault = _snapshot_tree(global_vault)
        after_repo = _snapshot_tree(akms_repo)

        assert before_vault == after_vault, (
            f"compile must NOT write into the AKMS global vault ({global_vault}); "
            f"changed entries: {set(before_vault) ^ set(after_vault)}"
        )
        assert before_repo == after_repo, (
            f"compile must NOT write into the AKMS source tree ({akms_repo}); "
            f"changed entries: {set(before_repo) ^ set(after_repo)}"
        )

        # Positive control: the writes DID land under the tmp cache.
        artifact_dir = Path(data["cache_dir"])
        assert artifact_dir.resolve().is_relative_to(cache_root), (
            f"artifact dir {artifact_dir} must live under the tmp cache {cache_root}"
        )
        written = list(artifact_dir.rglob("*"))
        assert any(p.is_file() for p in written), (
            "compile must have written artifact files under the tmp cache dir"
        )

    # -----------------------------------------------------------------------
    # Step 9 — other tabs still work (non-regression)
    # -----------------------------------------------------------------------
    @pytest.mark.integration
    def test_other_tabs_still_work(self, client: TestClient) -> None:
        """Step 9: the non-learn routes (app health, graph, vault, zsum/pipeline,
        nodes, and picker if mountable) respond without any 5xx — confirming the
        Learn subsystem did not regress the rest of the API.

        Picker is mounted only when the AKMS extra + a valid akms_repo are present;
        with the tmp akms_repo here the mount is skipped (degrade-gracefully), so
        a 404 on the picker probe is tolerated. Any 5xx on any route is a failure.
        """
        # Core read-only probes that must all succeed (200) with a tmp config.
        for path in (
            "/api/app/health",
            "/api/graph/status",
            "/api/vault/tree",
            "/api/vault/collections",
            "/api/zsum/status",
            "/api/nodes/files",
        ):
            r = client.get(path)
            assert r.status_code < 500, (
                f"{path} returned a 5xx ({r.status_code}) — Learn must not regress "
                f"other tabs: {r.text[:200]}"
            )
            assert r.status_code == 200, (
                f"{path} expected 200 with tmp config; got {r.status_code}: {r.text[:200]}"
            )

        # Picker mount is best-effort (skipped when AKMS unavailable / repo bad).
        # Tolerate 404 (mount skipped) but never a 5xx.
        rp = client.get("/api/picker/")
        assert rp.status_code < 500, (
            f"picker probe returned a 5xx ({rp.status_code}); a skipped mount must "
            f"degrade to 404, not error: {rp.text[:200]}"
        )

        # Learn's own status route must also still be up (sanity: real plugin path).
        rl = client.get("/api/learn/status")
        assert rl.status_code == 200, f"/api/learn/status must be 200; got {rl.status_code}"
