"""Response models for the Failure Memory workspace.

These are the wire contract between ``routes/failure_memory.py`` and
``apps/desktop/src/api/failureMemory.ts``. Two shapes here carry more weight
than the rest and should be changed only deliberately:

:class:`DeliveryReport`
    Makes "this lesson was delivered NOWHERE" visibly different from "no lesson
    applies to this task". Those two look identical in a naive UI — both render
    as an empty required list — and the shared conformance pack proves they are
    not the same thing at all: a documentation-only task whose declared path has
    a live compiled route returns ``records == []`` with ``status: "ok"``. An
    operator who cannot tell the two apart concludes the lesson is inactive when
    it is merely unroutable.

:class:`OverlayNode` / :class:`OverlayEdge`
    Every id is prefixed with the project's ``repository_id``, so failure memory
    stays an OVERLAY on top of the source graph and two repositories that both
    contain ``src/config.py`` never merge into one node.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FailureMemoryMode = Literal["disabled", "read-only", "operator"]

#: Overlay edge vocabulary for the failure-memory graph representation
#: (a closed set).
OverlayEdgeType = Literal[
    "failure_route",
    "load_with",
    "selected_required",
    "selected_coactivated",
    "selected_advisory",
]

SelectionClass = Literal["required", "coactivated", "advisory"]


# ── discovery ───────────────────────────────────────────────────────────────


class ProjectSummary(BaseModel):
    """One entry from ``[[failure_memory_projects]]``, loaded or not.

    ``available=False`` with an ``error_code`` is a normal, reportable state:
    a configured project whose repository is not checked out on this machine
    should degrade to a disabled row, not break the whole projects list.
    """

    id: str
    config_path: str
    root: str | None = None
    available: bool = False
    repository_id: str | None = None
    node_namespace: str | None = None
    config_fingerprint: str | None = None
    error_code: str | None = None
    error: str | None = None
    never_written: bool = Field(
        default=False,
        description=(
            "The project owns NO artifacts at all — no registry, no route index, no "
            "generated nodes, no graph. A distinct, actionable state: not 'broken', not "
            "'no lessons', and in operator mode fixed by writing once. Deliberately NOT "
            "derived from the disposable lock directory, whose absence is ordinary in any "
            "fresh clone and says nothing about whether the project has been written to."
        ),
    )
    lock_parent_exists: bool = Field(
        default=True,
        description=(
            "Whether the disposable lock directory is materialised. An operational fact "
            "about lock-taking calls only; it is NOT evidence about content or currency."
        ),
    )


class ProjectsResponse(BaseModel):
    """Always-200 discovery payload — the workspace gate reads this.

    Mirrors the ``GET /api/learn/status`` convention: read-only discovery
    endpoints never 503, they report unavailability in the body so the UI can
    render an install hint instead of an error toast.
    """

    mode: FailureMemoryMode
    enabled: bool = Field(description="mode != 'disabled'.")
    operator: bool = Field(description="mode == 'operator': record/compile/refresh are enabled.")
    available: bool = Field(description="enabled AND the optional package is importable.")
    package_installed: bool
    package_version: str | None = None
    reason: str | None = None
    degradation: dict[str, Any] | None = Field(
        default=None,
        description="The frozen consumer-contract degradation envelope, present only when the package is absent.",
    )
    projects: list[ProjectSummary] = Field(default_factory=list)


# ── status ──────────────────────────────────────────────────────────────────


class ArtifactState(BaseModel):
    """Existence + digest of one configured artifact, as the package reports it."""

    key: str
    path: str
    exists: bool
    sha256: str | None = None
    policy: str | None = Field(default=None, description="committed | disposable, from [generated].")


class StalenessState(BaseModel):
    """Whether the committed route index still matches the canonical registry.

    This is Logic-Loom's cheap, purely local staleness signal: the compiled
    route index records the registry digest it was built from, so comparing that
    to the registry's current digest answers "have the lessons moved since the
    last compile?" without running anything.

    It is NOT the provider fingerprint staleness the WWW stream gates on. That
    one additionally depends on the published graph and on a caller-supplied
    ``generated_at``, and the shared pack's operator rule is explicit that a
    consumer may treat it as blocking only if it controls both of those axes.
    Logic-Loom controls neither, so it reports and never blocks.
    """

    state: Literal["current", "stale", "uncompiled", "unknown"]
    registry_sha256: str | None = None
    routes_source_hash: str | None = None
    reason: str


class GraphState(BaseModel):
    """Published project-graph header, or ``published=False``.

    ``identity_matches_config`` compares the graph's recorded ``repo_id`` with
    the project configuration's ``repository_id``. The package treats the
    configured value as the single authority on identity, so a mismatch means
    the graph was published under a different identity and will read as stale
    until an explicit graph refresh — worth surfacing, never worth guessing at.

    ``currency`` answers "is this published graph the one that belongs to the
    registry as it stands?" — and answers ``unverifiable`` when it cannot tell,
    which is a different thing from ``current``.
    """

    currency: Literal["current", "stale", "unverifiable", "absent"] = "absent"
    currency_reason: str = ""
    content_node_count: int = Field(
        default=0,
        description=(
            "Nodes actually present in the graph DOCUMENT, counted from its `nodes` "
            "array — never the `graph.node_count` header, which is caller-controlled "
            "metadata about content, not content."
        ),
    )
    routed_node_count: int = Field(
        default=0, description="Lesson nodes the compiled route index points at."
    )
    missing_routed_node_ids: list[str] = Field(
        default_factory=list,
        description="Routed nodes ABSENT from the published graph. Resolution fails for these.",
    )
    published: bool = False
    generated_at: str | None = None
    repo_id: str | None = None
    global_vault: str | None = None
    node_count: int | None = None
    edge_count: int | None = None
    identity_matches_config: bool | None = None


class StatusResponse(BaseModel):
    mode: FailureMemoryMode
    project_id: str
    repository_id: str
    node_namespace: str
    config_path: str
    root: str
    config_fingerprint: str
    lesson_count: int
    routed_path_count: int
    routed_symbol_count: int
    never_written: bool = Field(
        default=False,
        description="See ProjectSummary.never_written. Reads still work; writes bootstrap it.",
    )
    lock_parent_exists: bool = Field(
        default=True,
        description=(
            "Operational only — lock-taking calls need it. It never influences `currency`: "
            "a cold clone with committed artifacts and no scratch directory can be, and "
            "must be reported as, STALE."
        ),
    )
    currency: Literal["current", "stale", "unverifiable", "uncompiled", "never_written"] = (
        Field(
            default="unverifiable",
            description=(
                "The single honest answer to 'is what this project would resolve today "
                "current?' — the worst finding across every signal, ordered by strength "
                "of evidence. `current` only when everything positively verified; "
                "anything undeterminable is `unverifiable`, never `current`. On this "
                "read-only surface `unverifiable` is informational; an ENFORCING "
                "consumer must treat it as blocking. `stale` outranks `never_written`."
            ),
        )
    )
    currency_reason: str = ""
    check_status: Literal[
        "clean", "drift", "invalid", "never_written", "unverifiable"
    ] | None = Field(
        default=None,
        description=(
            "The compiler's own `/check` verdict, obtained on the same code path as "
            "POST /check and consulted in `currency`. `None` means the diagnostics "
            "could not be obtained at all — which blocks `current`."
        ),
    )
    check_reason: str = Field(
        default="", description="One sentence explaining check_status."
    )
    generated_state: Literal["complete", "incomplete", "absent"] = Field(
        default="absent",
        description=(
            "Lock-free content comparison of the generated-nodes directory against the "
            "registry's lessons — one node file per lesson, no orphans. `incomplete` is "
            "definitive published-content disagreement and reads as stale even where "
            "the recompile-based check cannot run."
        ),
    )
    generated_reason: str = ""
    generated_missing_node_ids: list[str] = Field(default_factory=list)
    generated_orphan_node_ids: list[str] = Field(default_factory=list)
    currency_verifiable_without_toolchain: bool = Field(
        default=True,
        description=(
            "Always true. Detecting that registry content moved needs only the registry "
            "digest and the compiled route-index hash — never repo2md. Refreshing needs "
            "the toolchain; DETECTING does not, and an unavailable refresh must never "
            "make a stale or unknown state look current."
        ),
    )
    artifacts: list[ArtifactState] = Field(default_factory=list)
    staleness: StalenessState
    graph: GraphState
    toolchain: dict[str, Any] = Field(default_factory=dict)
    generated_policy: dict[str, str] = Field(default_factory=dict)


# ── lessons ─────────────────────────────────────────────────────────────────


class LessonLocation(BaseModel):
    area: str = ""
    file: str = ""
    line_hint: str = ""
    module: str = ""
    package: str = ""
    symbol: str = ""


class LessonFoundBy(BaseModel):
    type: str = ""
    trigger: str = ""


class LessonReferences(BaseModel):
    commit: str = ""
    issue: str = ""
    plan: str = ""
    pr: str = ""


class LessonSummary(BaseModel):
    """One canonical registry lesson plus its compiled routing facts.

    ``declared_paths`` is the expanded set the compiler derived from
    ``location.file`` (a single lesson may declare several paths — the project
    config names the separator). ``routed_paths`` is the subset that actually
    appears in the committed route index, so a lesson with declared paths but no
    routes is visibly uncompiled rather than silently absent.
    """

    id: str
    node_id: str
    overlay_node_id: str
    repository_id: str
    location: LessonLocation
    found_by: LessonFoundBy
    references: LessonReferences
    symptom: str = ""
    root_cause: str = ""
    fix: str = ""
    prevention: str = ""
    notes: str | None = None
    related: list[str] = Field(default_factory=list)
    date_found: str = ""
    date_found_precision: str = ""
    date_fixed: str = ""
    date_fixed_precision: str = ""
    declared_paths: list[str] = Field(default_factory=list)
    routed_paths: list[str] = Field(default_factory=list)
    routed_symbols: list[str] = Field(default_factory=list)
    documentation_only: bool = Field(
        default=False,
        description=(
            "Every declared path classifies as documentation. Such a lesson can "
            "only reach a task through the advisory lane, and only when an "
            "advisory tag matches — see DeliveryReport."
        ),
    )
    node_generated: bool = False


class LessonFacets(BaseModel):
    """Distinct filter values present in this registry, for the UI's controls."""

    categories: list[str] = Field(default_factory=list)
    areas: list[str] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)


