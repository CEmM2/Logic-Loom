"""Operator mode: write boundary, bypass audit, job semantics.

Three questions this file exists to answer, in the order a reviewer asks them:

1. **Can read-only still not mutate, now that write code lives in the same
   codebase?** The read-only adapter must remain structurally incapable, not
   merely un-invoked.
2. **Is there any way to reach the registry other than through the package?**
   This is the single most important check in the slice. "Open JSON, append
   dict, save" would bypass the atomic writer, schema validation, id allocation
   and generated-artifact checks while looking, from outside, exactly like a
   successful recording.
3. **Can a failed background job leave something looking accepted?**

Everything runs against a REAL project with REAL lessons, for the reason this
whole program keeps rediscovering: an empty registry makes every check pass
while proving nothing.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.config import Settings
from logic_loom_api.deps import get_settings
from logic_loom_api.main import api

pytestmark = pytest.mark.integration

pytest.importorskip("akms_failure_memory", reason="run `uv sync --extra failure-memory`")

_SRC = Path(__file__).resolve().parents[1] / "src" / "logic_loom_api"

# Pinned AKMS release and consumer-contract pack digest.
#
# Logic-Loom installs ``akms`` / ``akms-failure-memory`` from PyPI (locked in
# ``uv.lock``): version 0.4.0 of both. The pack, however, lives only under
# ``tests/fixtures/`` in the AKMS source tree and is not part of any wheel, so
# it is fetched from the PUBLIC https://github.com/CEmM2/AKMS repository (no
# token) at the commit that the ``v0.4.0`` tag points to -- the exact source the
# PyPI 0.4.0 distributions were built from. The commit, not the tag name, is the
# immutable identity used for the fetch; the tag is recorded so a reader can
# verify it with:
#
#   git ls-remote https://github.com/CEmM2/AKMS.git 'refs/tags/v0.4.0^{}'
#
# The pack digest bd1c2dc3... is pack revision "amendment-2": AKMS re-froze the
# pack (CEmM2/AKMS #42) after the public repository scrubbed internal-plan
# references from it, which had left the v0.3.2 pack (90ad2801...) failing its
# own verify_pack.py. The akms release version no longer feeds the provider
# fingerprint, so a plain AKMS version bump does not by itself invalidate the
# pinned expected/*.json. These values still do NOT track AKMS's historical v1
# contracts/failure-memory-integration/v1/integration-lock.json (0.1.0 @
# 72494ac), which is deliberately left recording that artifact.
#
# Bumping these is a deliberate re-pin, never a routine edit: when the locked
# ``akms-failure-memory`` version changes, point PINNED_AKMS_COMMIT at the new
# release tag's commit, re-read the pack digest from its fixture-manifest.json,
# and keep all three call sites in step:
#   services/logic_loom_api/tests/test_failure_memory_conformance.py
#   services/logic_loom_api/tests/test_failure_memory_operator.py
#   tests/failure_memory/test_consumer_contract_pack.py
PINNED_AKMS_COMMIT = "937f7aa062dee10837e0dbb9b98cd80a4bd20495"  # tag v0.4.0
PACK_SUBPATH = "packages/akms_failure_memory/tests/fixtures/consumer_contract/v1"
EXPECTED_PACK_SHA256 = "bd1c2dc3a1d35ab41d7203aecce1bd1d39a494dde1532c9360b9e3518f2f8735"
AKMS_REPOSITORY = "https://github.com/CEmM2/AKMS.git"

PROPOSAL: dict[str, Any] = {
    "date_found": "2026-08-16",
    "date_fixed": "2026-08-16",
    "location": {
        "area": "src",
        "file": "src/engine/queue.py",
        "line_hint": "operator proof",
        "module": "engine.queue",
        "package": "engine",
        "symbol": "drain",
    },
    "found_by": {"type": "manual review", "trigger": "operator-mode proof"},
    "references": {"commit": "", "issue": "", "plan": "", "pr": ""},
    "symptom": "The queue drained out of order under load.",
    "root_cause": "The drain loop popped before committing the cursor.",
    "fix": "Commit the cursor first, then pop.",
    "prevention": "Regression test for out-of-order drain.",
    "related": [],
}


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("operator-pack")
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=destination, check=True, capture_output=True, text=True
    )
    try:
        run("init", "-q")
        run("remote", "add", "origin", AKMS_REPOSITORY)
        # The pack is verified by hashing the bytes git puts on disk, so the
        # checkout must be byte-exact. On Windows git defaults to
        # core.autocrlf=true and rewrites LF -> CRLF, which changes every file's
        # digest and makes verify_pack.py report "digest mismatch" for all of
        # expected/*.json while the manifest's own pack_sha256 still matches.
        # Disable the conversion for this throwaway clone.
        run("config", "core.autocrlf", "false")
        run("config", "core.eol", "lf")
        run("sparse-checkout", "init", "--cone")
        run("sparse-checkout", "set", PACK_SUBPATH)
        run("fetch", "--depth", "1", "origin", PINNED_AKMS_COMMIT)
        run("checkout", "-q", "FETCH_HEAD")
    except subprocess.CalledProcessError as exc:  # pragma: no cover - network
        pytest.skip(f"cannot fetch the pinned pack: {exc.stderr.strip()[:200]}")
    root = destination / PACK_SUBPATH
    manifest = json.loads((root / "fixture-manifest.json").read_text(encoding="utf-8"))
    assert manifest["checksum"]["pack_sha256"] == EXPECTED_PACK_SHA256
    return root


@pytest.fixture
def project(pack: Path, tmp_path: Path) -> tuple[Path, Path]:
    """A fresh writable copy per test — these tests mutate on purpose."""
    repo = tmp_path / "repo"
    shutil.copytree(pack, repo)
    vault = tmp_path / "vault"
    vault.mkdir()
    return repo, vault


def _client(repo: Path, vault: Path, mode: str, project_id: str = "pack") -> TestClient:
    settings = Settings(
        failure_memory_mode=mode,  # type: ignore[arg-type]
        akms_global_vault=vault,
        failure_memory_projects=[
            {"id": project_id, "config": str(repo / "project.toml"), "root": str(repo)}
        ],
    )
    api.dependency_overrides[get_settings] = lambda: settings
    return TestClient(api)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    api.dependency_overrides.pop(get_settings, None)


def _await(client: TestClient, job_id: str, timeout: float = 120.0) -> dict[str, Any]:
    from logic_loom_api.services.failure_memory_jobs import get_job_registry

    job = get_job_registry().wait(job_id, timeout=timeout)
    assert job is not None
    return job.model_dump()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _tree(relative: str) -> ast.Module:
    return ast.parse((_SRC / relative).read_text(encoding="utf-8"), filename=relative)


# ── 1. read-only remains structurally incapable ─────────────────────────────


def test_the_readonly_adapter_does_not_import_the_operator_adapter() -> None:
    """The dependency arrow points one way only.

    ``failure_memory_operator`` imports the read-only adapter for its shared
    types; the read-only adapter must not import back, or the audit that calls
    it write-free would stop being a statement about what it can reach.
    """
    for relative in ("adapters/failure_memory.py", "services/failure_memory.py"):
        imported: list[str] = []
        for node in ast.walk(_tree(relative)):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported += [alias.name for alias in node.names]
                if node.module:
                    imported.append(node.module)
        assert not any("failure_memory_operator" in name for name in imported), (
            f"{relative} imports the write-capable module: {imported}"
        )


def test_readonly_surface_still_cannot_mutate_with_operator_code_loaded(
    project: tuple[Path, Path],
) -> None:
    """Importing the write-capable module into the same process changes nothing.

    Presence is not reach. The operator adapter is imported here deliberately,
    then the entire read-only surface is exercised in read-only mode against a
    six-lesson project, and every byte is compared.
    """
    from logic_loom_api.adapters import failure_memory_operator  # noqa: F401

    repo, vault = project
    (repo / "runtime").mkdir()  # so read paths do not stop at lock_parent_missing
    client = _client(repo, vault, "read-only")
    before = _tree_digest(repo)
    before_paths = {str(p.relative_to(repo)) for p in repo.rglob("*")}

    assert client.get("/api/failure-memory/pack/status").status_code == 200
    assert client.get("/api/failure-memory/pack/lessons").status_code == 200
    assert client.get("/api/failure-memory/pack/routes").status_code == 200
    assert client.get("/api/failure-memory/pack/write-diagnostics").status_code == 200
    assert client.post("/api/failure-memory/pack/check").status_code == 200
    # Both a well-formed and a malformed body must produce the SAME typed
    # refusal: the permission answer must not depend on the shape of the request.
    for endpoint, body in (
        ("record", PROPOSAL),
        ("record", {}),
        ("record", {"garbage": True}),
        ("compile", {}),
        ("refresh", {"action": "graph"}),
        ("refresh", {"action": "clean"}),
    ):
        response = client.post(f"/api/failure-memory/pack/{endpoint}", json=body)
        assert response.status_code == 403, (endpoint, body)
        assert response.json()["code"] == "operator_mode_required"
        assert response.json()["mutating"] is False

    assert {str(p.relative_to(repo)) for p in repo.rglob("*")} == before_paths
    assert _tree_digest(repo) == before


def test_the_only_write_artifacts_argument_is_still_a_false_literal() -> None:
    """The read-only surface's structural property, re-asserted now that write code exists."""
    literals = []
    for node in ast.walk(_tree("adapters/failure_memory.py")):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "write_artifacts":
                    assert isinstance(keyword.value, ast.Constant)
                    literals.append(keyword.value.value)
    assert literals == [False]

    from logic_loom_api.adapters import failure_memory as readonly

    assert not hasattr(readonly, "WRITE_ARTIFACTS")


