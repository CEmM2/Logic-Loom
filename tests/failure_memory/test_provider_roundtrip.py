"""Permanent regression guard for the failure-memory init-layout defect.

Context: Logic-Loom adopted ``akms-failure-memory`` using the package's
canonical ``failure-memory init`` command. That was correct, but the package
itself had a BLOCKING defect: ``render_project_config``'s default ``[paths]``
put ``local_nodes``/``generated_nodes`` OUTSIDE ``akms_repo_root``, while
``akms.graph.build_graph`` hardcodes ``<akms_repo_root>/knowledge/local-nodes``
as the only local-node source. Compiled failure nodes therefore never reached
the graph, and ``resolve_provider`` raised ``FailureMemoryError`` with code
``resolve_error`` for EVERY route. Logic-Loom's initial registry started at
zero lessons, so ``validate``/``compile``/``check`` all legitimately passed —
the defect was invisible until the first real lesson was recorded. The
shared consumer-contract pack (AKMS repo) caught it; the upstream package was
fixed (upstream of the AKMS commit pinned in this repo's ``pyproject.toml``)
so ``init``'s default layout now nests ``local_nodes`` inside
``akms_repo_root``.

This test proves the fix end to end, against a THROWAWAY temp project (never
against Logic-Loom's real ``.failure-memory/``): it records a REAL lesson
through the public recorder, compiles, builds the REAL AKMS graph (not
mocked), and calls the REAL provider for a task that declares the recorded
lesson's path — asserting it comes back as a ``required`` record instead of
raising. If a future package regression reintroduces the layout defect, this
test fails loudly instead of silently passing the way an empty-registry
``validate``/``compile``/``check`` run did.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# Skip with a clear reason rather than an import error if the `failure-memory`
# extra hasn't been synced (`uv sync --extra failure-memory`).
akms_graph = pytest.importorskip("akms.graph.build_graph")
project = pytest.importorskip("akms_failure_memory.project")
record = pytest.importorskip("akms_failure_memory.record")
compiler = pytest.importorskip("akms_failure_memory.compiler")
provider = pytest.importorskip("akms_failure_memory.provider")
config_module = pytest.importorskip("akms_failure_memory.config")

build_graph = akms_graph.build_graph
init_project = project.init_project
add_lesson = record.add_lesson
run_compiler = compiler.run_compiler
resolve_provider = provider.resolve_provider
load_project_config = config_module.load_project_config


# Same identity Logic-Loom's real .failure-memory/config.toml uses (see
# dev/docs/failure-memory.md). Recorded ONLY in tmp_path, never in the real
# registry at repo root.
REPOSITORY_ID = "Logic-Loom"
NODE_NAMESPACE = "logic-loom-failure"

_PROPOSAL = {
    "date_fixed": "2026-08-16",
    "date_fixed_precision": "exact",
    "date_found": "2026-08-15",
    "date_found_precision": "exact",
    "fix": "Nest local_nodes/generated_nodes under akms_repo_root so compiled nodes are graph-visible.",
    "found_by": {
        "trigger": "Shared consumer-contract pack found the init-default layout defect",
        "type": "manual review",
    },
    "location": {
        "area": "src",
        "file": "src/failure_memory_demo/example.py",
        "line_hint": "provider round-trip proof",
        "module": "failure_memory_demo.example",
        "package": "failure_memory_demo",
        "symbol": "example_function",
    },
    "notes": (
        "Throwaway proof lesson recorded in a temp project copy for "
        "test_provider_roundtrip.py. Never written to Logic-Loom's real "
        ".failure-memory/lessons.json."
    ),
    "prevention": "tests/failure_memory/test_provider_roundtrip.py guards this permanently.",
    "references": {"commit": "", "issue": "", "plan": "", "pr": ""},
    "related": [],
    "root_cause": (
        "akms.graph.build_graph hardcodes <akms_repo_root>/knowledge/local-nodes "
        "as the only local-node source; failure-memory init previously put "
        "local_nodes outside akms_repo_root."
    ),
    "symptom": (
        "resolve_provider raised FailureMemoryError(code=resolve_error) for "
        "every route because compiled nodes never reached the graph."
    ),
}


def _provider_request(declared_path: str) -> dict:
    return {
        "schema_version": "failure-memory-provider-request/v1",
        "invocation_id": "ll-s1-correction-proof",
        "mode": "pre-task",
        "role": "implementer",
        "repository_id": REPOSITORY_ID,
        "task": {
            "task_id": "ll-s1-correction-proof",
            "title": "Touch the recorded demo file",
            "objective": "Regression: resolve_provider must return the recorded lesson, not raise.",
            "scope": [],
            "deliverables": [],
            "akms_tags": [],
        },
        "declared_paths": [declared_path],
        "changed_paths": [],
        "base": None,
        "head": None,
        "baseline": "ll-s1-correction-proof-baseline",
        "refresh_policy": "never",
        "output_dir": ".failure-memory/runtime/provider",
    }


def test_init_default_layout_feeds_the_graph_and_resolves(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    repo.mkdir()
    vault.mkdir()
    config_path = repo / ".failure-memory/config.toml"

    init_project(
        repository_root=repo,
        config_path=".failure-memory/config.toml",
        repository_id=REPOSITORY_ID,
        node_namespace=NODE_NAMESPACE,
    )
    config = load_project_config(config_path)

    # The regression this test exists to catch: local_nodes must live inside
    # akms_repo_root, or build_graph (which hardcodes
    # <akms_repo_root>/knowledge/local-nodes) can never see compiled nodes.
    assert config.resolve(repo, "local_nodes").is_relative_to(
        config.resolve(repo, "akms_repo_root")
    ), "init's default local_nodes must live inside akms_repo_root"

    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(json.dumps(_PROPOSAL), encoding="utf-8")

    created = add_lesson(
        config_path=config_path,
        repository_root=repo,
        global_vault=vault,
        request_path=proposal_path,
    )
    assert created["status"] == "created"
    node_id = f"{NODE_NAMESPACE}-l001"

    check = run_compiler(
        config_path=config_path, repository_root=repo, global_vault=vault, mode="check"
    )
    assert check["status"] == "clean", check

    graph = build_graph(
        config.resolve(repo, "akms_repo_root"),
        global_vault=vault,
        output_path=config.resolve(repo, "graph"),
        strict=True,
    )
    assert node_id in set(graph.nodes), "the recorded lesson's compiled node must reach the graph"

    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(_provider_request(_PROPOSAL["location"]["file"])), encoding="utf-8"
    )
    result = resolve_provider(
        config_path=config_path,
        repository_root=repo,
        request_source=request_path,
        write_artifacts=False,
    )

    assert result["status"] == "ok"
    assert [record["node_id"] for record in result["records"]] == [node_id]
    assert result["records"][0]["selection_class"] == "required"


# ── real-root regression (allowed_roots must cover the real source layout) ──

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG_PATH = _REPO_ROOT / ".failure-memory" / "config.toml"

_REAL_ROOT_PROPOSAL = {
    "date_fixed": "2026-08-17",
    "date_fixed_precision": "exact",
    "date_found": "2026-08-17",
    "date_found_precision": "exact",
    "fix": (
        "validation.allowed_roots now includes apps/services/packaging/scripts/dev, "
        "matching Logic-Loom's real top-level source layout (README.md ## Layout)."
    ),
    "found_by": {
        "trigger": (
            "Review finding: allowed_roots excluded "
            "apps/services, rejecting real services/logic_loom_api/... lesson paths"
        ),
        "type": "review finding",
    },
    "location": {
        "area": "services",
        "file": "services/logic_loom_api/src/logic_loom_api/main.py",
        "line_hint": "allowed_roots real-root regression",
        "module": "logic_loom_api.main",
        "package": "logic_loom_api",
        "symbol": "app",
    },
    "notes": (
        "Throwaway proof lesson recorded in a temp project copy for "
        "test_real_source_root_services_path_resolves. Never written to Logic-Loom's "
        "real .failure-memory/lessons.json."
    ),
    "prevention": (
        "tests/failure_memory/test_provider_roundtrip.py::"
        "test_real_source_root_services_path_resolves guards this permanently."
    ),
    "references": {"commit": "", "issue": "", "plan": "", "pr": ""},
    "related": [],
    "root_cause": (
        "failure-memory init's default validation.allowed_roots (src/tests/docs/tools/"
        ".github) does not match Logic-Loom's real top-level layout "
        "(apps/services/packaging/scripts/dev per README.md ## Layout), so the recorder "
        "rejected any lesson located under services/ or apps/."
    ),
    "symptom": (
        "add_lesson raised FailureMemoryError(code=registry_path) for a lesson whose "
        "location.file was services/logic_loom_api/... because that root was not in "
        "allowed_roots."
    ),
}


def test_real_source_root_services_path_resolves(tmp_path: Path) -> None:
    """Regression test: ``validation.allowed_roots`` must cover the real layout.

    Uses Logic-Loom's REAL committed ``.failure-memory/config.toml`` (copied into a
    throwaway temp project, never mutated in place), not a freshly re-initialized
    default -- so this also guards against ``validation.allowed_roots`` silently
    regressing back to a set that excludes ``apps/`` or ``services/``, which is
    where nearly all of Logic-Loom's source lives.

    Records a real lesson against a genuine ``services/logic_loom_api/...`` file
    (not a synthetic path), compiles, builds the real AKMS graph, and calls the
    real provider -- asserting the lesson comes back ``required``, not raising and
    not merely ``advisory``.
    """
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    (repo / ".failure-memory").mkdir(parents=True)
    vault.mkdir()
    config_path = repo / ".failure-memory" / "config.toml"
    config_path.write_bytes(_REAL_CONFIG_PATH.read_bytes())
    (repo / ".failure-memory" / "lessons.json").write_bytes(
        (_REPO_ROOT / ".failure-memory" / "lessons.json").read_bytes()
    )
    config = load_project_config(config_path)

    assert "apps" in config.validation["allowed_roots"]
    assert "services" in config.validation["allowed_roots"]

    real_location = _REAL_ROOT_PROPOSAL["location"]["file"]
    assert (_REPO_ROOT / real_location).is_file(), "the proof must reference a real file"

    proposal_path = tmp_path / "real-root-proposal.json"
    proposal_path.write_text(json.dumps(_REAL_ROOT_PROPOSAL), encoding="utf-8")

    created = add_lesson(
        config_path=config_path,
        repository_root=repo,
        global_vault=vault,
        request_path=proposal_path,
    )
    assert created["status"] == "created"
    # Not hardcoded to `-l001`: the real registry now already has at least
    # one real lesson recorded in it (see docs/release_notes/v0.2.0.md), so
    # this test's own synthetic proposal is appended after it and does not
    # necessarily land on L001. Use the ID the recorder actually allocated.
    node_id = f"{NODE_NAMESPACE}-{created['record']['id'].lower()}"

    check = run_compiler(
        config_path=config_path, repository_root=repo, global_vault=vault, mode="check"
    )
    assert check["status"] == "clean", check

    graph = build_graph(
        config.resolve(repo, "akms_repo_root"),
        global_vault=vault,
        output_path=config.resolve(repo, "graph"),
        strict=True,
    )
    assert node_id in set(graph.nodes)

    request_path = tmp_path / "real-root-request.json"
    request_path.write_text(json.dumps(_provider_request(real_location)), encoding="utf-8")
    result = resolve_provider(
        config_path=config_path,
        repository_root=repo,
        request_source=request_path,
        write_artifacts=False,
    )

    assert result["status"] == "ok"
    # Not a strict single-record equality: the real registry now carries the
    # first genuine lesson recorded through this same recorder (see
    # docs/release_notes/v0.2.0.md), which shares a `package-logic-loom-api`
    # advisory tag with this test's synthetic lesson and is legitimately
    # co-activated as `advisory` alongside it. What this test actually
    # guards -- that a lesson located under a real `services/...` root comes
    # back `required` via its exact path route, not raising and not demoted
    # to advisory-only -- still holds regardless of how many other real
    # lessons happen to be recorded.
    records_by_id = {record["node_id"]: record for record in result["records"]}
    assert node_id in records_by_id
    assert records_by_id[node_id]["selection_class"] == "required"