class LessonsResponse(BaseModel):
    project_id: str
    repository_id: str
    total: int = Field(description="Lessons in the registry, before filtering.")
    count: int = Field(description="Lessons after filtering.")
    lessons: list[LessonSummary] = Field(default_factory=list)
    facets: LessonFacets = Field(default_factory=LessonFacets)


class LessonDetail(BaseModel):
    project_id: str
    repository_id: str
    lesson: LessonSummary
    node_path: str | None = None
    node_markdown: str | None = Field(
        default=None,
        description=(
            "The compiled node body, read from the generated-nodes tree. Reached "
            "via the node id / content_ref — never via a provider record's "
            "source_ref, which is the AKMS provenance enum ('human'), not a path."
        ),
    )
    related_lessons: list[LessonSummary] = Field(default_factory=list)
    routes: list["RouteEntry"] = Field(default_factory=list)


# ── routes + overlay ────────────────────────────────────────────────────────


class RouteReason(BaseModel):
    source_field: str = ""
    matched_text: str = ""


class RouteEntry(BaseModel):
    """One compiled route: a repository path or symbol → a lesson node."""

    kind: Literal["path", "symbol"]
    key: str
    node_id: str
    lesson_id: str | None = None
    overlay_edge_id: str
    match_semantics: str | None = None
    reasons: list[RouteReason] = Field(default_factory=list)
    source_exists: bool | None = Field(
        default=None,
        description="Whether the routed repository path is present in the checkout (path routes only).",
    )
    documentation: bool = Field(
        default=False,
        description="The routed path classifies as documentation (path routes only).",
    )