# ── 2. no direct registry write bypass ──────────────────────────────────────


#: Names that create, truncate, move or delete. The operator module is allowed
#: exactly one write — the proposal JSON, in an OS temp directory — because
#: ``add_lesson``'s public signature takes a path and offers no in-memory form.
_ALLOWED_OPERATOR_WRITES = {"write_text"}
_FORBIDDEN_WRITES = frozenset(
    {
        "write_bytes",
        "mkdir",
        "makedirs",
        "unlink",
        "rmdir",
        "rmtree",
        "remove",
        "rename",
        "replace",
        "touch",
        "copy",
        "copy2",
        "copyfile",
        "copytree",
        "move",
        "dump",
        "writelines",
        "truncate",
    }
)


def test_the_operator_adapter_writes_only_the_temp_proposal() -> None:
    offenders = []
    writes = []
    for node in ast.walk(_tree("adapters/failure_memory_operator.py")):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else getattr(node.func, "id", None)
        )
        if name in _FORBIDDEN_WRITES:
            offenders.append(f"line {node.lineno}: {name}(...)")
        if name in _ALLOWED_OPERATOR_WRITES:
            writes.append(f"line {node.lineno}: {name}(...)")
        if name == "open":
            offenders.append(f"line {node.lineno}: open(...)")
    assert not offenders, "unexpected write in the operator adapter:\n" + "\n".join(offenders)
    assert len(writes) == 1, f"expected exactly one write (the proposal), got {writes}"


def test_the_operator_adapter_uses_only_the_public_write_api() -> None:
    """Three package entry points, and no re-implementation of any of them."""
    seen: dict[str, set[str]] = {}
    for node in ast.walk(_tree("adapters/failure_memory_operator.py")):
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_module"
            and value.args
            and isinstance(value.args[0], ast.Constant)
        ):
            continue
        seen.setdefault(str(value.args[0].value), set()).add(node.attr)
    assert seen == {
        "record": {"add_lesson"},
        "compiler": {"run_compiler"},
        "refresh": {"refresh_project"},
    }, seen


def test_the_compiler_write_mode_is_a_literal() -> None:
    """The mirror of the read-only adapter's literal ``False``."""
    modes = []
    for node in ast.walk(_tree("adapters/failure_memory_operator.py")):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "mode":
                assert isinstance(keyword.value, ast.Constant), (
                    "the compiler mode must be a literal, never a rebindable name"
                )
                modes.append(keyword.value.value)
    assert modes == ["write"]


def test_no_module_anywhere_in_the_service_names_the_registry_file() -> None:
    """Nothing in the whole application refers to a project registry by name.

    A hard-coded ``lessons.json`` in application code is the fingerprint of the
    bypass this slice must not contain — the registry's filename is project
    configuration (``paths.registry``), and every legitimate access goes through
    the package.
    """
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # Only STRING LITERALS in executable positions count. Prose in a
        # docstring explaining that Logic-Loom never touches lessons.json is the
        # opposite of the defect, so scanning raw text would flag the very
        # comments that document the rule.
        docstrings = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(tree)
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if "lessons.json" not in node.value or node.value in docstrings:
                continue
            offenders.append(f"{path.relative_to(_SRC)}:{node.lineno}: {node.value[:80]!r}")
    assert not offenders, (
        "application code names a project registry file directly:\n" + "\n".join(offenders)
    )


def test_a_failing_recorder_leaves_the_registry_untouched(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Logic-Loom contributes no write path of its own.

    ``add_lesson`` is replaced with something that raises. If any part of the
    recording were implemented on the Logic-Loom side, the registry would move
    anyway. It does not.
    """
    import akms_failure_memory.record as recorder

    repo, vault = project
    (repo / "runtime").mkdir()
    before = (repo / "lessons.json").read_bytes()
    before_tree = _tree_digest(repo)

    def explode(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("recorder refused")

    monkeypatch.setattr(recorder, "add_lesson", explode)
    client = _client(repo, vault, "operator")
    created = client.post("/api/failure-memory/pack/record", json=PROPOSAL)
    assert created.status_code == 202
    job = _await(client, created.json()["id"])

    assert job["status"] == "failed"
    assert job["result"] is None
    assert (repo / "lessons.json").read_bytes() == before
    assert _tree_digest(repo) == before_tree


def test_recording_goes_through_the_package_end_to_end(
    project: tuple[Path, Path],
) -> None:
    """The positive proof: a real recording, checked against the package itself.

    Four independent confirmations that Logic-Loom contributed no bytes:
    the id was ALLOCATED (Logic-Loom never sends one and cannot), the resulting
    registry is byte-identical to the package's own canonical serialisation, the
    package's validator accepts it, and a fresh ``check`` reports ``clean`` —
    which it could not if the generated tree had been written by anything other
    than the compiler.
    """
    from akms_failure_memory.compiler import run_compiler, validate_registry_bytes
    from akms_failure_memory.config import load_project_config

    repo, vault = project
    client = _client(repo, vault, "operator")
    before_ids = [
        lesson["id"]
        for lesson in json.loads((repo / "lessons.json").read_text(encoding="utf-8"))["lessons"]
    ]

    created = client.post("/api/failure-memory/pack/record", json=PROPOSAL)
    assert created.status_code == 202
    assert created.json()["status"] == "queued", "202 must not claim success"
    job = _await(client, created.json()["id"])
    assert job["status"] == "succeeded", job

    allocated = job["result"]["record"]["id"]
    assert allocated not in before_ids
    assert "id" not in PROPOSAL, "Logic-Loom must never send an id"

    config = load_project_config(repo / "project.toml")
    registry_bytes = (repo / "lessons.json").read_bytes()
    lessons = validate_registry_bytes(registry_bytes, repo / "lessons.json", config)
    assert [lesson["id"] for lesson in lessons] == [*before_ids, allocated]
    assert registry_bytes == (
        json.dumps(
            json.loads(registry_bytes.decode("utf-8")), ensure_ascii=False, indent=2
        )
        + "\n"
    ).encode("utf-8"), "the registry is not the package's canonical serialisation"

    assert (
        run_compiler(
            config_path=repo / "project.toml",
            repository_root=repo,
            global_vault=vault,
            mode="check",
        )["status"]
        == "clean"
    )

    # Every generated node embeds the whole-registry digest, so one lesson
    # rewrites the whole tree. Expected output, not damage.
    assert len(job["result"]["affected_outputs"]) > 1
    assert "full-tree diff here is correct output" in job["summary"]


def test_the_record_request_model_refuses_an_id_and_any_extra_field(
    project: tuple[Path, Path],
) -> None:
    repo, vault = project
    (repo / "runtime").mkdir()
    client = _client(repo, vault, "operator")
    for smuggled in ({"id": "L900"}, {"node_id": "x"}, {"source_sha256": "0" * 64}):
        response = client.post(
            "/api/failure-memory/pack/record", json={**PROPOSAL, **smuggled}
        )
        assert response.status_code == 422, smuggled
        assert response.json()["error"]["code"] == "validation_error"
    assert json.loads((repo / "lessons.json").read_text(encoding="utf-8"))["lessons"][-1][
        "id"
    ] == "L006"


# ── 3. background failure cannot look accepted ──────────────────────────────


def test_a_queued_job_never_carries_a_result(project: tuple[Path, Path]) -> None:
    repo, vault = project
    client = _client(repo, vault, "operator")
    created = client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()
    assert created["status"] == "queued"
    assert created["result"] is None
    assert created["error"] is None
    assert created["finished_at"] is None
    _await(client, created["id"])


def test_success_and_failure_are_never_half_published(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A succeeded job always has a result; a failed job never does.

    That pairing is what stops a UI rendering "done" while holding nothing to
    show for it, or rendering an error beside a result that exists.
    """
    import akms_failure_memory.record as recorder

    repo, vault = project
    client = _client(repo, vault, "operator")

    ok = _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])
    assert ok["status"] == "succeeded" and ok["result"] is not None and ok["error"] is None

    monkeypatch.setattr(
        recorder, "add_lesson", lambda **_k: (_ for _ in ()).throw(RuntimeError("nope"))
    )
    bad = _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])
    assert bad["status"] == "failed" and bad["result"] is None and bad["error"] is not None


