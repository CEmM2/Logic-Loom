"""Tests for /profiles and /exporters (all profiles, optional-dep aware).

Covers: all profiles are surfaced; exporter availability reflects installed
optional deps.

Cases:
  1. test_all_profiles_returned  — /profiles surfaces exactly the true generation-mode
     capabilities (the akms_learn catalog), not the full non-export set.  Uses
     learn_adapter.get_generation_mode_capabilities() as the authoritative expected
     set, matching the narrowed route filter exactly.
  2. test_profiles_excludes_adapters_and_infra  — guards against re-introducing
     over-inclusion: ADAPTER_CAPABILITIES and infra/metadata caps must never appear
     as profiles.
  3. test_exporter_availability_reflects_deps  — /exporters surfaces every *_export
     capability with its availability status; bundle/markdown report available;
     any unavailable-extra exporter reports a non-available status.

Plugin-absence path: both endpoints return 200 + empty list (degrade-gracefully).

Patterns reused from test_learn_backend_suite.py:
  - HAS_AKMS_LEARN / skipif idiom
  - tmp-path TestClient fixture (patch.dict env + reload_settings + TestClient)
  - monkeypatch learn_adapter.akms_learn_available for absence simulation
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
# Shared fixture (mirrors test_learn_backend_suite.py::client exactly)
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


# ---------------------------------------------------------------------------
# Main task test class
# ---------------------------------------------------------------------------


class TestProfilesAndExporters:
    """Tests for /profiles and /exporters."""

    # -----------------------------------------------------------------------
    # Case 1: all profiles returned (non-export capabilities, with status)
    # -----------------------------------------------------------------------

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_all_profiles_returned(self, client: TestClient) -> None:
        """Verifies: GET /api/learn/profiles surfaces exactly the true generation-mode
        capabilities — the 12 user-selectable modes in the akms_learn catalog — and nothing more.

        Uses learn_adapter.get_generation_mode_capabilities() as the authoritative
        expected set (same derivation the route uses), so this test will never
        hard-code capability names and will stay in sync with catalog changes.

        Assertions:
        - 200 response
        - akms_learn_available=True
        - profiles is a non-empty list
        - count and membership match get_generation_mode_capabilities() exactly
        - every profile entry has id + status + label
        - no *_export capability leaks into profiles
        """
        r = client.get("/api/learn/profiles")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

        data = r.json()
        assert data.get("akms_learn_available") is True, (
            "akms_learn_available must be True when plugin is installed"
        )

        profiles = data.get("profiles")
        assert isinstance(profiles, list) and profiles, (
            "profiles must be a non-empty list when akms_learn is installed"
        )

        # Derive expected profiles using the SAME filter as the route — generation
        # modes only (no adapters, no infra, no exporters).
        expected_caps = learn_adapter.get_generation_mode_capabilities()
        assert len(expected_caps) > 0, "Plugin must expose at least one generation-mode capability"

        # Count must match the catalog exactly (no hard-coding, no silent omissions)
        assert len(profiles) == len(expected_caps), (
            f"profiles count {len(profiles)} does not match generation-mode catalog "
            f"count {len(expected_caps)}; "
            f"profiles ids: {[p['id'] for p in profiles]}, "
            f"expected: {[e['capability'] for e in expected_caps]}"
        )

        # Every expected generation-mode cap must be present
        profile_ids = {p["id"] for p in profiles}
        for entry in expected_caps:
            cap = entry["capability"]
            assert cap in profile_ids, (
                f"Generation-mode capability '{cap}' missing from /profiles response"
            )

        for profile in profiles:
            assert "id" in profile, f"Profile missing 'id': {profile!r}"
            assert "status" in profile, f"Profile missing 'status': {profile!r}"
            assert "label" in profile, f"Profile missing 'label': {profile!r}"
            assert profile["status"] in ("available", "unavailable", "planned"), (
                f"Profile status must be one of available/unavailable/planned, "
                f"got: {profile['status']!r}"
            )

        # No *_export capability should leak into profiles
        export_leaks = [p["id"] for p in profiles if p["id"].endswith("_export")]
        assert not export_leaks, (
            f"Exporter capabilities must not appear in /profiles: {export_leaks}"
        )

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_profiles_excludes_adapters_and_infra(self, client: TestClient) -> None:
        """Guards against re-introducing over-inclusion in /profiles.

        The four execution-backend adapters (ADAPTER_CAPABILITIES) and the five
        infra/metadata capabilities must never appear as user-selectable profiles.
        This test imports the canonical sets from the adapter module so it stays
        in sync with upstream catalog changes rather than hard-coding names.

        Assertions:
        - ADAPTER_CAPABILITIES names are absent from /profiles
        - infra/metadata capability names (_INFRA_CAPABILITIES) are absent
        """
        from akms_learn.capabilities_catalog import ADAPTER_CAPABILITIES
        from logic_loom_api.adapters.learn_adapter import _INFRA_CAPABILITIES

        r = client.get("/api/learn/profiles")
        assert r.status_code == 200

        profiles = r.json().get("profiles", [])
        profile_ids = {p["id"] for p in profiles}

        # Execution-backend adapters must not be surfaced as user-selectable modes
        adapter_leaks = [cap for cap in ADAPTER_CAPABILITIES if cap in profile_ids]
        assert not adapter_leaks, (
            f"Adapter capabilities must not appear in /profiles: {adapter_leaks}"
        )

        # Infrastructure/metadata capabilities must not be surfaced as modes
        infra_leaks = [cap for cap in _INFRA_CAPABILITIES if cap in profile_ids]
        assert not infra_leaks, (
            f"Infra/metadata capabilities must not appear in /profiles: {infra_leaks}"
        )

    @pytest.mark.integration
    def test_profiles_returns_200_empty_when_plugin_absent(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Degrade-gracefully: /profiles returns 200 + empty list when plugin absent."""
        monkeypatch.setattr(learn_adapter, "akms_learn_available", lambda: False)

        r = client.get("/api/learn/profiles")
        assert r.status_code == 200, (
            f"profiles must return 200 (not 503) when plugin absent; got {r.status_code}"
        )
        data = r.json()
        assert data.get("profiles") == [], (
            "profiles must be [] when plugin absent"
        )
        assert data.get("akms_learn_available") is False

    # -----------------------------------------------------------------------
    # Case 2: exporter availability reflects optional dep status
    # -----------------------------------------------------------------------

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_exporter_availability_reflects_deps(self, client: TestClient) -> None:
        """Verifies: GET /api/learn/exporters marks each exporter
        available/unavailable per installed optional dependency (notebook/html).
        Requirement: exporter availability reflects installed optional deps. Passes when:
        bundle/markdown report available; optional-dep-gated exporters report
        their actual status (not all forced to available).

        Assertions:
        - 200 response
        - akms_learn_available=True
        - exporters is a non-empty list
        - every *_export capability in capabilities_with_status() is present
        - count matches (no hard-coding)
        - each exporter has id + status + label
        - bundle_export and markdown_export are always available (baseline exporters)
        - statuses come from the catalog, not hard-coded
        """
        r = client.get("/api/learn/exporters")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

        data = r.json()
        assert data.get("akms_learn_available") is True, (
            "akms_learn_available must be True when plugin is installed"
        )

        exporters = data.get("exporters")
        assert isinstance(exporters, list) and exporters, (
            "exporters must be a non-empty list when akms_learn is installed"
        )

        # Derive expected exporters from the live plugin catalog
        expected_export_caps = [
            entry
            for entry in learn_adapter.get_capabilities_with_status()
            if entry["capability"].endswith("_export")
        ]
        assert len(expected_export_caps) > 0, "Plugin must expose at least one exporter capability"

        # Count must match catalog exactly
        assert len(exporters) == len(expected_export_caps), (
            f"exporters count {len(exporters)} does not match catalog "
            f"exporter capabilities count {len(expected_export_caps)}; "
            f"exporter ids: {[e['id'] for e in exporters]}, "
            f"catalog: {[e['capability'] for e in expected_export_caps]}"
        )

        # Build a quick lookup: id -> status
        exporter_map = {e["id"]: e for e in exporters}

        for entry in expected_export_caps:
            cap = entry["capability"]
            assert cap in exporter_map, (
                f"Exporter capability '{cap}' missing from /exporters response"
            )
            reported = exporter_map[cap]
            assert "status" in reported, f"Exporter missing 'status': {reported!r}"
            assert "label" in reported, f"Exporter missing 'label': {reported!r}"
            assert reported["status"] in ("available", "unavailable", "planned"), (
                f"Exporter status must be available/unavailable/planned, "
                f"got: {reported['status']!r}"
            )
            # Status must match the catalog (the route must not override it)
            assert reported["status"] == entry["status"], (
                f"Exporter '{cap}': route reports status={reported['status']!r} "
                f"but catalog says {entry['status']!r} — must match"
            )

        # Baseline: bundle_export and markdown_export must always be available
        for must_be_available in ("bundle_export", "markdown_export"):
            assert must_be_available in exporter_map, (
                f"Baseline exporter '{must_be_available}' must always be in /exporters"
            )
            assert exporter_map[must_be_available]["status"] == "available", (
                f"'{must_be_available}' must report status='available' (baseline exporter)"
            )

        # At least one non-bundle/markdown exporter exists (the catalog has notebook/html/quiz)
        optional_exporters = [e for e in exporters if e["id"] not in ("bundle_export", "markdown_export")]
        assert optional_exporters, (
            "Expected at least one optional-dep-gated exporter beyond bundle/markdown"
        )

        # Non-export capabilities must not appear in /exporters
        non_export_leaks = [e["id"] for e in exporters if not e["id"].endswith("_export")]
        assert not non_export_leaks, (
            f"Non-export capabilities must not appear in /exporters: {non_export_leaks}"
        )

    @pytest.mark.integration
    def test_exporters_returns_200_empty_when_plugin_absent(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Degrade-gracefully: /exporters returns 200 + empty list when plugin absent."""
        monkeypatch.setattr(learn_adapter, "akms_learn_available", lambda: False)

        r = client.get("/api/learn/exporters")
        assert r.status_code == 200, (
            f"exporters must return 200 (not 503) when plugin absent; got {r.status_code}"
        )
        data = r.json()
        assert data.get("exporters") == [], (
            "exporters must be [] when plugin absent"
        )
        assert data.get("akms_learn_available") is False


# ---------------------------------------------------------------------------
# Catalog-drift tripwire (hardening for _INFRA_CAPABILITIES)
# ---------------------------------------------------------------------------


class TestCatalogDriftTripwire:
    """Pin the akms_learn capability catalog so an AKMS re-vendor that changes it
    fails LOUDLY.

    ``_INFRA_CAPABILITIES`` is a hand-maintained denylist of baseline caps that
    are NOT generation modes — akms_learn exposes no "infra" grouping to import.
    If a `git subtree pull` grows BASELINE_CAPABILITIES and the new cap is infra,
    it would silently leak into /profiles as a bogus mode. These tests convert
    that silent drift into a CI failure at exactly the re-vendor moment.
    """

    @pytest.mark.unit
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_infra_capabilities_subset_of_baseline(self) -> None:
        from akms_learn.capabilities_catalog import BASELINE_CAPABILITIES
        from logic_loom_api.adapters.learn_adapter import _INFRA_CAPABILITIES

        stale = set(_INFRA_CAPABILITIES) - set(BASELINE_CAPABILITIES)
        assert not stale, (
            f"_INFRA_CAPABILITIES drifted from akms_learn BASELINE — these no longer "
            f"exist upstream: {stale}. Reconcile the denylist after the AKMS re-vendor."
        )

    @pytest.mark.unit
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_catalog_grouping_snapshot(self) -> None:
        """Snapshot the three catalog set SIZES. When a re-vendor changes any,
        this fails — forcing a human to classify new caps as generation-mode vs
        infra/adapter/exporter BEFORE bumping the snapshot (so nothing leaks)."""
        from akms_learn.capabilities_catalog import (
            ADAPTER_CAPABILITIES,
            BASELINE_CAPABILITIES,
            EXPORTER_CAPABILITIES,
        )

        snapshot = {"baseline": 18, "adapter": 4, "exporter": 3}
        actual = {
            "baseline": len(set(BASELINE_CAPABILITIES)),
            "adapter": len(set(ADAPTER_CAPABILITIES)),
            "exporter": len(set(EXPORTER_CAPABILITIES)),
        }
        assert actual == snapshot, (
            f"akms_learn capability catalog changed: {actual} vs pinned {snapshot}. "
            "Re-vendor detected — re-classify any new capability as a generation mode "
            "vs infra (_INFRA_CAPABILITIES in learn_adapter.py) before updating this snapshot."
        )

    @pytest.mark.unit
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_no_adapter_infra_or_exporter_leaks_into_modes(self) -> None:
        """The derived generation-mode set must never contain an adapter, infra,
        or exporter capability — the invariant /profiles depends on."""
        from akms_learn.capabilities_catalog import ADAPTER_CAPABILITIES
        from logic_loom_api.adapters.learn_adapter import (
            _INFRA_CAPABILITIES,
            get_generation_mode_capabilities,
        )

        modes = {m["capability"] for m in get_generation_mode_capabilities()}
        assert not (modes & set(ADAPTER_CAPABILITIES)), (
            f"adapter caps leaked into /profiles modes: {modes & set(ADAPTER_CAPABILITIES)}"
        )
        assert not (modes & set(_INFRA_CAPABILITIES)), (
            f"infra caps leaked into /profiles modes: {modes & set(_INFRA_CAPABILITIES)}"
        )
        assert not any(m.endswith("_export") for m in modes), (
            f"exporter caps leaked into /profiles modes: {[m for m in modes if m.endswith('_export')]}"
        )