class OverlayNode(BaseModel):
    """A source-file or failure-lesson node in the overlay.

    ``id`` is ``"<repository_id>::<kind>:<key>"``. The repository prefix is not
    decoration: without it, ``src/config.py`` from two different repositories
    collapses into one node the moment both projects are shown together.
    """

    id: str
    kind: Literal["source_file", "failure_lesson"]
    project_id: str
    repository_id: str
    label: str
    path: str | None = None
    node_id: str | None = None
    lesson_id: str | None = None
    exists: bool | None = None
    documentation: bool | None = None
    selection_class: SelectionClass | None = None


class OverlayEdge(BaseModel):
    id: str
    source: str
    target: str
    type: OverlayEdgeType
    project_id: str
    repository_id: str
    label: str | None = None


class Overlay(BaseModel):
    """Failure-memory overlay for one project.

    Deliberately a separate graph document rather than nodes injected into
    ``/api/graph``: the plan requires failure memory to be an overlay, not a
    second writer into the AKMS source graph.
    """

    project_id: str
    repository_id: str
    nodes: list[OverlayNode] = Field(default_factory=list)
    edges: list[OverlayEdge] = Field(default_factory=list)


class RoutesResponse(BaseModel):
    project_id: str
    repository_id: str
    schema_version: str | None = None
    source_hash: str | None = None
    compiled: bool = Field(description="A committed route index exists on disk.")
    staleness: StalenessState
    routes: list[RouteEntry] = Field(default_factory=list)
    coverage: "RouteCoverage"
    overlay: Overlay