def test_a_late_failure_reports_failed_not_succeeded(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The precise hazard: work happened, then the call raised.

    A recorder that mutates and then fails must still produce a FAILED job.
    Success is reported only from a returned value, never from "we got far
    enough". (The real ``add_lesson`` additionally rolls the registry back in
    this situation; this asserts Logic-Loom's half of the contract regardless.)
    """
    import akms_failure_memory.record as recorder

    repo, vault = project
    (repo / "runtime").mkdir()
    marker = repo / "runtime" / "late-failure-marker"

    def half_done(**_kwargs: Any) -> dict[str, Any]:
        marker.write_text("work happened", encoding="utf-8")
        raise RuntimeError("failed after doing work")

    monkeypatch.setattr(recorder, "add_lesson", half_done)
    client = _client(repo, vault, "operator")
    job = _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])

    assert marker.exists(), "the simulated recorder did do work"
    assert job["status"] == "failed"
    assert job["result"] is None
    assert "failed after doing work" in job["error"]["message"]


def test_jobs_are_listable_and_scoped_to_their_project(
    project: tuple[Path, Path],
) -> None:
    repo, vault = project
    client = _client(repo, vault, "operator", project_id="scoped")
    created = client.post("/api/failure-memory/scoped/record", json=PROPOSAL).json()
    _await(client, created["id"])
    listed = client.get("/api/failure-memory/jobs", params={"project": "scoped"}).json()
    assert [job["id"] for job in listed][:1] == [created["id"]]
    assert client.get(f"/api/failure-memory/jobs/{created['id']}").status_code == 200
    assert client.get("/api/failure-memory/jobs/nope").status_code == 404


# ── 3b. currency precedence: evidence outranks a missing scratch directory ──


def test_a_cold_clone_with_a_hand_edited_registry_reports_stale(
    project: tuple[Path, Path],
) -> None:
    """A stale cold clone must read `stale`, not `never_written`.

    A fresh clone is the ORDINARY case: registry and routes committed,
    disposable `runtime/` absent. Proof that content moved must never be
    overridden by the absence of a scratch directory.
    """
    repo, vault = project
    # Exactly a cold clone: committed registry + committed routes + generated
    # nodes, and no runtime/ at all.
    assert (repo / "lessons.json").is_file()
    assert (repo / "generated" / "routes.json").is_file()
    assert not (repo / "runtime").exists()

    registry = json.loads((repo / "lessons.json").read_text(encoding="utf-8"))
    registry["lessons"][0]["symptom"] = "hand edited in a cold clone"
    (repo / "lessons.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    client = _client(repo, vault, "operator")
    diagnostics = client.get("/api/failure-memory/pack/write-diagnostics").json()
    if diagnostics["refresh_classification"] == "available":  # pragma: no cover
        pytest.skip("this environment has a conforming pinned repo2md checkout")

    status = client.get("/api/failure-memory/pack/status").json()
    assert status["currency"] == "stale", (
        "a cold clone with a hand-edited registry was laundered into "
        f"{status['currency']!r}"
    )
    assert status["staleness"]["state"] == "stale"
    assert status["never_written"] is False, "committed artifacts exist"
    assert status["lock_parent_exists"] is False, "and the scratch dir is genuinely absent"
    assert client.get("/api/failure-memory/projects").json()["projects"][0][
        "never_written"
    ] is False

    # The check cannot run without the lock, so it must claim nothing either way.
    check = client.post("/api/failure-memory/pack/check").json()
    assert check["status"] == "unverifiable", check
    assert check["status"] != "clean"
    assert check["status"] != "never_written"


def test_lock_absence_never_overrides_content_evidence() -> None:
    """The precedence rule itself, exercised directly against the owner module."""
    from logic_loom_api.services.failure_memory_currency import (
        GeneratedCoverage,
        GraphVerification,
        WriteHistory,
        resolve_currency,
    )

    # The check evidence in a cold clone is honestly `unverifiable`; the point
    # of these cases is that content evidence still wins over absence claims.
    verified = GraphVerification(state="verified", reason="ok", graph_node_count=6)
    for absence_claim in (True, False):
        verdict, _ = resolve_currency(
            history=WriteHistory(never_written=absence_claim, reason=""),
            routes_state="stale",
            graph=verified,
            check_status="unverifiable",
            generated=GeneratedCoverage(state="complete", reason=""),
        )
        assert verdict == "stale", "stale must outrank a never-written claim either way"

    verdict, _ = resolve_currency(
        history=WriteHistory(never_written=True, reason=""),
        routes_state="unknown",
        graph=GraphVerification(state="absent", reason=""),
        check_status="unverifiable",
        generated=GeneratedCoverage(state="absent", reason=""),
    )
    assert verdict == "unverifiable", "an unreadable digest outranks absence too"

    verdict, _ = resolve_currency(
        history=WriteHistory(never_written=True, reason="nothing here"),
        routes_state="uncompiled",
        graph=GraphVerification(state="absent", reason=""),
        check_status="never_written",
        generated=GeneratedCoverage(state="absent", reason=""),
    )
    assert verdict == "never_written", "with no evidence at all, absence may speak"


def test_a_contradicted_never_written_claim_is_demoted() -> None:
    """When this surface's own signals disagree, the reassuring one must lose.

    The reviewer's sharpest finding was an internal contradiction: `/check`
    reported `drift` while the top-level verdict said `never_written`. Any
    disagreement now demotes the claim to `unverifiable`.
    """
    from logic_loom_api.services.failure_memory_currency import (
        GeneratedCoverage,
        GraphVerification,
        WriteHistory,
        resolve_currency,
    )

    absent = GraphVerification(state="absent", reason="")
    no_tree = GeneratedCoverage(state="absent", reason="")
    for contradiction in (
        {"lesson_count": 3, "check_status": "never_written"},
        {"check_status": "drift"},
        {"check_status": "invalid"},
    ):
        verdict, reason = resolve_currency(
            history=WriteHistory(never_written=True, reason="nothing here"),
            routes_state="uncompiled",
            graph=absent,
            **{"generated": no_tree, **contradiction},
        )
        assert verdict == "unverifiable", contradiction
        assert "contradicted" in reason

    # A surviving generated tree beside a never-written claim is stronger than a
    # contradiction: node files without their route index are TORN published
    # output, which is definitive and reads as stale, not merely undetermined.
    verdict, reason = resolve_currency(
        history=WriteHistory(never_written=True, reason="nothing here"),
        routes_state="uncompiled",
        graph=absent,
        check_status="never_written",
        generated=GeneratedCoverage(state="complete", reason=""),
    )
    assert verdict == "stale", "a surviving generated tree must defeat the claim"
    assert "torn" in reason

    verdict, reason = resolve_currency(
        history=WriteHistory(
            never_written=True, evidence={"routes": True}, reason="claims nothing"
        ),
        routes_state="uncompiled",
        graph=absent,
        check_status="never_written",
        generated=no_tree,
    )
    assert verdict == "unverifiable", "surviving artifacts contradict the claim"
    assert "artifacts exist" in reason


def test_a_genuinely_fresh_project_is_still_non_blocking_and_recordable(
    tmp_path: Path,
) -> None:
    """The legitimate fresh case stays non-blocking and recordable.

    A freshly ``init``ed project reports ``uncompiled`` (it owns a registry
    FILE, so ``never_written`` would over-claim): non-blocking, claiming
    nothing, still recordable.
    """
    from akms_failure_memory.project import init_project

    repo = tmp_path / "fresh"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "config.py").write_text("SETTING = 1\n", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    init_project(
        repository_root=repo,
        config_path=".failure-memory/config.toml",
        repository_id="Fresh",
        node_namespace="fresh-failure",
    )
    settings = Settings(
        failure_memory_mode="operator",
        akms_global_vault=vault,
        failure_memory_projects=[
            {"id": "fresh", "config": str(repo / ".failure-memory" / "config.toml"), "root": str(repo)}
        ],
    )
    api.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(api)

    listed = client.get("/api/failure-memory/projects").json()["projects"][0]
    status = client.get("/api/failure-memory/fresh/status").json()
    assert listed["available"] is True
    assert status["currency"] == "uncompiled"
    assert status["currency"] not in {"current", "stale"}
    assert status["lesson_count"] == 0

    proposal = {
        **PROPOSAL,
        "location": {**PROPOSAL["location"], "file": "src/config.py", "area": "src"},
    }
    job = _await(client, client.post("/api/failure-memory/fresh/record", json=proposal).json()["id"])
    assert job["status"] == "succeeded", job
    assert client.get("/api/failure-memory/fresh/status").json()["currency"] != "never_written"


def test_a_project_with_nothing_at_all_is_never_written(tmp_path: Path) -> None:
    """The one state that legitimately earns the label, and it stays non-blocking."""
    from akms_failure_memory.project import init_project

    repo = tmp_path / "empty"
    repo.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    initialized = init_project(
        repository_root=repo,
        config_path=".failure-memory/config.toml",
        repository_id="Empty",
        node_namespace="empty-failure",
    )
    # Remove the scaffolded registry so the project genuinely owns NOTHING.
    # Its location is the package's choice (akms-failure-memory 0.4 moved it to
    # .knowledge/lessons.json), so take it from init_project's own report.
    (repo / initialized["registry"]).unlink()

    settings = Settings(
        akms_global_vault=vault,
        failure_memory_projects=[
            {"id": "empty", "config": str(repo / ".failure-memory" / "config.toml"), "root": str(repo)}
        ],
    )
    api.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(api)
    listed = client.get("/api/failure-memory/projects").json()["projects"][0]
    assert listed["available"] is True, "never-written is not unavailable"
    assert listed["never_written"] is True


def test_history_outranks_artifact_absence(project: tuple[Path, Path]) -> None:
    """Record, then erase every trace: the label must not become reassuring.

    Record a real lesson, validly empty the registry, clean the disposable
    outputs — surviving evidence of history must keep `never_written` off the
    table.
    """
    from akms_failure_memory.refresh import refresh_project

    repo, vault = project
    client = _client(repo, vault, "operator")
    job = _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])
    assert job["status"] == "succeeded"

    registry = json.loads((repo / "lessons.json").read_text(encoding="utf-8"))
    registry["lessons"] = []
    (repo / "lessons.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    refresh_project(
        action="clean",
        config_path=repo / "project.toml",
        repository_root=repo,
        global_vault=vault,
    )

    status = client.get("/api/failure-memory/pack/status").json()
    check = client.post("/api/failure-memory/pack/check").json()
    assert status["currency"] != "never_written", (
        f"history was laundered into {status['currency']!r} after record + empty + clean"
    )
    assert status["currency"] != "current"
    assert status["never_written"] is False
    # And no internal contradiction: whatever /check says, the verdict agrees it
    # is not a clean slate.
    assert check["status"] in {"drift", "clean", "unverifiable", "invalid"}
    if check["status"] == "drift":
        assert status["currency"] in {"stale", "unverifiable", "uncompiled"}


# ── 3c. job retention and evidence ──# ── 3c. job retention and evidence ──────────────────────────────────────────


def test_retention_never_evicts_a_nonterminal_job() -> None:
    """A job that may still write is not a log entry to be trimmed.

    A 101-job probe used to evict a RUNNING job whose write then completed with
    no observable status and no evidence — the partially-accepted state this
    module exists to prevent, arriving through the retention policy rather than
    the success path. The cap now bounds terminal history only, and is
    deliberately exceeded while work is in flight.
    """
    import threading

    from logic_loom_api.services.failure_memory_jobs import JobRegistry

    # max_inflight raised well past the probe size: this test is about
    # RETENTION, and the admission bound is exercised separately below.
    registry = JobRegistry(max_retained=5, max_inflight=1000)
    gate = threading.Event()
    blocker = registry.submit(
        project_id="p",
        action="compile",
        detail="blocking",
        work=lambda: {"ok": gate.wait(30)},
        summarize=lambda _r: "blocked then finished",
    )
    queued = [
        registry.submit(
            project_id="p",
            action="compile",
            detail=str(index),
            work=lambda: {"ok": True},
            summarize=lambda _r: "done",
        )
        for index in range(150)
    ]

    # THE DEFECT: 151 live jobs against a cap of 5. Not one may be dropped.
    retained = registry.list(limit=1000)
    assert len(retained) == 151, "the cap must be exceeded rather than drop live jobs"
    assert registry.get(blocker.id) is not None, "the running job was evicted"
    assert all(registry.get(job.id) is not None for job in queued)
    assert all(not job.terminal for job in retained)

    gate.set()
    finished = registry.wait(queued[-1].id, timeout=60)
    assert finished is not None and finished.status == "succeeded"

    # Reaping no longer happens on the terminal-publication path — that let the
    # first completed job vanish before anyone could read it. It happens on the
    # next submission, oldest terminal first.
    observed = registry.list(limit=1000)
    assert len(observed) == 151, "no submission has happened, so nothing was trimmed"
    assert all(job.terminal for job in observed)

    registry.submit(
        project_id="p",
        action="compile",
        detail="trigger-reap",
        work=lambda: {"ok": True},
        summarize=lambda _r: "done",
    )
    remaining = registry.list(limit=1000)
    assert len(remaining) <= 6, remaining
    assert all(job.terminal or job.status in ("queued", "running") for job in remaining)


def test_a_blocked_jobs_evidence_survives_its_terminal_publication() -> None:
    """The other half: a job retained through completion keeps its evidence.

    Retention reaps oldest-terminal-first, so a long-running job is eventually
    aged out of history like any other — but only AFTER its terminal state and
    result were published and observable, which is the property that matters.
    """
    import threading

    from logic_loom_api.services.failure_memory_jobs import JobRegistry

    registry = JobRegistry(max_retained=100, max_inflight=1000)
    gate = threading.Event()
    blocker = registry.submit(
        project_id="p",
        action="record",
        detail="blocking",
        work=lambda: {"status": "created", "record": {"id": "L009"}},
        summarize=lambda _r: "recorded",
    )
    for index in range(20):
        registry.submit(
            project_id="p",
            action="compile",
            detail=str(index),
            work=lambda: {"ok": True},
            summarize=lambda _r: "done",
        )
    gate.set()

    settled = registry.wait(blocker.id, timeout=60)
    assert settled is not None
    assert settled.status == "succeeded"
    assert settled.result == {"status": "created", "record": {"id": "L009"}}
    assert settled.finished_at is not None


def test_terminal_evidence_is_observable_before_it_becomes_reapable() -> None:
    """A concurrent poller must see `succeeded`, never `running -> missing`.

    Reaping used to run in the same lock acquisition that published the terminal
    state, so with a small retention cap the FIRST completed job could be gone
    before any observer read it. A job that completed and vanished is
    indistinguishable from one that never ran.
    """
    import threading
    import time as _time

    from logic_loom_api.services.failure_memory_jobs import JobRegistry

    registry = JobRegistry(max_retained=100, max_inflight=200)
    first = registry.submit(
        project_id="p",
        action="record",
        detail="first",
        work=lambda: {"status": "created"},
        summarize=lambda _r: "recorded",
    )

    seen: list[str] = []
    stop = threading.Event()

    def poll() -> None:
        for _ in range(600):
            job = registry.get(first.id)
            seen.append("missing" if job is None else job.status)
            if job is not None and job.terminal:
                stop.set()
                return
            _time.sleep(0.005)
        stop.set()

    poller = threading.Thread(target=poll)
    poller.start()
    # Keep submitting while the first job runs and settles — this is what used to
    # evict it out from under the poller. The retention cap is the realistic
    # default order of magnitude: retention is a recency cap, and the property
    # under test is that PUBLICATION never reaps, so a poller in the same
    # instant always sees the terminal state.
    for index in range(30):
        registry.submit(
            project_id="p",
            action="compile",
            detail=str(index),
            work=lambda: {"ok": True},
            summarize=lambda _r: "done",
        )
    poller.join(timeout=30)

    assert "succeeded" in seen, f"the poller never observed a terminal state: {seen[:10]}"
    assert "missing" not in seen[: seen.index("succeeded") + 1], (
        f"the job vanished before it was observed: {seen[:10]}"
    )


def test_admission_is_bounded_and_over_capacity_is_rejected_not_dropped() -> None:
    """Bound what is ACCEPTED; never silently drop accepted work.

    Retaining every nonterminal job is the right retention rule, and it turns an
    unbounded queue into an unbounded memory commitment feeding a single worker.
    So the queue is finite and an over-capacity submission is refused outright —
    nothing is created and nothing is started, so a caller that got a 202 can
    rely on it.
    """
    import threading

    from logic_loom_api.services.failure_memory_jobs import JobAdmissionError, JobRegistry

    registry = JobRegistry(max_inflight=3)
    gate = threading.Event()
    accepted = [
        registry.submit(
            project_id="p",
            action="compile",
            detail=str(index),
            work=lambda: {"ok": gate.wait(20)},
            summarize=lambda _r: "done",
        )
        for index in range(3)
    ]
    assert registry.inflight() == 3

    with pytest.raises(JobAdmissionError) as raised:
        registry.submit(
            project_id="p",
            action="compile",
            detail="overflow",
            work=lambda: {"ok": True},
            summarize=lambda _r: "done",
        )
    assert raised.value.code == "job_queue_full"
    assert raised.value.limit == 3
    assert len(registry.list(limit=1000)) == 3, "the rejected job must not exist"

    gate.set()
    for job in accepted:
        settled = registry.wait(job.id, timeout=30)
        assert settled is not None and settled.status == "succeeded", "accepted work ran"


def test_a_full_queue_returns_a_typed_429(project: tuple[Path, Path]) -> None:
    """The rejection reaches the caller as a typed envelope, not a 500."""
    import threading

    from logic_loom_api.services import failure_memory_jobs
    from logic_loom_api.services.failure_memory_jobs import JobRegistry

    repo, vault = project
    (repo / "runtime").mkdir()
    registry = JobRegistry(max_inflight=1)
    gate = threading.Event()
    registry.submit(
        project_id="p",
        action="compile",
        detail="hold",
        work=lambda: {"ok": gate.wait(20)},
        summarize=lambda _r: "done",
    )
    original = failure_memory_jobs._registry
    failure_memory_jobs._registry = registry
    try:
        client = _client(repo, vault, "operator")
        response = client.post("/api/failure-memory/pack/compile", json={})
    finally:
        failure_memory_jobs._registry = original
        gate.set()

    assert response.status_code == 429, response.text
    body = response.json()["error"]
    assert body["code"] == "job_queue_full"
    assert body["detail"]["accepted"] is False


def test_success_requires_validated_evidence() -> None:
    """"Did not raise" is not "produced a result".

    A callable returning ``None`` satisfied the old rule while producing nothing
    an operator could inspect, so the job published ``succeeded`` with
    ``result: null`` — a green tick over an empty hand.
    """
    from logic_loom_api.services.failure_memory_jobs import JobRegistry

    registry = JobRegistry()
    for label, value in (("none", None), ("non-mapping", ["written"]), ("empty", {})):
        job = registry.wait(
            registry.submit(
                project_id="p",
                action="record",
                detail=label,
                work=lambda value=value: value,
                summarize=lambda _r: "should never be reached",
            ).id,
            timeout=30,
        )
        assert job is not None
        assert job.status == "failed", f"{label} was published as a success"
        assert job.result is None
        assert job.error is not None and job.error.code == "missing_result_evidence"

    good = registry.wait(
        registry.submit(
            project_id="p",
            action="record",
            detail="real",
            work=lambda: {"status": "created"},
            summarize=lambda _r: "ok",
        ).id,
        timeout=30,
    )
    assert good is not None and good.status == "succeeded" and good.result == {"status": "created"}


# ── 4. write diagnostics, toolchain classification, generated_at ────────────


def test_refresh_shortfall_is_unavailable_and_never_stale(
    project: tuple[Path, Path],
) -> None:
    """Must agree with the WWW stream's classification of the identical condition.

    Every mutating refresh action runs the package's toolchain preflight, which
    demands a verifiable, pinned, clean, EDITABLE repo2md distribution. An
    install from the pinned AKMS git commit alone cannot have one. That is
    ``unavailable`` — FIX THE TOOLCHAIN — and must never be reported as
    ``stale``, which means THE KNOWLEDGE MOVED. A gate that reports a permanent
    toolchain shortfall as staleness is indistinguishable from a broken one.
    """
    repo, vault = project
    (repo / "runtime").mkdir()
    client = _client(repo, vault, "operator")

    diagnostics = client.get("/api/failure-memory/pack/write-diagnostics").json()
    assert diagnostics["refresh_classification"] in {"available", "unavailable"}
    if diagnostics["refresh_classification"] == "available":  # pragma: no cover
        pytest.skip("this environment has a conforming pinned repo2md checkout")

    assert diagnostics["can_refresh"] is False
    assert diagnostics["can_record"] is True, "a repo2md shortfall must not block recording"
    assert diagnostics["can_compile"] is True
    assert "stale" not in diagnostics["refresh_classification"]
    assert "FIX THE TOOLCHAIN" in diagnostics["refresh_reason"]
    assert "does NOT mean the recorded knowledge has moved" in diagnostics["refresh_reason"]
    assert any(not requirement["satisfied"] for requirement in diagnostics["toolchain"])

    job = _await(client, client.post(
        "/api/failure-memory/pack/refresh", json={"action": "graph"}
    ).json()["id"])
    assert job["status"] == "failed"
    assert job["error"]["code"].startswith("repo2md_") or job["error"]["code"] == "akms_contract"

    from logic_loom_api.services.failure_memory_operator import is_toolchain_unavailable

    assert is_toolchain_unavailable(job["error"]["code"])


def _publish_graph(repo: Path, vault: Path, generated_at: str) -> None:
    """Publish a canonical graph at an explicit instant (the pack's procedure)."""
    from akms.graph.build_graph import build_graph
    from akms_failure_memory.config import load_project_config
    from akms_failure_memory.refresh import _finalize_graph_payload

    config = load_project_config(repo / "project.toml")
    output = config.resolve(repo, "graph")
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = output.parent / ".staging.json"
    build_graph(
        config.resolve(repo, "akms_repo_root"),
        global_vault=vault,
        output_path=scratch,
        strict=True,
    )
    payload = _finalize_graph_payload(
        json.loads(scratch.read_text(encoding="utf-8")),
        config=config,
        generated_at=generated_at,
    )
    scratch.unlink()
    output.write_bytes(payload)


def test_an_unavailable_refresh_never_makes_a_stale_state_look_current(
    project: tuple[Path, Path],
) -> None:
    """An unavailable refresh must never suppress staleness detection.

    Republishing needs the repo2md toolchain; NOTICING that content moved only
    needs the registry digest and route-index hash. With refresh unavailable
    and a hand-edited registry, the surface must still say `stale`.
    """
    from logic_loom_api.services.failure_memory_operator import derive_generated_at

    repo, vault = project
    client = _client(repo, vault, "operator")
    _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])
    registry_digest = hashlib.sha256((repo / "lessons.json").read_bytes()).hexdigest()
    _publish_graph(repo, vault, derive_generated_at(registry_digest))
    assert client.get("/api/failure-memory/pack/status").json()["currency"] == "current"

    diagnostics = client.get("/api/failure-memory/pack/write-diagnostics").json()
    if diagnostics["refresh_classification"] == "available":  # pragma: no cover
        pytest.skip("this environment has a conforming pinned repo2md checkout")

    registry = json.loads((repo / "lessons.json").read_text(encoding="utf-8"))
    registry["lessons"][0]["symptom"] = "hand edited outside Logic-Loom"
    (repo / "lessons.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    status = client.get("/api/failure-memory/pack/status").json()
    assert status["currency"] == "stale", "an unavailable refresh laundered a stale state"
    assert status["staleness"]["state"] == "stale"
    assert status["graph"]["currency"] == "stale"
    assert status["currency_verifiable_without_toolchain"] is True
    assert diagnostics["detection_still_works"] is True

    # And the compiler agrees, also without repo2md.
    assert client.post("/api/failure-memory/pack/check").json()["status"] == "drift"


def test_a_zero_node_graph_can_never_yield_current(project: tuple[Path, Path]) -> None:
    """A zero-node graph with a correct-looking header must never be `current`.

    Verification reads the document's actual nodes and checks every routed node
    is present — metadata about content is not content.
    """
    from logic_loom_api.services.failure_memory import derive_generated_at

    repo, vault = project
    client = _client(repo, vault, "operator")
    _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])
    digest = hashlib.sha256((repo / "lessons.json").read_bytes()).hexdigest()
    _publish_graph(repo, vault, derive_generated_at(digest))
    assert client.get("/api/failure-memory/pack/status").json()["currency"] == "current"

    graph_path = repo / "runtime" / "graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    honest_node_count = len(graph["nodes"])
    assert honest_node_count > 0

    # Forge it: empty the document, leave every piece of metadata untouched and
    # still claiming the real node count.
    graph["nodes"] = []
    graph["links"] = []
    graph_path.write_text(json.dumps(graph, sort_keys=True, indent=2), encoding="utf-8")
    assert json.loads(graph_path.read_text(encoding="utf-8"))["graph"]["node_count"] == (
        honest_node_count
    ), "the forged graph still claims its original node count"

    status = client.get("/api/failure-memory/pack/status").json()
    assert status["currency"] != "current", "a zero-node graph yielded `current`"
    assert status["currency"] == "stale"
    assert status["graph"]["content_node_count"] == 0
    assert status["graph"]["node_count"] == honest_node_count, "metadata still lies"
    assert status["graph"]["routed_node_count"] > 0
    assert status["graph"]["missing_routed_node_ids"], "the missing routed nodes are named"


