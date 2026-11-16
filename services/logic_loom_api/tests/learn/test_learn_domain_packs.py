"""Tests for the domain-pack / source-pack status payload.

Covers:
  1. Status includes domain_packs with capabilities and source_packs.
  2. No companion package is imported/inspected directly; no compmech concept
     is hard-coded.

Consumer rules for the domain-pack status (referenced below by number):
  rule 2 — Logic-Loom never imports or inspects companion packages;
  rule 3 — Logic-Loom never hard-codes computational-mechanics concepts;
  rule 4 — the payload is descriptor-driven, and an empty registry is valid.

Strategy:
  - The status payload is driven from descriptor DATA, not companion code. The
    integration tests write a NEUTRAL domain-pack descriptor (no compmech
    concept) + source-pack YAMLs into a tmp dir, point Settings'
    ``learn_domain_packs_path`` at it via env, and assert ``GET
    /api/learn/status`` (available path) surfaces them in the domain-pack shape. Using a
    neutral fixture proves the serializer is generic — it works for ANY domain
    pack, never a hard-coded one (rule 4).
  - A consumer-discipline guard test greps the production modules (routes/learn.py
    + adapters/learn_adapter.py) to assert they NEVER import a companion package
    and contain NO computational-mechanics literal (rules 2 & 3).
  - An empty-registry test asserts ``domain_packs == []`` when no descriptor is
    discoverable (rule 4 — an empty registry is valid, not an error).

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
HAS_DOMAIN_PACKS = importlib.util.find_spec("akms_learn.domain_packs") is not None


# ---------------------------------------------------------------------------
# Neutral descriptor fixtures (deliberately NOT compmech — proves the serializer
# is descriptor-driven and works for ANY domain, per rule 4).
# ---------------------------------------------------------------------------

_DOMAIN_PACK_YAML = """\
domain_pack_schema: akms-learn-domain-pack/v1
domain_id: demo
pack_id: demo.reference
name: Demo Reference Pack
version: "0.1.0"
status: reference
summary: Neutral domain pack used to exercise the domain-pack status serializer.
capabilities:
  static_markdown: true
  executable_notebook: false
companion_roles:
  - id: helperkit
    package_name: demo.helperkit
    runtime_hint: optional
    capability_status: planned
source_packs:
  - source_packs/helperkit.yaml
  - source_packs/bridge.yaml
"""

_SOURCE_PACK_HELPERKIT = """\
source_pack_schema: akms-learn-source-pack/v1
source_pack_id: demo.helperkit
name: Helper Kit
version: "0.1.0"
companion_role: concept_kit
capability_status: planned
adapter:
  adapter_id: demo.helperkit_adapter
  status: planned
"""

_SOURCE_PACK_BRIDGE = """\
source_pack_schema: akms-learn-source-pack/v1
source_pack_id: demo.bridge
name: Executable Bridge
version: "0.1.0"
companion_role: executable_bridge
capability_status: available
adapter:
  adapter_id: demo.bridge_runner
  status: available