class RouteCoverage(BaseModel):
    """How much of the registry actually reaches the route index.

    ``by_symbol`` being empty is normal, not a bug: the compiler emits a symbol
    route only when the referenced file exists in the checkout, so a registry
    describing files that have since moved yields path routes and no symbol
    routes.
    """

    lesson_count: int = 0
    routed_lesson_count: int = 0
    unrouted_lesson_ids: list[str] = Field(default_factory=list)
    path_route_count: int = 0
    symbol_route_count: int = 0
    missing_source_paths: list[str] = Field(default_factory=list)
    documentation_path_count: int = 0


# ── resolve preview ─────────────────────────────────────────────────────────


class ResolveRequest(BaseModel):
    """A task or diff to preview, in Logic-Loom terms.

    Deliberately NOT the raw ``failure-memory-provider-request/v1`` object: the
    service composes that from these fields so the UI cannot hand the package a
    request whose ``repository_id`` disagrees with the selected project, and so
    ``output_dir`` — the only field that could make the provider write — is
    never caller-controlled.
    """

    task_id: str = Field(default="logic-loom-preview", min_length=1)
    title: str = ""
    objective: str = ""
    declared_paths: list[str] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    base: str | None = None
    head: str | None = None
    akms_tags: list[str] = Field(default_factory=list)
    mode: Literal["pre-task", "post-diff"] = "pre-task"
    role: str = "implementer"
    baseline: str = Field(default="logic-loom-preview")
    invocation_id: str = Field(default="logic-loom-preview", min_length=1)
    symbols: list[str] = Field(
        default_factory=list,
        description=(
            "Task symbol seeds. Emitted into the provider task ONLY when non-empty — "
            "the task object feeds result.fingerprint, so always sending an empty list "
            "would move every fingerprint away from the frozen contract's."
        ),
    )


class RecordView(BaseModel):
    """One provider record, presented for the UI.

    ``source_ref`` is passed through unchanged and is the AKMS v2 provenance
    enum — for compiled failure nodes its value is the literal string
    ``"human"``. The node body lives at ``content_ref``.
    """

    node_id: str
    lesson_id: str | None = None
    overlay_node_id: str
    selection_class: SelectionClass
    content_ref: str | None = None
    source_ref: str | None = None
    reasons: list[str] = Field(default_factory=list)