def test_a_graph_missing_one_routed_lesson_is_not_current(
    project: tuple[Path, Path],
) -> None:
    """Partial content is not content: one absent routed node is enough."""
    from logic_loom_api.services.failure_memory import derive_generated_at

    repo, vault = project
    client = _client(repo, vault, "operator")
    _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])
    digest = hashlib.sha256((repo / "lessons.json").read_bytes()).hexdigest()
    _publish_graph(repo, vault, derive_generated_at(digest))

    graph_path = repo / "runtime" / "graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    dropped = graph["nodes"].pop()["id"]
    graph_path.write_text(json.dumps(graph, sort_keys=True, indent=2), encoding="utf-8")

    status = client.get("/api/failure-memory/pack/status").json()
    assert status["currency"] == "stale"
    assert dropped in status["graph"]["missing_routed_node_ids"]
    assert dropped in status["currency_reason"] or dropped in status["graph"]["currency_reason"]


def test_corrupted_generated_content_is_stale_not_current(
    project: tuple[Path, Path],
) -> None:
    """Corrupting a generated node's body must read `stale`, never `current`.

    Only the compiler's recompile comparison can see this, so its evidence is
    a required input to the verdict — `/status` and `/check` cannot disagree.
    """
    from logic_loom_api.services.failure_memory import derive_generated_at

    repo, vault = project
    client = _client(repo, vault, "operator")
    _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])
    digest = hashlib.sha256((repo / "lessons.json").read_bytes()).hexdigest()
    _publish_graph(repo, vault, derive_generated_at(digest))
    before = client.get("/api/failure-memory/pack/status").json()
    assert before["currency"] == "current"
    assert before["check_status"] == "clean"

    # Corrupt ONE generated node body. Same name, same routes, same graph.
    nodes_dir = repo / "generated" / "nodes" / "knowledge" / "local-nodes" / "failure-lessons"
    node = next(nodes_dir.glob("cc-failure-*.md"))
    node.write_text(node.read_text(encoding="utf-8") + "\ncorrupted\n", encoding="utf-8")

    status = client.get("/api/failure-memory/pack/status").json()
    check = client.post("/api/failure-memory/pack/check").json()
    assert check["status"] == "drift"
    assert status["check_status"] == "drift", "the verdict consulted the check evidence"
    assert status["currency"] == "stale", (
        f"corrupted generated content read as {status['currency']!r} beside /check drift"
    )