"""


def _write_demo_descriptor(root: Path) -> Path:
    """Materialise the neutral demo domain pack under ``root``; return its dir."""
    pack_dir = root / "demo_pack"
    sp_dir = pack_dir / "source_packs"
    sp_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "domain_pack.yaml").write_text(_DOMAIN_PACK_YAML, encoding="utf-8")
    (sp_dir / "helperkit.yaml").write_text(_SOURCE_PACK_HELPERKIT, encoding="utf-8")
    (sp_dir / "bridge.yaml").write_text(_SOURCE_PACK_BRIDGE, encoding="utf-8")
    return pack_dir


# ---------------------------------------------------------------------------
# Client fixture (mirrors test_learn_profiles_exporters.py::client) parametrised by a descriptor
# search root passed via the LOGIC_LOOM_LEARN_DOMAIN_PACKS_PATH env var.
# ---------------------------------------------------------------------------


def _make_client(tmp_path: Path, domain_packs_path: Path | None) -> Iterator[TestClient]:
    cfg_path = tmp_path / "config.toml"
    env = {
        "LOGIC_LOOM_CONFIG": str(cfg_path),
        "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
        "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
        # Pin akms_repo to an empty dir so the search-root fallback never picks
        # up the developer's real AKMS tree — discovery is fully controlled here.
        "LOGIC_LOOM_AKMS_REPO": str(tmp_path / "empty_akms"),
    }
    if domain_packs_path is not None:
        env["LOGIC_LOOM_LEARN_DOMAIN_PACKS_PATH"] = str(domain_packs_path)
    with patch.dict(os.environ, env):
        from logic_loom_api.deps import reload_settings  # type: ignore[import-not-found]

        reload_settings()
        from logic_loom_api.main import api  # type: ignore[import-not-found]

        with TestClient(api) as c:
            yield c
        reload_settings()


@pytest.fixture()
def client_with_demo_pack(tmp_path: Path) -> Iterator[TestClient]:
    """TestClient whose domain-pack discovery points at a neutral demo pack."""
    pack_dir = _write_demo_descriptor(tmp_path / "domain_packs")
    yield from _make_client(tmp_path, pack_dir)


@pytest.fixture()
def client_no_packs(tmp_path: Path) -> Iterator[TestClient]:
    """TestClient whose domain-pack discovery finds an empty directory."""
    empty = tmp_path / "empty_domain_packs"
    empty.mkdir(parents=True, exist_ok=True)
    yield from _make_client(tmp_path, empty)


# ---------------------------------------------------------------------------
# Task tests
# ---------------------------------------------------------------------------


class TestDomainPackStatus:
    """Tests for the domain_packs status payload."""

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    @pytest.mark.skipif(not HAS_DOMAIN_PACKS, reason="akms_learn.domain_packs missing")
    def test_domain_packs_present_in_status(self, client_with_demo_pack: TestClient) -> None:
        """/status (available path) includes domain_packs[], each entry
        carrying domain_id/pack_id/status/capabilities, sourced from descriptor
        DATA. Statuses come from the descriptor (here: 'reference')."""
        r = client_with_demo_pack.get("/api/learn/status")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        payload = r.json()

        assert payload.get("akms_learn_available") is True, (
            "available path required for the domain_packs key"
        )
        assert "domain_packs" in payload, "status must include a domain_packs key"
        packs = payload["domain_packs"]
        assert isinstance(packs, list) and packs, (
            "domain_packs must be a non-empty list when a descriptor is discovered"
        )

        pack = next((p for p in packs if p.get("pack_id") == "demo.reference"), None)
        assert pack is not None, f"demo.reference pack missing from {packs!r}"

        # Required domain-pack keys.
        for key in ("domain_id", "pack_id", "status", "capabilities", "source_packs"):
            assert key in pack, f"domain pack missing key {key!r}: {pack!r}"

        assert pack["domain_id"] == "demo"
        assert pack["pack_id"] == "demo.reference"
        assert pack["status"] == "reference"
        # capabilities is the descriptor's own map (data passthrough), values
        # are plain JSON (bools), not enums.
        assert isinstance(pack["capabilities"], dict) and pack["capabilities"]
        assert pack["capabilities"]["static_markdown"] is True
        assert pack["capabilities"]["executable_notebook"] is False

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    @pytest.mark.skipif(not HAS_DOMAIN_PACKS, reason="akms_learn.domain_packs missing")
    def test_source_packs_roles_and_status_present(
        self, client_with_demo_pack: TestClient
    ) -> None:
        """Each pack's source_packs[] carry source_pack_id/role/status,
        resolved from the relative source-pack YAMLs via akms_learn's loader.
        Enum fields (role, status) are serialised to their plain string values."""
        r = client_with_demo_pack.get("/api/learn/status")
        assert r.status_code == 200, r.text
        packs = r.json()["domain_packs"]
        pack = next(p for p in packs if p["pack_id"] == "demo.reference")

        source_packs = pack["source_packs"]
        assert isinstance(source_packs, list) and len(source_packs) == 2, (
            f"expected 2 source packs, got {source_packs!r}"
        )
        by_id = {sp["source_pack_id"]: sp for sp in source_packs}

        for sp in source_packs:
            for key in ("source_pack_id", "role", "status"):
                assert key in sp, f"source pack missing key {key!r}: {sp!r}"
            # Enums serialised to plain strings (not 'CapabilityStatus.planned').
            assert isinstance(sp["role"], str) and "." not in sp["role"]
            assert isinstance(sp["status"], str) and "." not in sp["status"]

        assert by_id["demo.helperkit"]["role"] == "concept_kit"
        assert by_id["demo.helperkit"]["status"] == "planned"
        assert by_id["demo.bridge"]["role"] == "executable_bridge"
        assert by_id["demo.bridge"]["status"] == "available"

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_empty_registry_yields_empty_list(self, client_no_packs: TestClient) -> None:
        """Rule 4: no discoverable descriptor → domain_packs == [].

        An empty registry is a valid state, never an error — the status route
        still returns 200 with an empty list, not a 5xx and not a missing key."""
        r = client_no_packs.get("/api/learn/status")
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload.get("akms_learn_available") is True
        assert payload.get("domain_packs") == [], (
            "an empty discovery root must yield domain_packs == []"
        )

    # -----------------------------------------------------------------------
    # Consumer-discipline guard (rules 2 & 3) — static source scan
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_production_source_has_no_companion_import_or_compmech_literal(self) -> None:
        """Rules 2 & 3 (enforced statically):

        rule 2 — Logic-Loom MUST NOT import or inspect companion packages.
        rule 3 — Logic-Loom MUST NOT hard-code computational-mechanics concepts.

        Greps the production modules that drive the domain-pack status payload
        (routes/learn.py + adapters/learn_adapter.py + config.py) and asserts:
        (a) no import of a companion package, and (b) no
        compmech/mechdsl/constkit/etc. literal.  config.py is included because
        Settings.learn_domain_packs_search_root() is the discovery entry point
        and its docstring/comments must likewise stay domain-agnostic.
        This is the test that keeps the feature honest — descriptor DATA in,
        no companion code, no domain ontology baked into Logic-Loom."""
        import logic_loom_api.adapters.learn_adapter as la_mod
        import logic_loom_api.config as cfg_mod
        import logic_loom_api.routes.learn as route_mod

        sources = {
            Path(la_mod.__file__): Path(la_mod.__file__).read_text(encoding="utf-8"),
            Path(route_mod.__file__): Path(route_mod.__file__).read_text(encoding="utf-8"),
            Path(cfg_mod.__file__): Path(cfg_mod.__file__).read_text(encoding="utf-8"),
        }

        # (a) No companion-package import. The only permitted akms imports are the
        # akms_learn infrastructure (incl. akms_learn.domain_packs). Companion
        # packages would appear as bare top-level imports of these names.
        forbidden_imports = (
            "import compmech_reference_pack",
            "from compmech_reference_pack",
            "import constkit",
            "from constkit",
            "import mechdsl",
            "from mechdsl",
            "import symbolic_fem_workbench",
            "from symbolic_fem_workbench",
        )
        # (b) No hard-coded computational-mechanics concept literals. These are
        # DOMAIN ontology that must only ever arrive as descriptor data.
        forbidden_literals = (
            "compmech",
            "mechdsl",
            "constkit",
            "symbolic_fem_workbench",
        )

        for path, text in sources.items():
            for needle in forbidden_imports:
                assert needle not in text, (
                    f"{path.name} imports a companion package ({needle!r}) — "
                    "rule 2 forbids importing/inspecting companions."
                )
            lowered = text.lower()
            for needle in forbidden_literals:
                assert needle not in lowered, (
                    f"{path.name} contains a hard-coded comp-mech literal "
                    f"({needle!r}) — rule 3 forbids hard-coding "
                    "computational-mechanics concepts; values must come from "
                    "descriptor data only."
                )

    @pytest.mark.unit
    def test_get_domain_packs_degrades_without_settings(self) -> None:
        """Degrade-gracefully: get_domain_packs(None) returns [] and never raises.

        This is the seam the build_status() no-arg call sites rely on so the
        status-contract tests keep passing unchanged."""
        assert learn_adapter.get_domain_packs(None) == []


# ---------------------------------------------------------------------------
# Gate-B minor fixes: narrowed fallback + cache isolation (perf/cleanliness)
# ---------------------------------------------------------------------------


class TestSearchRootNarrowing:
    """learn_domain_packs_search_root() now falls back to <akms_repo>/Packages,
    not the bare akms_repo root (Gate-B minor fix)."""

    @pytest.mark.unit
    def test_fallback_resolves_to_packages_subdir(self, tmp_path: Path) -> None:
        """When learn_domain_packs_path is unset, the resolver returns
        <akms_repo>/Packages (not the bare akms_repo) when that dir exists."""
        from logic_loom_api.config import Settings

        fake_repo = tmp_path / "AKMS"
        packages_dir = fake_repo / "Packages"
        packages_dir.mkdir(parents=True)

        s = Settings.load(
            config_path=tmp_path / "missing.toml",
            akms_repo=fake_repo,
        )
        root = s.learn_domain_packs_search_root()
        assert root == packages_dir, (
            f"Fallback must be <akms_repo>/Packages, got {root!r}"
        )

    @pytest.mark.unit
    def test_fallback_returns_none_when_packages_dir_absent(self, tmp_path: Path) -> None:
        """When learn_domain_packs_path is unset and <akms_repo>/Packages does not
        exist, resolver returns None (degrade-gracefully → domain_packs: [])."""
        from logic_loom_api.config import Settings

        fake_repo = tmp_path / "AKMS"
        fake_repo.mkdir(parents=True)
        # Note: NO Packages/ subdir created — the narrowed fallback must return None.

        s = Settings.load(
            config_path=tmp_path / "missing.toml",
            akms_repo=fake_repo,
        )
        root = s.learn_domain_packs_search_root()
        assert root is None, (
            f"Resolver must return None when <akms_repo>/Packages is absent, got {root!r}"
        )

    @pytest.mark.unit
    def test_explicit_path_still_wins(self, tmp_path: Path) -> None:
        """Explicit learn_domain_packs_path takes precedence over the akms_repo
        fallback — unchanged precedence rule."""
        from logic_loom_api.config import Settings

        explicit_dir = tmp_path / "my_packs"
        explicit_dir.mkdir()
        fake_repo = tmp_path / "AKMS"
        (fake_repo / "Packages").mkdir(parents=True)

        s = Settings.load(
            config_path=tmp_path / "missing.toml",
            akms_repo=fake_repo,
            learn_domain_packs_path=explicit_dir,
        )
        root = s.learn_domain_packs_search_root()
        assert root == explicit_dir, (
            f"Explicit learn_domain_packs_path must win over akms_repo fallback, got {root!r}"
        )


class TestTestDirExclusion:
    """_discover_descriptor_paths must not surface descriptors under test directories."""

    @pytest.mark.unit
    def test_fixture_descriptors_excluded_from_discovery(self, tmp_path: Path) -> None:
        """A real descriptor at <root>/demo_pack/domain_pack.yaml is returned;
        a fixture descriptor at <root>/something/tests/fixtures/domain_packs/x/domain_pack.yaml
        is silently excluded — test-tree paths must never appear as live domain packs."""
        from logic_loom_api.adapters.learn_adapter import _discover_descriptor_paths

        # Real (production) descriptor.
        real_dir = tmp_path / "demo_pack"
        real_dir.mkdir(parents=True)
        real_yaml = real_dir / "domain_pack.yaml"
        real_yaml.write_text("pack_id: real_pack\n", encoding="utf-8")

        # Fixture descriptor nested inside a tests/fixtures tree — must be excluded.
        fixture_dir = tmp_path / "something" / "tests" / "fixtures" / "domain_packs" / "x"
        fixture_dir.mkdir(parents=True)
        fixture_yaml = fixture_dir / "domain_pack.yaml"
        fixture_yaml.write_text("pack_id: fixture_pack\n", encoding="utf-8")

        found = _discover_descriptor_paths(tmp_path)

        assert real_yaml in found, (
            f"Real descriptor must be discovered; found={[str(p) for p in found]!r}"
        )
        assert fixture_yaml not in found, (
            f"Fixture descriptor under tests/fixtures must be excluded; found={[str(p) for p in found]!r}"
        )
        assert len(found) == 1, (
            f"Only the real descriptor should be returned, got {[str(p) for p in found]!r}"
        )

    @pytest.mark.unit
    def test_test_dir_variants_all_excluded(self, tmp_path: Path) -> None:
        """Segments 'test', 'tests', 'fixtures', '__pycache__' are all excluded
        (case-insensitively). A descriptor at none of these paths is returned."""
        from logic_loom_api.adapters.learn_adapter import _discover_descriptor_paths

        excluded_roots = [
            tmp_path / "pkg" / "tests" / "domain_pack.yaml",
            tmp_path / "pkg" / "test" / "domain_pack.yaml",
            tmp_path / "pkg" / "fixtures" / "domain_pack.yaml",
            tmp_path / "pkg" / "__pycache__" / "domain_pack.yaml",
            # Case-insensitive variants.
            tmp_path / "pkg" / "Tests" / "domain_pack.yaml",
            tmp_path / "pkg" / "FIXTURES" / "domain_pack.yaml",
        ]
        for p in excluded_roots:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("pack_id: should_be_excluded\n", encoding="utf-8")

        # One real descriptor that should survive.
        real = tmp_path / "real_pack" / "domain_pack.yaml"
        real.parent.mkdir(parents=True)
        real.write_text("pack_id: real\n", encoding="utf-8")

        found = _discover_descriptor_paths(tmp_path)
        assert found == [real], (
            f"Only the real descriptor must survive; got {[str(p) for p in found]!r}"
        )


    @pytest.mark.unit
    def test_search_root_under_test_ancestor_not_over_excluded(self, tmp_path: Path) -> None:
        """A search_root that itself sits under a 'tests' ANCESTOR must not cause
        every descriptor to be dropped — only excluded segments WITHIN the scanned
        tree (relative to search_root) count. Guards the _is_test_path
        relative-path fix (an akms_repo at /home/u/tests/AKMS would otherwise
        yield zero domain packs)."""
        from logic_loom_api.adapters.learn_adapter import _discover_descriptor_paths

        # search_root lives under an ancestor literally named 'tests'.
        search_root = tmp_path / "tests" / "checkout" / "akms_repo" / "Packages"
        pack = search_root / "real_pack" / "domain_pack.yaml"
        pack.parent.mkdir(parents=True)
        pack.write_text("pack_id: real\n", encoding="utf-8")

        # A 'fixtures' dir WITHIN the scanned tree must still be excluded.
        fixture = search_root / "x" / "fixtures" / "domain_pack.yaml"
        fixture.parent.mkdir(parents=True)
        fixture.write_text("pack_id: fixture\n", encoding="utf-8")

        found = _discover_descriptor_paths(search_root)
        assert found == [pack], (
            "ancestor 'tests' must be ignored while in-tree 'fixtures' is excluded; "
            f"got {[str(p) for p in found]!r}"
        )


class TestDomainPackCacheIsolation:
    """The path-keyed discovery cache must not leak results across distinct roots."""

    @pytest.mark.unit
    @pytest.mark.skipif(not HAS_DOMAIN_PACKS, reason="akms_learn.domain_packs missing")
    def test_two_roots_yield_independent_results(self, tmp_path: Path) -> None:
        """Two separate search roots with different descriptor sets each return
        their own results — no cross-contamination via the module-level cache."""
        from logic_loom_api.adapters.learn_adapter import (
            clear_domain_packs_cache,
            get_domain_packs,
        )
        from logic_loom_api.config import Settings

        # Root A: contains the demo descriptor.
        root_a = tmp_path / "root_a"
        _write_demo_descriptor(root_a)

        # Root B: empty — no descriptors.
        root_b = tmp_path / "root_b"
        root_b.mkdir()

        # Ensure a clean cache before the test.
        clear_domain_packs_cache()

        s_a = Settings.load(
            config_path=tmp_path / "missing.toml",
            learn_domain_packs_path=root_a,
        )
        s_b = Settings.load(
            config_path=tmp_path / "missing.toml",
            learn_domain_packs_path=root_b,
        )

        result_a = get_domain_packs(s_a)
        result_b = get_domain_packs(s_b)

        assert len(result_a) >= 1, f"Root A must yield at least one pack; got {result_a!r}"
        assert result_b == [], f"Root B must yield [] (empty dir); got {result_b!r}"

        # Call again — must still be independent (cache hit path).
        assert get_domain_packs(s_a) == result_a
        assert get_domain_packs(s_b) == []

    @pytest.mark.unit
    @pytest.mark.skipif(not HAS_DOMAIN_PACKS, reason="akms_learn.domain_packs missing")
    def test_clear_cache_forces_fresh_discovery(self, tmp_path: Path) -> None:
        """clear_domain_packs_cache() invalidates all entries so the next call
        re-discovers from disk (important for CLI/reload scenarios)."""
        from logic_loom_api.adapters.learn_adapter import (
            _domain_packs_cache,
            clear_domain_packs_cache,
            get_domain_packs,
        )
        from logic_loom_api.config import Settings

        root = tmp_path / "root"
        _write_demo_descriptor(root)

        clear_domain_packs_cache()

        s = Settings.load(
            config_path=tmp_path / "missing.toml",
            learn_domain_packs_path=root,
        )
        # Populate the cache.
        result = get_domain_packs(s)
        assert len(result) >= 1
        assert str(root) in _domain_packs_cache

        # Clear and verify the entry is gone.
        clear_domain_packs_cache()
        assert _domain_packs_cache == {}, "Cache must be empty after clear_domain_packs_cache()"

        # Re-discover — still finds the same pack.
        result2 = get_domain_packs(s)
        assert result2 == result, "Re-discovery after cache clear must return the same packs"