class DeliveryReport(BaseModel):
    """Why the required lane looks the way it does.

    The verdicts, worst-first:

    ``delivered_nowhere``
        At least one lesson has a LIVE compiled route matching an input path,
        and yet arrived in no lane at all. The frozen pack case
        ``docs_only_untagged_is_delivered_nowhere`` is exactly this: ``records ==
        []`` with ``status: "ok"``. The route is not missing; delivery was
        suppressed. Treating this as "no lessons apply" is the single most
        misleading thing this surface could do.
    ``downgraded_to_advisory``
        A routed lesson arrived, but only in the advisory lane. Frozen as
        ``docs_only_suppressed_to_advisory``.
    ``delivered``
        Every routed lesson arrived in the required lane.
    ``no_lessons_apply``
        No compiled route matches any considered path or symbol AND nothing was
        delivered at all. True by construction: the branch is only reachable
        with both an empty candidate set and an empty record set, so this
        verdict can never sit above a delivered lesson.

    The considered set is ``request_paths`` plus ``provider_derived_paths``. The
    second is not a detail: a ``post-diff`` resolution given ``base``/``head``
    supplies none of its paths in the request, and attributing such a call from
    the request alone reported ``no_lessons_apply`` directly above a selected
    REQUIRED lesson.

    ``documentation_only`` explains the two suppressed verdicts. AKMS drops
    every exact path spec when ALL of a task's path inputs classify as
    documentation, where documentation means any ``.md`` / ``.mdx`` / ``.rst`` /
    ``.adoc`` / ``.asciidoc`` file ANYWHERE (not only under ``docs/``), anything
    under ``doc/`` / ``docs/`` / ``documentation/``, and any basename starting
    with ``readme``. Adding one non-documentation path to the same task restores
    the SAME lesson to required — enforcement of a documentation lesson is
    contingent on unrelated paths in the task, which is why this block reports
    the classification of every input path rather than a single flag.

    The verdict itself is derived empirically — routed candidates versus
    delivered records — not from the path classifier. The classifier only
    explains; it never decides.
    """

    verdict: Literal[
        "no_lessons_apply", "delivered", "downgraded_to_advisory", "delivered_nowhere"
    ]
    headline: str
    input_paths: list[str] = Field(
        default_factory=list,
        description="Every path the resolution considered: request paths plus provider-derived ones.",
    )
    request_paths: list[str] = Field(
        default_factory=list, description="Paths the caller supplied."
    )
    provider_derived_paths: list[str] = Field(
        default_factory=list,
        description=(
            "Paths that came from the provider's own diff resolution rather than the "
            "request — a base/head post-diff call supplies none of its paths directly."
        ),
    )
    symbols: list[str] = Field(default_factory=list)
    empty_diff_fallback: bool = Field(
        default=False,
        description="The diff resolved to no paths and AKMS fell back to the declared scope.",
    )
    documentation_paths: list[str] = Field(default_factory=list)
    code_paths: list[str] = Field(default_factory=list)
    documentation_only: bool = False
    routed_candidate_node_ids: list[str] = Field(
        default_factory=list,
        description="Lesson nodes with a compiled route matching an input path — what SHOULD have been delivered.",
    )
    delivered_node_ids: list[str] = Field(default_factory=list)
    required_node_ids: list[str] = Field(default_factory=list)
    delivered_nowhere_node_ids: list[str] = Field(
        default_factory=list,
        description="Routed candidates that arrived in NO lane. Never render these as an empty state.",
    )
    downgraded_node_ids: list[str] = Field(
        default_factory=list,
        description="Routed candidates that arrived only as advisory.",
    )
    tag_only_node_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Records that arrived without a matching compiled path route — an "
            "advisory-tag match, or a load_with co-activation pulled in by another "
            "lesson. The two are different mechanisms and the headline names which."
        ),
    )