def test_removed_route_coverage_is_stale_not_current(
    project: tuple[Path, Path],
) -> None:
    """Removing one lesson's routes must read `stale`, never `current`.

    `routes.json` keeps its recorded `source_hash` and the graph still contains
    every REMAINING routed node — only the compiler's recompile notices, so its
    evidence must be consulted in the verdict.
    """
    from logic_loom_api.services.failure_memory import derive_generated_at

    repo, vault = project
    client = _client(repo, vault, "operator")
    _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])
    digest = hashlib.sha256((repo / "lessons.json").read_bytes()).hexdigest()
    _publish_graph(repo, vault, derive_generated_at(digest))
    assert client.get("/api/failure-memory/pack/status").json()["currency"] == "current"

    routes_path = repo / "generated" / "routes.json"
    routes = json.loads(routes_path.read_text(encoding="utf-8"))
    victim = sorted(routes["by_path"])[0]
    removed = routes["by_path"].pop(victim)
    assert removed, "the probe must remove real coverage"
    routes_path.write_text(json.dumps(routes, indent=2, sort_keys=True), encoding="utf-8")

    status = client.get("/api/failure-memory/pack/status").json()
    check = client.post("/api/failure-memory/pack/check").json()
    assert status["staleness"]["state"] == "current", (
        "precondition: the digest comparison alone cannot see removed coverage"
    )
    assert check["status"] == "drift"
    assert status["currency"] == "stale", (
        f"removed route coverage read as {status['currency']!r} beside /check drift"
    )


