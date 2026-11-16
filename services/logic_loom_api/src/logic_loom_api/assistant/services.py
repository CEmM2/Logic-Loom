"""Assistant service factory.

`get_assistant_services(settings)` returns a record exposing every
Assistant service: discovery, zotero_match, related, batches, workflows,
runner, output_store, research_index. Routes and other downstream code
should import services through this factory instead of constructing the
individual classes directly — the factory layer is where shared
dependencies (Claude runtime, AKMS BatchStore, etc.) get injected.

`output_store` is kept as its own module rather than folded into the
runner stack because the outputs API is built independently of the run
dispatcher; it is co-listed under the `services` mapping for ergonomics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from ..config import Settings
from .batches import SourceBatchService
from .discovery import PaperDiscoveryService
from .models import BatchSourceItem, SourceBatch
from .output_store import OutputStoreService
from .related import RelatedPaperService
from .research_index import ResearchIndexService
from .research_index.ingest import zotero_body_for
from .research_index.schema import IndexableRecord, compute_content_hash
from .runner import RunnerService
from .workflows import WorkflowService
from .zotero_match import ZoteroMatchService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssistantServices:
    """Named record of Assistant service stubs.

    Frozen dataclass so consumers can rely on stable attribute access
    (`services.discovery`, `services.runner`, …) without dict-key typos.
    """

    discovery: PaperDiscoveryService
    zotero_match: ZoteroMatchService
    related: RelatedPaperService
    batches: SourceBatchService
    workflows: WorkflowService
    runner: RunnerService
    output_store: OutputStoreService
    research_index: ResearchIndexService

    def as_mapping(self) -> dict[str, object]:
        """Return a dict view of the record. Useful for introspection /
        diagnostics endpoints."""
        return {
            "discovery": self.discovery,
            "zotero_match": self.zotero_match,
            "related": self.related,
            "batches": self.batches,
            "workflows": self.workflows,
            "runner": self.runner,
            "output_store": self.output_store,
            "research_index": self.research_index,
        }


def _make_batch_index_hook(
    settings: Settings,
    research_index: ResearchIndexService,
) -> "Callable[[SourceBatch], None]":
    """Return a post-write hook that upserts a batch's zotero sources.

    The hook iterates the batch's :class:`BatchSourceItem` list, picks
    the ``kind="zotero"`` entries (the only batch-derived kind in the
    closed-set index schema), and fires
    :meth:`IndexLifecycle.on_change` for each. The lifecycle's 500&thinsp;ms
    debounce collapses rapid bulk edits into a single flush.

    Non-zotero source kinds are silently skipped — they don't
    correspond to an indexable record kind today. The hook is a no-op
    when the batch has no zotero sources.

    ``source_path`` mirrors the bulk-rebuild ``ingest_zotero`` adapter:
    every zotero record points at the batches store on disk so search
    results carry a stable URI for UI deep-linking. The path is resolved
    inside the closure (once per batch write, not per record) so it
    tracks any dynamic workspace switching on ``settings`` rather than
    freezing the value at factory time.
    """

    def hook(batch: SourceBatch) -> None:
        batches_source_path = str(settings.assistant_batches_path().resolve())
        lifecycle = research_index._get_lifecycle()  # noqa: SLF001
        for item in batch.sources:
            if item.kind != "zotero":
                continue
            body = zotero_body_for(item)
            record = IndexableRecord(
                id=f"zotero:{batch.id}:{item.id}",
                kind="zotero",
                title=item.title,
                body=body,
                source_path=batches_source_path,
                batch_id=batch.id,
                created_at=batch.created_at,
                updated_at=batch.updated_at,
                content_hash=compute_content_hash(
                    kind="zotero", title=item.title, body=body
                ),
            )
            lifecycle.on_change(record)

    return hook


def _make_batch_eviction_hook(
    research_index: ResearchIndexService,
) -> "Callable[[str, list[BatchSourceItem]], None]":
    """Return a hook that drops evicted zotero sources from the index.

    Fires :meth:`IndexLifecycle.on_delete` for each removed
    :class:`BatchSourceItem` of ``kind="zotero"``. Non-zotero sources
    aren't indexed in the first place, so they're silently skipped.
    Used by :meth:`SourceBatchService.remove_sources` and
    :meth:`SourceBatchService.delete`.
    """

    def hook(batch_id: str, removed: list[BatchSourceItem]) -> None:
        lifecycle = research_index._get_lifecycle()  # noqa: SLF001
        for item in removed:
            if item.kind != "zotero":
                continue
            lifecycle.on_delete(f"zotero:{batch_id}:{item.id}")

    return hook


def get_assistant_services(settings: Settings) -> AssistantServices:
    """Construct the Assistant service record.

    Every service receives the same `Settings` instance. The
    :class:`SourceBatchService` is wired with a post-write hook so
    batch edits flow into the research-memory index automatically;
    other services still rely on the manual rebuild route for now.
    """
    research_index = ResearchIndexService(settings)
    lifecycle = research_index._get_lifecycle()  # noqa: SLF001
    batches = SourceBatchService(
        settings,
        on_batch_changed=_make_batch_index_hook(settings, research_index),
        on_sources_deleted=_make_batch_eviction_hook(research_index),
    )
    return AssistantServices(
        discovery=PaperDiscoveryService(settings),
        zotero_match=ZoteroMatchService(settings),
        related=RelatedPaperService(settings),
        batches=batches,
        workflows=WorkflowService(settings),
        runner=RunnerService(settings, batches=batches, index_lifecycle=lifecycle),
        output_store=OutputStoreService(settings),
        research_index=research_index,
    )