class ResolveResponse(BaseModel):
    project_id: str
    repository_id: str
    write_artifacts: bool = Field(
        default=False,
        description="Always false. Read-only preview never materialises provider artifacts.",
    )
    request: dict[str, Any] = Field(description="The provider request Logic-Loom composed and sent.")
    result: dict[str, Any] = Field(
        description="The provider result VERBATIM, so a consumer can diff it against the frozen fixture."
    )
    required: list[RecordView] = Field(default_factory=list)
    coactivated: list[RecordView] = Field(default_factory=list)
    advisory: list[RecordView] = Field(default_factory=list)
    delivery: DeliveryReport
    overlay: Overlay


# ── check ───────────────────────────────────────────────────────────────────


class CheckResponse(BaseModel):
    """Read-only compiler diagnostics: ``validate`` plus ``check``.

    ``check`` recompiles in memory and diffs against the committed bytes; it
    publishes nothing. A non-empty ``changed`` list after a recording is
    expected to cover the WHOLE generated tree, because every node embeds the
    whole-registry digest.
    """

    project_id: str
    repository_id: str
    status: Literal["clean", "drift", "invalid", "never_written", "unverifiable"]
    drift: bool = False
    validation: dict[str, Any] = Field(default_factory=dict)
    check: dict[str, Any] | None = None
    global_vault: str | None = None
    global_vault_exists: bool = False
    warnings: list[dict[str, str]] = Field(default_factory=list)


# ── operator ────────────────────────────────────────────────────────────────


class OperatorDisabledResponse(BaseModel):
    """Typed refusal returned by every operator endpoint outside operator mode.

    The plan allows operator endpoints to be absent OR to return a typed
    disabled response; a typed response is chosen so the UI can explain WHY the
    recording form is unavailable instead of rendering a bare 404.
    """

    status: Literal["disabled"]
    code: Literal["operator_mode_required"]
    message: str
    mode: FailureMemoryMode
    operator: bool
    mutating: bool = Field(
        default=False, description="Always false — this response performed nothing."
    )


# ── operator mode ───────────────────────────────────────────────────────────


class RecordLocation(BaseModel):
    """Closed key set, mirroring the package's ``_LOCATION_KEYS`` exactly."""

    model_config = ConfigDict(extra="forbid")

    area: str = ""
    file: str = Field(min_length=1)
    line_hint: str = ""
    module: str = ""
    package: str = ""
    symbol: str = ""


class RecordFoundBy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = ""
    trigger: str = ""


class RecordReferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commit: str = ""
    issue: str = ""
    plan: str = ""
    pr: str = ""


class RecordRequest(BaseModel):
    """One proposed lesson, in exactly the package recorder's closed key set.

    ``extra="forbid"`` is doing real work here. The recorder rejects a request
    carrying an ``id`` (``record_request``) because ids are allocated under the
    project lock; forbidding extras means such a request cannot even reach the
    package, and no other unknown field can either. Logic-Loom never invents an
    id, a node id, a route or a digest — it forwards this object unchanged.

    ``notes`` is the package's one optional field; every other key is required
    by ``_REQUIRED_REQUEST_KEYS``, which is why they are all present with
    defaults rather than optional.
    """

    model_config = ConfigDict(extra="forbid")

    date_found: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_found_precision: Literal["exact", "approximate"] = "exact"
    date_fixed: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_fixed_precision: Literal["exact", "approximate"] = "exact"
    location: RecordLocation
    found_by: RecordFoundBy = Field(default_factory=RecordFoundBy)
    references: RecordReferences = Field(default_factory=RecordReferences)
    symptom: str = ""
    root_cause: str = ""
    fix: str = ""
    prevention: str = ""
    related: list[str] = Field(default_factory=list)
    notes: str | None = None

    def to_proposal(self) -> dict[str, Any]:
        """The exact object handed to ``add_lesson``. No id, no derived fields."""
        proposal: dict[str, Any] = {
            "date_found": self.date_found,
            "date_found_precision": self.date_found_precision,
            "date_fixed": self.date_fixed,
            "date_fixed_precision": self.date_fixed_precision,
            "location": self.location.model_dump(),
            "found_by": self.found_by.model_dump(),
            "references": self.references.model_dump(),
            "symptom": self.symptom,
            "root_cause": self.root_cause,
            "fix": self.fix,
            "prevention": self.prevention,
            "related": list(self.related),
        }
        if self.notes is not None:
            proposal["notes"] = self.notes
        return proposal


