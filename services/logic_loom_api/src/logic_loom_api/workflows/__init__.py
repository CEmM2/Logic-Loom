"""Workflow Library subsystem.

Re-exports the schema and loader so callers can write::

    from logic_loom_api.workflows import WorkflowDefinition, list_bundled_workflows
"""

from __future__ import annotations

from .builtin import (
    BUNDLED_RESOURCE_PACKAGE,
    WORKFLOW_SUFFIX,
    bundled_ids,
    iter_bundled,
    list_bundled_workflows,
)
from .models import (
    SCHEMA_VERSION,
    Executor,
    OutputFormat,
    SourceMode,
    WorkflowDefinition,
    WorkflowSource,
    WorkflowStatus,
    WorkflowValidation,
    dump_workflow_to_dict,
    load_workflow_from_json,
)

__all__ = [
    "BUNDLED_RESOURCE_PACKAGE",
    "Executor",
    "OutputFormat",
    "SCHEMA_VERSION",
    "SourceMode",
    "WORKFLOW_SUFFIX",
    "WorkflowDefinition",
    "WorkflowSource",
    "WorkflowStatus",
    "WorkflowValidation",
    "bundled_ids",
    "dump_workflow_to_dict",
    "iter_bundled",
    "list_bundled_workflows",
    "load_workflow_from_json",
]