def test_a_missing_generated_node_is_stale_even_in_a_cold_clone(
    project: tuple[Path, Path],
) -> None:
    """Generated-tree coverage is a LOCK-FREE signal and works where /check cannot.

    In a cold clone the recompile-based check is blocked by the absent lock
    parent, so it honestly reports `unverifiable` — but a registry lesson whose
    generated node FILE is missing is observable from a directory listing, and
    definitive: resolution fails for exactly that lesson. Detection must not
    require the disposable runtime tree.
    """
    repo, vault = project
    assert not (repo / "runtime").exists(), "precondition: cold clone"
    nodes_dir = repo / "generated" / "nodes" / "knowledge" / "local-nodes" / "failure-lessons"
    victims = sorted(nodes_dir.glob("cc-failure-*.md"))
    victims[0].unlink()

    client = _client(repo, vault, "operator")
    status = client.get("/api/failure-memory/pack/status").json()
    assert status["check_status"] == "unverifiable", "the check cannot run cold"
    assert status["generated_state"] == "incomplete"
    assert victims[0].stem in status["generated_missing_node_ids"]
    assert status["currency"] == "stale", (
        f"a missing generated node in a cold clone read as {status['currency']!r}"
    )


def test_the_surface_forwards_owner_verdicts_verbatim(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every verdict the surface reports is the owner's, byte for byte.

    The owner is stubbed to return each verdict beside a nonce reason and the
    HTTP payload must carry exactly both — so no consumer can compose, map or
    override a verdict on the way to the caller.
    """
    from logic_loom_api.services import failure_memory_currency as owner

    repo, vault = project
    (repo / "runtime").mkdir()
    client = _client(repo, vault, "read-only")

    for verdict in ("current", "stale", "unverifiable", "uncompiled", "never_written"):
        nonce = f"owner-nonce-{verdict}-8f2a"
        monkeypatch.setattr(
            owner,
            "resolve_currency",
            lambda *args, _v=verdict, _n=nonce, **kwargs: (_v, _n),
        )
        status = client.get("/api/failure-memory/pack/status").json()
        assert status["currency"] == verdict, (
            f"the surface reported {status['currency']!r} where the owner said {verdict!r}"
        )
        assert status["currency_reason"] == nonce, (
            "the owner's reason was transformed on the way to the caller"
        )
    monkeypatch.undo()

    # Graph currency forwards the owner's verification verbatim too.
    from logic_loom_api.services.failure_memory_currency import GraphVerification

    for state, label in (
        ("missing_nodes", "stale"),
        ("content_mismatch", "stale"),
        ("timestamp_mismatch", "unverifiable"),
        ("verified", "current"),
    ):
        nonce = f"verification-nonce-{state}-8f2a"
        monkeypatch.setattr(
            owner,
            "verify_graph_content",
            lambda *args, _s=state, _n=nonce, **kwargs: GraphVerification(
                state=_s, reason=_n, graph_node_count=6, routed_node_count=6
            ),
        )
        status = client.get("/api/failure-memory/pack/status").json()
        assert status["graph"]["currency"] == label
        assert status["graph"]["currency_reason"] == nonce
    monkeypatch.undo()

    # And the never-written determination.
    from logic_loom_api.services.failure_memory_currency import WriteHistory

    for claim in (True, False):
        nonce = f"history-nonce-{claim}-8f2a"
        monkeypatch.setattr(
            owner,
            "write_history",
            lambda *args, _c=claim, _n=nonce, **kwargs: WriteHistory(
                never_written=_c, reason=_n
            ),
        )
        status = client.get("/api/failure-memory/pack/status").json()
        listed = client.get("/api/failure-memory/projects").json()["projects"][0]
        diagnostics = client.get("/api/failure-memory/pack/write-diagnostics").json()
        assert status["never_written"] is claim
        assert listed["never_written"] is claim
        assert diagnostics["never_written"] is claim


def test_deleting_a_live_load_with_edge_is_stale(project: tuple[Path, Path]) -> None:
    """Altering graph node content (load_with, tags) must read `stale`.

    Resolution reads content out of the graph, and the compiler check does not
    cover the graph document. Verification compares each graph node against
    its OWN published generated file — two published artifacts must agree;
    nothing re-derives which relationships ought to exist.
    """
    from logic_loom_api.services.failure_memory import derive_generated_at

    repo, vault = project
    client = _client(repo, vault, "operator")
    _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])
    digest = hashlib.sha256((repo / "lessons.json").read_bytes()).hexdigest()
    _publish_graph(repo, vault, derive_generated_at(digest))
    assert client.get("/api/failure-memory/pack/status").json()["currency"] == "current"

    graph_path = repo / "runtime" / "graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    victim = next(node for node in graph["nodes"] if node.get("load_with"))
    assert victim["load_with"], "the probe needs a LIVE load_with entry"
    victim["load_with"] = []
    graph_path.write_text(json.dumps(graph, sort_keys=True, indent=2), encoding="utf-8")

    status = client.get("/api/failure-memory/pack/status").json()
    check = client.post("/api/failure-memory/pack/check").json()
    assert check["status"] == "clean", (
        "precondition: the compiler check does not see the graph document"
    )
    assert status["currency"] == "stale", (
        f"deleting a live load_with edge read as {status['currency']!r}"
    )
    assert status["graph"]["currency"] == "stale"
    assert victim["id"] in status["graph"]["currency_reason"]

    # Invented variant of the same class: altering advisory-relevant content
    # (tags) on an existing node must be caught the same way.
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    tagged = next(node for node in graph["nodes"] if node.get("tags"))
    tagged["tags"] = ["smuggled-tag"]
    graph_path.write_text(json.dumps(graph, sort_keys=True, indent=2), encoding="utf-8")
    assert client.get("/api/failure-memory/pack/status").json()["currency"] == "stale"

    # And the additive twin: a FABRICATED project-namespaced node with no
    # published generated file behind it — advisory-bait tags, a load_with
    # nobody published — must read as stale, not as extra credit.
    _publish_graph(repo, vault, derive_generated_at(digest))
    assert client.get("/api/failure-memory/pack/status").json()["currency"] == "current"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["nodes"].append(
        {
            "id": "cc-failure-l999",
            "tags": ["smuggled-advisory-tag"],
            "load_with": [],
            "akms_schema": "v2",
        }
    )
    graph_path.write_text(json.dumps(graph, sort_keys=True, indent=2), encoding="utf-8")
    status = client.get("/api/failure-memory/pack/status").json()
    assert status["currency"] == "stale", (
        f"a fabricated graph node read as {status['currency']!r}"
    )
    assert "cc-failure-l999" in status["graph"]["currency_reason"]


def test_status_under_lock_contention_is_bounded_and_unverifiable(
    project: tuple[Path, Path],
) -> None:
    """`/status` must not inherit the 120-second write lock timeout.

    A concurrent writer holding the project lock used to block the status read
    for the full configured `toolchain.timeout_seconds`. The check evidence is
    now gathered under a short bounded acquisition; contention degrades
    honestly to `unverifiable` — exactly like an absent lock parent — and the
    read returns promptly.
    """
    import os as _os
    import time as _time

    repo, vault = project
    (repo / "runtime").mkdir()
    lock_path = repo / "runtime" / ".lock"
    # A live owner: our own pid, so stale-lock recovery must not steal it.
    lock_path.write_text(
        json.dumps({"pid": _os.getpid(), "token": "f" * 32, "version": 1}) + "\n",
        encoding="utf-8",
    )
    try:
        client = _client(repo, vault, "read-only")
        started = _time.monotonic()
        status = client.get("/api/failure-memory/pack/status").json()
        elapsed = _time.monotonic() - started
        assert elapsed < 15.0, (
            f"/status blocked {elapsed:.1f}s under lock contention — it must be bounded"
        )
        assert status["check_status"] == "unverifiable", status["check_reason"]
        assert "lock_contention" in status["check_reason"]
        assert status["currency"] == "unverifiable"
        assert status["currency"] != "current"
    finally:
        lock_path.unlink()


def test_currency_is_never_current_on_absent_evidence(
    project: tuple[Path, Path],
) -> None:
    """Five distinguishable states, and `current` requires positive verification.

    The same discipline as `delivered_nowhere` versus `no_lessons_apply`, for the
    same reason: an operator who cannot tell "stale" from "unverifiable" from
    "current" will make the wrong call, and rendering uncertainty as a green tick
    is the failure that does not announce itself.
    """
    from logic_loom_api.services.failure_memory_operator import derive_generated_at

    repo, vault = project
    client = _client(repo, vault, "operator")
    seen: dict[str, str] = {}

    # NB: a copy of the pack is a COLD CLONE, not a fresh project — it ships a
    # committed registry, route index and generated nodes. Only the disposable
    # runtime/ tree is missing, which says nothing about content.
    seen["cold clone, no graph"] = client.get("/api/failure-memory/pack/status").json()[
        "currency"
    ]

    _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])
    seen["no graph published"] = client.get("/api/failure-memory/pack/status").json()["currency"]

    digest = hashlib.sha256((repo / "lessons.json").read_bytes()).hexdigest()
    _publish_graph(repo, vault, derive_generated_at(digest))
    seen["graph at derived instant"] = client.get("/api/failure-memory/pack/status").json()[
        "currency"
    ]

    _publish_graph(repo, vault, "2001-01-01T00:00:00+00:00")
    seen["graph at a foreign instant"] = client.get("/api/failure-memory/pack/status").json()[
        "currency"
    ]

    assert seen == {
        # A registry with no published graph cannot resolve at all — which is
        # emphatically not the same as being up to date, and emphatically not
        # "never written to" when the registry and routes are right there.
        "cold clone, no graph": "unverifiable",
        "no graph published": "unverifiable",
        "graph at derived instant": "current",
        # Content may or may not have moved; Logic-Loom cannot tell, so it says so.
        "graph at a foreign instant": "unverifiable",
    }, seen


def test_write_diagnostics_name_the_repo2md_prerequisite(
    project: tuple[Path, Path],
) -> None:
    """repo2md is a documented DEPLOYMENT PREREQUISITE, not a known limitation.

    Neither consumer vendors a checkout, so in a normal deployment this is the
    primary content of the write diagnostics rather than an edge case. It must
    therefore name the pinned commit and say concretely what to install.
    """
    repo, vault = project
    (repo / "runtime").mkdir()
    client = _client(repo, vault, "operator")
    diagnostics = client.get("/api/failure-memory/pack/write-diagnostics").json()
    if diagnostics["refresh_classification"] == "available":  # pragma: no cover
        pytest.skip("this environment has a conforming pinned repo2md checkout")

    config = json.loads(
        json.dumps(
            __import__("tomllib").loads((repo / "project.toml").read_text(encoding="utf-8"))
        )
    )
    pinned = config["toolchain"]["repo2md_commit"]

    assert diagnostics["repo2md_required_commit"] == pinned
    assert diagnostics["repo2md_required_version"] == config["toolchain"]["repo2md_version"]
    assert diagnostics["repo2md_command"] == config["toolchain"]["repo2md_command"]
    assert diagnostics["refresh_remediation"], "an unavailable refresh must be actionable"
    assert any(pinned in step for step in diagnostics["refresh_remediation"])
    assert any("EDITABLE" in step for step in diagnostics["refresh_remediation"])
    assert any("still detected" in step for step in diagnostics["refresh_remediation"])
    assert "DEPLOYMENT PREREQUISITE" in diagnostics["refresh_reason"]
    assert "DETECTION is unaffected" in diagnostics["refresh_reason"]


def test_write_diagnostics_with_the_builtin_mirror_provider(
    project: tuple[Path, Path],
) -> None:
    """AKMS 0.4 absorbed repo2md: ``mirror_provider = "builtin"`` needs no checkout.

    The diagnostics must then name the provider, report no repo2md pin, and —
    if refresh is still unavailable — never tell the operator to install repo2md.
    """
    repo, vault = project
    (repo / "runtime").mkdir()
    config_path = repo / "project.toml"
    text = config_path.read_text(encoding="utf-8")
    assert "mirror_provider" not in text
    config_path.write_text(
        text.replace("[toolchain]\n", '[toolchain]\nmirror_provider = "builtin"\n', 1),
        encoding="utf-8",
    )
    client = _client(repo, vault, "operator")
    diagnostics = client.get("/api/failure-memory/pack/write-diagnostics").json()

    assert diagnostics["mirror_provider"] == "builtin"
    assert diagnostics["repo2md_required_commit"] == ""
    assert diagnostics["repo2md_command"] == []
    if diagnostics["refresh_classification"] == "available":
        names = {item["name"] for item in diagnostics["toolchain"]}
        assert "mirror" in names and "repo2md" not in names
    else:
        assert "DEPLOYMENT PREREQUISITE" not in diagnostics["refresh_reason"]
        assert not any("EDITABLE" in step for step in diagnostics["refresh_remediation"])


def test_write_diagnostics_default_to_the_repo2md_provider(
    project: tuple[Path, Path],
) -> None:
    """A config without ``mirror_provider`` keeps repo2md, as the package does."""
    repo, vault = project
    (repo / "runtime").mkdir()
    client = _client(repo, vault, "read-only")
    diagnostics = client.get("/api/failure-memory/pack/write-diagnostics").json()
    assert diagnostics["mirror_provider"] == "repo2md"


def test_clean_is_not_an_exposed_refresh_action(project: tuple[Path, Path]) -> None:
    """``clean`` deletes every disposable artifact; it does not belong in a UI."""
    repo, vault = project
    (repo / "runtime").mkdir()
    client = _client(repo, vault, "operator")
    for action in ("clean", "status", "nonsense"):
        response = client.post("/api/failure-memory/pack/refresh", json={"action": action})
        assert response.status_code == 422, action


def test_generated_at_is_derived_from_registry_content_not_the_clock(
    project: tuple[Path, Path],
) -> None:
    """Same content ⇒ same instant, and the input is the routes.json digest.

    A wall-clock ``generated_at`` makes identical content read as STALE on every
    refresh, so a freshness gate built on it always fires and becomes
    indistinguishable from a broken one. The input here is deliberately the same
    one the WWW stream derives from, so the two surfaces cannot disagree about
    what "current" means.
    """
    from logic_loom_api.services.failure_memory_operator import derive_generated_at

    repo, vault = project
    (repo / "runtime").mkdir()
    client = _client(repo, vault, "operator")
    routes = json.loads((repo / "generated" / "routes.json").read_text(encoding="utf-8"))

    diagnostics = client.get("/api/failure-memory/pack/write-diagnostics").json()
    assert diagnostics["generated_at_source"] == routes["source_hash"]
    assert diagnostics["derived_generated_at"] == derive_generated_at(routes["source_hash"])

    # Stable across calls, and identical for the same digest with or without the
    # `sha256:` prefix.
    again = client.get("/api/failure-memory/pack/write-diagnostics").json()
    assert again["derived_generated_at"] == diagnostics["derived_generated_at"]
    bare = routes["source_hash"].split(":", 1)[-1]
    assert derive_generated_at(bare) == diagnostics["derived_generated_at"]

    # Timezone-aware — the package rejects a naive value outright.
    from datetime import datetime

    assert datetime.fromisoformat(diagnostics["derived_generated_at"]).tzinfo is not None

    # Every bit of the digest participates. Earlier attempts sliced or truncated
    # the digest and mapped DIFFERENT registries to the SAME instant, which makes
    # moved content read as current — the failure direction that stays silent.
    flipped = {
        derive_generated_at(f"{int(bare, 16) ^ (1 << bit):064x}") for bit in range(256)
    }
    assert len(flipped) == 256


def test_a_recording_changes_the_derived_generated_at(
    project: tuple[Path, Path],
) -> None:
    """Content-derived means it MOVES when content moves."""
    from logic_loom_api.services.failure_memory_operator import derive_generated_at

    repo, vault = project
    client = _client(repo, vault, "operator")
    before = json.loads((repo / "generated" / "routes.json").read_text(encoding="utf-8"))[
        "source_hash"
    ]
    _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])
    after = json.loads((repo / "generated" / "routes.json").read_text(encoding="utf-8"))[
        "source_hash"
    ]
    assert after != before
    assert derive_generated_at(after) != derive_generated_at(before)


def test_write_diagnostics_are_readable_in_read_only_mode(
    project: tuple[Path, Path],
) -> None:
    """"Would refresh work if I enabled operator mode?" is a read-only question."""
    repo, vault = project
    (repo / "runtime").mkdir()
    client = _client(repo, vault, "read-only")
    body = client.get("/api/failure-memory/pack/write-diagnostics").json()
    assert body["operator"] is False
    assert body["can_record"] is False and body["can_compile"] is False
    assert any("read-only" in blocker for blocker in body["blockers"])


def test_a_cold_clone_without_a_lock_directory_is_recordable_and_not_never_written(
    project: tuple[Path, Path],
) -> None:
    """Retargeted: this staged a six-lesson cold clone and called it never-written.

    The original assertion was the defect written into the suite as an
    expectation — it asserted ``never_written is True`` for a project with a
    committed registry, route index and six generated nodes, purely because
    ``runtime/`` was absent. What actually matters here is the operational half:
    a missing lock directory does not block recording, and the write bootstraps
    it. The never-written verdict must be FALSE throughout.
    """
    repo, vault = project
    assert not (repo / "runtime").exists()
    client = _client(repo, vault, "operator")

    diagnostics = client.get("/api/failure-memory/pack/write-diagnostics").json()
    assert diagnostics["lock_parent_exists"] is False
    assert diagnostics["never_written"] is False, (
        "six committed lessons are not 'never written to' whatever the scratch dir says"
    )
    assert diagnostics["can_record"] is True, "a missing lock dir must not block recording"

    job = _await(client, client.post("/api/failure-memory/pack/record", json=PROPOSAL).json()["id"])
    assert job["status"] == "succeeded", job
    assert (repo / "runtime").is_dir(), "the write bootstrapped the lock directory"
    status = client.get("/api/failure-memory/pack/status").json()
    assert status["never_written"] is False
    assert status["lock_parent_exists"] is True


def test_operator_mode_still_cannot_be_enabled_over_http() -> None:
    """The read-only surface's control must survive the arrival of something worth escalating to."""
    from logic_loom_api.routes.app import ConfigUpdate, FirstRunPayload

    assert "failure_memory_mode" not in ConfigUpdate.model_fields
    assert "failure_memory_projects" not in ConfigUpdate.model_fields
    assert "failure_memory_mode" not in FirstRunPayload.model_fields