#: Refresh actions Logic-Loom is willing to expose.
#:
#: ``clean`` is deliberately ABSENT. It deletes every artifact the project marks
#: disposable — graph, mirror, loadouts, provider outputs — and a destructive
#: cleanup one click away from a lesson inventory is not a feature, it is an
#: incident waiting for a mis-click. Operators who want it have the CLI.
#: ``status`` is absent because ``GET /status`` already serves it without a lock.
RefreshAction = Literal["preflight", "lessons", "mirror", "graph", "all"]


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: RefreshAction = "preflight"
    generated_at: str | None = Field(
        default=None,
        description=(
            "Explicit timezone-aware ISO-8601 instant. None = derive it from registry "
            "content (see services.failure_memory.derive_generated_at). Never wall clock: "
            "same content with a different generated_at reads as STALE, so a wall-clock "
            "value makes every freshness check fire on every refresh."
        ),
    )


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolchainRequirement(BaseModel):
    """One preflight requirement and whether this install satisfies it."""

    name: str
    satisfied: bool
    detail: str = ""


class WriteDiagnostics(BaseModel):
    """Can this Logic-Loom actually write to this project, and if not, why?

    Deliberately answers the question BEFORE an operator fills in a recording
    form, because every failure mode here is a configuration fact that is
    knowable in advance: the mode is read-only, the package is absent, the
    global vault does not exist, the project has never been written to, or the
    pinned repo2md toolchain is not installed.

    ``refresh_classification`` is the load-bearing field. A repo2md shortfall is
    ``unavailable`` — FIX THE TOOLCHAIN — and never ``stale``, which means THE
    KNOWLEDGE MOVED. Conflating them would make a permanently-failing gate
    indistinguishable from a genuine change, and the WWW stream classifies the
    identical condition the same way so the two surfaces cannot disagree.
    """

    project_id: str
    repository_id: str
    mode: FailureMemoryMode
    operator: bool
    package_installed: bool
    can_record: bool
    can_compile: bool
    can_refresh: bool
    never_written: bool = False
    global_vault: str
    global_vault_exists: bool
    lock_path: str
    lock_parent_exists: bool
    generated_at_source: str = Field(
        default="", description="The registry digest the derived timestamp comes from."
    )
    derived_generated_at: str | None = None
    refresh_classification: Literal["available", "unavailable"] = "available"
    refresh_reason: str = ""
    mirror_provider: Literal["builtin", "repo2md"] = Field(
        default="repo2md",
        description=(
            "The project's [toolchain].mirror_provider (akms-failure-memory 0.4+). "
            "'builtin' runs the exporter shipped in akms and needs no repo2md checkout; "
            "'repo2md' (the default for configs without the key) keeps the external tool."
        ),
    )
    repo2md_required_commit: str = Field(
        default="",
        description="The project's pinned [toolchain].repo2md_commit — the checkout refresh needs.",
    )
    repo2md_required_version: str = ""
    repo2md_command: list[str] = Field(default_factory=list)
    refresh_remediation: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered, concrete steps that enable refresh on this machine. repo2md is a "
            "documented DEPLOYMENT PREREQUISITE for the mutating refresh actions, not a "
            "known limitation — neither consumer vendors it, so in a normal deployment "
            "these steps are the expected content of this diagnostic."
        ),
    )
    detection_still_works: bool = Field(
        default=True,
        description=(
            "Whether staleness DETECTION still works while refresh is unavailable. It "
            "always does: it reads the registry digest and the route-index hash. Stated "
            "explicitly so an unavailable refresh is never mistaken for an inability to "
            "notice that the knowledge moved."
        ),
    )
    toolchain: list[ToolchainRequirement] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


LessonDetail.model_rebuild()
RoutesResponse.model_rebuild()
