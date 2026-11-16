"""Tests for the host_llm/ package import shim (discovery moved out of Learn).

Covers: discover_providers, register_host_llm_providers, inject_api_keys_into_env
and rewire_host_llm stay importable from the learn_llm_providers shim;
discover_providers is importable from the host_llm package; and
test_learn_llm_providers.py keeps passing unchanged as the regression gate.
"""

from __future__ import annotations

import pytest


class TestHostLlmImportShim:
    """Tests for the host_llm/ package and the learn_llm_providers import shim."""

    @pytest.mark.unit
    def test_import_shim_old_new_paths_same_object(self) -> None:
        """Verifies: old (learn_llm_providers) and new (host_llm) import paths resolve to the same object.

        Requirement: 'Old and new import paths resolve to the same object.'
        Passes when: importing discover_providers, register_host_llm_providers,
        inject_api_keys_into_env, rewire_host_llm from both paths yields identical function objects.
        """
        from logic_loom_api import learn_llm_providers as shim
        from logic_loom_api.host_llm import discovery

        # Same objects via both paths
        assert shim.discover_providers is discovery.discover_providers
        assert shim.register_host_llm_providers is discovery.register_host_llm_providers
        assert shim.inject_api_keys_into_env is discovery.inject_api_keys_into_env
        assert shim.rewire_host_llm is discovery.rewire_host_llm

        # Also verify importable from host_llm package directly
        from logic_loom_api.host_llm import discover_providers as dp_pkg
        assert dp_pkg is discovery.discover_providers
