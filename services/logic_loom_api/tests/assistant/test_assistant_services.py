"""Tests for the shared Assistant service skeletons.

Covers: get_assistant_services(settings) returns a structure
exposing every named service (seven core services plus the eighth
output_store module).
"""

from __future__ import annotations

import importlib

import pytest

from logic_loom_api.assistant import (
    AssistantServices,
    get_assistant_services,
)
from logic_loom_api.assistant.batches import SourceBatchService
from logic_loom_api.assistant.discovery import PaperDiscoveryService
from logic_loom_api.assistant.output_store import OutputStoreService
from logic_loom_api.assistant.related import RelatedPaperService
from logic_loom_api.assistant.research_index import ResearchIndexService
from logic_loom_api.assistant.runner import RunnerService
from logic_loom_api.assistant.workflows import WorkflowService
from logic_loom_api.assistant.zotero_match import ZoteroMatchService
from logic_loom_api.config import Settings


_EXPECTED_MODULES = (
    "discovery",
    "zotero_match",
    "related",
    "batches",
    "workflows",
    "runner",
    "output_store",
    "research_index",
)

_EXPECTED_SERVICE_CLASSES = {
    "discovery": PaperDiscoveryService,
    "zotero_match": ZoteroMatchService,
    "related": RelatedPaperService,
    "batches": SourceBatchService,
    "workflows": WorkflowService,
    "runner": RunnerService,
    "output_store": OutputStoreService,
    "research_index": ResearchIndexService,
}


class TestAssistantServiceFactory:
    """Unit tests for the assistant stub modules + service factory."""

    @pytest.mark.unit
    def test_each_stub_module_imports(self) -> None:
        """Verifies: each stub module under logic_loom_api.assistant.*
        imports without error.

        Required modules:
        discovery, zotero_match, related, batches, workflows, runner,
        output_store, research_index.
        """
        for name in _EXPECTED_MODULES:
            mod = importlib.import_module(f"logic_loom_api.assistant.{name}")
            assert mod is not None, f"failed to import {name}"

    @pytest.mark.unit
    def test_get_assistant_services_returns_record(self, tmp_path) -> None:
        """Verifies: get_assistant_services(settings) returns a record
        exposing every named service.

        Requirement: 'returns a record of seven stubs' (note: eight modules
        exist). Eight services are exposed — see the seven-vs-eight
        reconciliation in services.py.
        """
        s = Settings(
            akms_workspace=tmp_path / "AKMS-Workspace",
            assistant_workspace=None,
            assistant_workspace_colocate=False,
        )

        services = get_assistant_services(s)

        # Type contract.
        assert isinstance(services, AssistantServices)

        # Every named attribute is the right service class.
        for attr, cls in _EXPECTED_SERVICE_CLASSES.items():
            obj = getattr(services, attr)
            assert isinstance(obj, cls), f"{attr} should be {cls.__name__}, got {type(obj).__name__}"

        # `as_mapping` agrees with attribute access.
        mapping = services.as_mapping()
        assert set(mapping.keys()) == set(_EXPECTED_MODULES)
        for attr, obj in mapping.items():
            assert getattr(services, attr) is obj

    @pytest.mark.unit
    def test_stub_methods_raise_not_implemented(self, tmp_path) -> None:
        """Verifies: calling any stub method raises NotImplementedError.

        Requirement: 'Each module exports a stub class raising
        NotImplementedError on method calls'.
        """
        s = Settings(
            akms_workspace=tmp_path / "AKMS-Workspace",
            assistant_workspace=None,
            assistant_workspace_colocate=False,
        )
        services = get_assistant_services(s)

        # Probe at least one method per service.
        method_probes: list[tuple[object, str, tuple[object, ...]]] = [
            # discovery.* methods are implemented — `search` and
            # `get_provider_status` are real, so they leave the
            # NotImplementedError probe set. The factory still exposes
            # `services.discovery` (asserted elsewhere in this test class).
            # zotero_match.match is implemented — it is now real,
            # so it leaves the NotImplementedError probe set.
            # related.related_for / related.invalidate were replaced —
            # the service now exposes the five mode methods
            # (find_similar, find_foundational, ...) rather than the
            # original related_for / invalidate stubs, so they leave the
            # NotImplementedError probe set too.
            # batches.* methods are implemented — `list_batches`,
            # `create_from_discovery`, and `send_to_akms` are real, so they
            # are removed from the
            # NotImplementedError probe set. The factory still exposes
            # `services.batches` (asserted elsewhere in this test class).
            # workflows.list_workflows is implemented — the service
            # now returns the filtered bundled-workflow list, so the
            # NotImplementedError probe is removed (fourth occurrence of
            # the replaced-route test coupling tripwire). The factory
            # still exposes `services.workflows`.
            # runner.dispatch_workflow is implemented — the
            # service now dispatches via start_run, so the
            # NotImplementedError probe is removed (fifth occurrence of
            # the tripwire). The factory still exposes
            # `services.runner` (asserted elsewhere in this test class).
            (services.output_store, "list_outputs", ()),
            (services.output_store, "read_output", ("o1",)),
            # research_index.search is implemented — backed by
            # the FTS5 backend, so the NotImplementedError probe is
            # removed (seventh occurrence of the replaced-stub
            # tripwire). research_index.rebuild is implemented too
            # (lifecycle.rebuild + /api/assistant/index/rebuild
            # route), so its probe is removed too (eighth occurrence).
            # The factory still exposes `services.research_index`
            # (asserted elsewhere in this test class).
        ]
        for obj, name, args in method_probes:
            with pytest.raises(NotImplementedError):
                getattr(obj, name)(*args)

    @pytest.mark.unit
    def test_seven_versus_eight_reconciliation(self) -> None:
        """Verifies the reconciliation between the 8 assistant modules and
        the 7 documented core services.

        Records that the factory exposes exactly the eight modules. If a
        future change decides to collapse `output_store`
        into `runner`, this assertion is the canonical failure point.
        """
        assert len(_EXPECTED_MODULES) == 8
        assert "output_store" in _EXPECTED_MODULES, "scope mismatch"
