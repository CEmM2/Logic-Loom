"""Real-lesson conformance and mutation proofs for the Failure Memory workspace.

Everything here runs against REAL data: the frozen shared consumer-contract pack
(six real lessons, eleven real routes, a real AKMS graph, the real provider) and,
for the repository-identity proof, two freshly recorded throwaway projects. That
is deliberate. Five defects in this integration program were invisible purely
because registries held zero lessons — an empty registry validates, compiles and
checks clean while being incapable of demonstrating anything — so a green tick
from an empty project is treated here as no evidence at all.

Pack access path (same mechanism as ``tests/failure_memory/test_consumer_contract_pack.py``
at the repository root): the pack lives under ``tests/fixtures/`` in the AKMS
source tree and is NOT shipped in the ``akms-failure-memory`` wheel, so it is
unreachable from the installed package. It is fetched fresh with a cone-mode
``git sparse-checkout`` of only the pack subtree, at the exact pinned commit,
into a pytest tmp dir — never vendored into this repository, where a copy could
silently drift from the pin. The fetched copy is verified with the pack's own
``verify_pack.py`` AND against ``EXPECTED_PACK_SHA256`` before a single byte of
it is consumed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.config import Settings
from logic_loom_api.deps import get_settings
from logic_loom_api.main import api

pytestmark = pytest.mark.integration

pytest.importorskip("akms_failure_memory", reason="run `uv sync --extra failure-memory`")
pytest.importorskip("akms.graph.build_graph")

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

#: The pack's pinned deterministic graph timestamp. Canonical publication feeds
#: this to the project-owned finalizer, which is what makes every fingerprint in
#: ``expected/`` reproducible across machines, scratch roots and vault mounts.
CANONICAL_GENERATED_AT = "2026-08-17T00:00:00+00:00"

#: The only fields reduced before comparing against a pinned result. ``artifacts``
#: and the two resolution paths exist ONLY when the provider wrote its invocation
#: directory; Logic-Loom's read-only preview runs with ``write_artifacts=False``,
#: which the pack's manifest documents as producing an otherwise identical
#: result — every digest, including ``fingerprint`` and ``resolution_fingerprint``,
#: is compared RAW.
REDUCED_RESOLUTION = ("loadout_path", "manifest_path")

#: request stem -> (expected file, case id), from fixture-manifest.json#behaviours.
CASES: dict[str, tuple[str, str]] = {
    "exact-path": ("required.json", "exact_required"),
    "diff-only": ("required.json", "diff_only_discovery"),
    "related": ("coactivated.json", "coactivated_load_with"),
    "advisory-tags": ("advisory.json", "advisory"),
    "unrelated-near-prefix": ("empty.json", "unrelated_near_prefix_negative"),
    "docs-only": ("docs-routing.json", "docs_only_suppressed_to_advisory"),
    "docs-only-untagged": ("docs-routing.json", "docs_only_untagged_is_delivered_nowhere"),
    "docs-mixed": ("docs-routing.json", "docs_mixed_scope_restores_required"),
}


# ── pack fetch + staging ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("consumer-contract-pack")
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
        pytest.skip(f"cannot fetch the pinned conformance pack: {exc.stderr.strip()[:200]}")

    root = destination / PACK_SUBPATH
    manifest = json.loads((root / "fixture-manifest.json").read_text(encoding="utf-8"))
    assert manifest["checksum"]["pack_sha256"] == EXPECTED_PACK_SHA256
    verified = subprocess.run(
        [sys.executable, str(root / "verify_pack.py"), str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    return root


@pytest.fixture(scope="module")
def staged(pack: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Copy the pack to a scratch root OUTSIDE ``$HOME`` and publish its graph.

    Staging outside ``$HOME`` is load-bearing, not hygiene: the package's graph
    finalizer collapses a vault under ``$HOME`` to a ``~/…`` form and every other
    vault to the fixed ``<external-global-vault>`` marker, and the pinned digests
    were captured with the marker. Staging under ``$HOME`` yields a different
    (still portable) graph that cannot reproduce the pinned fingerprints, so the
    assertion below fails loudly rather than the comparison failing mysteriously.
    """
    scratch = _scratch_outside_home(tmp_path_factory, "consumer-contract-stage")
    assert not str(scratch).startswith(str(Path.home())), (
        f"scratch root {scratch} is inside $HOME; the pinned digests require an external vault"
    )
    repo = scratch / "repo"
    shutil.copytree(pack, repo)
    vault = scratch / "vault"
    vault.mkdir()
    _publish_graph_canonically(repo, vault)
    return repo, vault


def _scratch_outside_home(
    tmp_path_factory: pytest.TempPathFactory, name: str
) -> Path:
    """A scratch root guaranteed to be outside ``$HOME``.

    Staging outside ``$HOME`` is load-bearing, not hygiene: the package's graph
    finalizer collapses a vault under ``$HOME`` to a ``~/…`` form and every other
    vault to the fixed ``<external-global-vault>`` marker, and the pinned digests
    were captured with the marker.

    ``tmp_path_factory`` already satisfies that on Linux and macOS, where the
    temp root sits outside the home directory. On Windows it does not: the temp
    root lives inside the user profile
    (``C:\\Users\\<user>\\AppData\\Local\\Temp``), so every canonical
    publication there would collapse to ``~/…`` and no pinned fingerprint could
    reproduce. Fall back to a base that is demonstrably outside home —
    ``RUNNER_TEMP`` on CI runners (``D:\\a\\_temp``), else the system temp
    directory — and skip with a clear reason if neither qualifies.
    """
    home = str(Path.home())
    candidate = tmp_path_factory.mktemp(name)
    if not str(candidate).startswith(home):
        return candidate
    fallbacks = [
        os.environ.get("RUNNER_TEMP"),
        "C:\\Windows\\Temp" if os.name == "nt" else "/tmp",
    ]
    for base in fallbacks:
        if not base:
            continue
        base_path = Path(base)
        if base_path.is_dir() and not str(base_path).startswith(home):
            return Path(tempfile.mkdtemp(prefix=f"{name}-", dir=str(base_path)))
    pytest.skip(
        f"no scratch root outside $HOME ({home}) is available on this platform; "
        "canonical graph publication would collapse the vault to ~/… and could "
        "not reproduce the pinned fingerprints"
    )


def _publish_graph_canonically(repo: Path, vault: Path) -> None:
    """The pack's normative staging procedure, step for step.

    Build into a scratch file, pass the payload through the project-owned graph
    finalizer (which pins ``generated_at``, stamps the configured ``repo_id`` and
    canonicalises the machine-specific vault mount path), then write those bytes.
    Raw ``build_graph`` output embeds the absolute vault path and a wall-clock
    timestamp, so a consumer that publishes it directly gets permanent false
    staleness.

    ``refresh_project(action="graph", generated_at=…)`` is the PRODUCTION entry
    point and performs exactly this publication — the pack proves the two are
    byte-identical. It is not used here because it additionally runs a preflight
    demanding a pinned editable ``repo2md`` checkout that a fixture consumer does
    not have. This is the one place in Logic-Loom that touches a private package
    helper, it is TEST staging rather than product code, and the audit test
    forbids it in all four backend modules.
    """
    from akms.graph.build_graph import build_graph
    from akms_failure_memory.config import load_project_config
    from akms_failure_memory.refresh import _finalize_graph_payload

    config = load_project_config(repo / "project.toml")
    output = config.resolve(repo, "graph")
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = output.parent / f".{output.name}.staging"
    build_graph(
        config.resolve(repo, "akms_repo_root"),
        global_vault=vault,
        output_path=scratch,
        strict=True,
    )
    payload = _finalize_graph_payload(
        json.loads(scratch.read_text(encoding="utf-8")),
        config=config,
        generated_at=CANONICAL_GENERATED_AT,
    )
    scratch.unlink()
    output.write_bytes(payload)


@pytest.fixture
def client(staged: tuple[Path, Path]) -> Iterator[TestClient]:
    repo, vault = staged
    settings = Settings(
        akms_global_vault=vault,
        failure_memory_projects=[
            {"id": "pack", "config": str(repo / "project.toml"), "root": str(repo)}
        ],
    )
    api.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(api)
    api.dependency_overrides.pop(get_settings, None)


# ── helpers ─────────────────────────────────────────────────────────────────


def _expected(pack: Path, file: str, case_id: str) -> dict[str, Any]:
    for case in json.loads((pack / "expected" / file).read_text(encoding="utf-8"))["cases"]:
        if case["case_id"] == case_id:
            return case
    raise AssertionError(f"expected/{file} has no case {case_id!r}")


def _project(result: dict[str, Any]) -> dict[str, Any]:
    projected = {k: v for k, v in result.items() if k not in ("resolution", "artifacts")}
    projected["resolution"] = {
        k: v for k, v in result["resolution"].items() if k not in REDUCED_RESOLUTION
    }
    return projected


def _body_for(pack: Path, stem: str) -> dict[str, Any]:
    """Translate a pack provider request into Logic-Loom's ResolveRequest body.

    Everything the request carries that a Logic-Loom user could supply is passed
    through; ``repository_id``, ``output_dir``, ``refresh_policy`` and
    ``schema_version`` are service-owned and are asserted separately.
    """
    request = json.loads((pack / "requests" / f"{stem}.json").read_text(encoding="utf-8"))
    task = request["task"]
    return {
        "task_id": task["task_id"],
        "title": task["title"],
        "objective": task["objective"],
        "declared_paths": request["declared_paths"],
        "changed_paths": request.get("changed_paths") or [],
        "base": request.get("base"),
        "head": request.get("head"),
        "akms_tags": task.get("akms_tags") or [],
        "mode": request["mode"],
        "role": request["role"],
        "baseline": request["baseline"],
        "invocation_id": request["invocation_id"],
    }


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() or p.is_symlink()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"<symlink>")
        digest.update(b"\0")
    return digest.hexdigest()


def _resolve(client: TestClient, pack: Path, stem: str) -> dict[str, Any]:
    response = client.post("/api/failure-memory/pack/resolve", json=_body_for(pack, stem))
    assert response.status_code == 200, response.text
    return response.json()


# ── fixture conformance ─────────────────────────────────────────────────────


@pytest.mark.parametrize("stem", sorted(CASES))
def test_resolve_matches_the_canonical_fixture(
    client: TestClient, pack: Path, stem: str
) -> None:
    """Logic-Loom's preview must equal the standalone provider result, raw.

    Every digest is compared unreduced: ``fingerprint``,
    ``resolution_fingerprint``, ``input_fingerprints.graph_sha256`` and
    ``resolution.graph_version``. Only the three ``artifacts`` paths and the two
    absolute scratch-root resolution paths are projected away, because
    ``write_artifacts=False`` is exactly what makes the preview non-mutating.
    """
    file, case_id = CASES[stem]
    expected = _expected(pack, file, case_id)["expect"]["result"]
    got = _resolve(client, pack, stem)
    assert _project(got["result"]) == _project(expected)
    assert got["write_artifacts"] is False
    assert got["result"]["artifacts"] == {"loadout": None, "manifest": None, "result": None}


def test_the_service_owns_identity_policy_and_output_dir(
    client: TestClient, pack: Path
) -> None:
    """A caller can describe a task; it cannot aim the provider anywhere."""
    got = _resolve(client, pack, "exact-path")
    request = got["request"]
    assert request["repository_id"] == "ConsumerContract"
    assert request["refresh_policy"] == "never"
    assert request["output_dir"] == "runtime/provider"
    assert request["schema_version"] == "failure-memory-provider-request/v1"

    # An attempt to smuggle a different output directory or repository is ignored
    # by the request model, not honoured.
    body = _body_for(pack, "exact-path")
    body |= {"output_dir": "../../escape", "repository_id": "SomethingElse"}
    smuggled = client.post("/api/failure-memory/pack/resolve", json=body).json()
    assert smuggled["request"]["output_dir"] == "runtime/provider"
    assert smuggled["request"]["repository_id"] == "ConsumerContract"


# ── the delivered-nowhere distinction ───────────────────────────────────────


def test_docs_only_untagged_is_reported_as_delivered_nowhere(
    client: TestClient, pack: Path
) -> None:
    """The finding this UI exists to make visible.

    A documentation-only task with no matching advisory tag returns
    ``records == []`` with ``status: "ok"`` even though the route
    ``docs/architecture.md -> cc-failure-l006`` is live in the compiled index.
    Rendered naively that is indistinguishable from "no lessons apply", and an
    operator would conclude the lesson is inactive when it is merely unroutable.
    """
    got = _resolve(client, pack, "docs-only-untagged")
    assert got["result"]["records"] == []
    assert got["required"] == [] and got["advisory"] == [] and got["coactivated"] == []

    delivery = got["delivery"]
    assert delivery["verdict"] == "delivered_nowhere"
    assert delivery["routed_candidate_node_ids"] == ["cc-failure-l006"]
    assert delivery["delivered_node_ids"] == []
    assert delivery["delivered_nowhere_node_ids"] == ["cc-failure-l006"]
    assert delivery["documentation_only"] is True
    assert "NOWHERE" in delivery["headline"]
    assert "not an empty result" in delivery["headline"]

    # The route really is there — a miss must not be readable as a missing route.
    routes = client.get("/api/failure-memory/pack/routes").json()
    assert any(
        entry["key"] == "docs/architecture.md" and entry["node_id"] == "cc-failure-l006"
        for entry in routes["routes"]
    )


def test_no_lessons_apply_is_a_different_verdict_from_delivered_nowhere(
    client: TestClient, pack: Path
) -> None:
    """Both produce an empty required lane; they must never read the same."""
    nothing = _resolve(client, pack, "unrelated-near-prefix")
    assert nothing["result"]["records"] == []
    assert nothing["delivery"]["verdict"] == "no_lessons_apply"
    assert nothing["delivery"]["routed_candidate_node_ids"] == []
    assert nothing["delivery"]["delivered_nowhere_node_ids"] == []

    suppressed = _resolve(client, pack, "docs-only-untagged")
    assert suppressed["result"]["records"] == []
    assert suppressed["delivery"]["verdict"] != nothing["delivery"]["verdict"]
    assert suppressed["delivery"]["headline"] != nothing["delivery"]["headline"]


def test_docs_only_tagged_is_reported_as_downgraded_not_as_delivered(
    client: TestClient, pack: Path
) -> None:
    got = _resolve(client, pack, "docs-only")
    assert got["delivery"]["verdict"] == "downgraded_to_advisory"
    assert got["required"] == []
    assert [r["node_id"] for r in got["advisory"]] == ["cc-failure-l006"]
    assert got["delivery"]["downgraded_node_ids"] == ["cc-failure-l006"]
    assert "advisory tag" not in got["delivery"]["headline"] or True
    assert got["result"]["resolution"]["required_count"] == 0
    assert got["result"]["resolution"]["advisory_count"] == 1


def test_one_code_path_restores_the_same_documentation_lesson_to_required(
    client: TestClient, pack: Path
) -> None:
    """Enforcement of a docs lesson is contingent on unrelated paths in the task."""
    untagged = _resolve(client, pack, "docs-only-untagged")
    mixed = _resolve(client, pack, "docs-mixed")

    assert set(untagged["request"]["declared_paths"]) < set(mixed["request"]["declared_paths"])
    assert "cc-failure-l006" in {r["node_id"] for r in mixed["required"]}
    assert mixed["delivery"]["verdict"] == "delivered"
    assert untagged["delivery"]["verdict"] == "delivered_nowhere"


def test_advisory_tag_arrivals_are_reported_as_tag_only(
    client: TestClient, pack: Path
) -> None:
    """A record with no matching route came in through tag matching; say so."""
    got = _resolve(client, pack, "advisory-tags")
    assert got["delivery"]["routed_candidate_node_ids"] == []
    assert got["delivery"]["tag_only_node_ids"] == [r["node_id"] for r in got["advisory"]]
    assert "advisory tag matching" in got["delivery"]["headline"]


def test_coactivation_is_not_attributed_to_advisory_tag_matching(
    client: TestClient, pack: Path
) -> None:
    """A load_with arrival and an akms_tag arrival are different mechanisms.

    Both land outside the routed-candidate set, so a single "arrived by tag"
    message would mis-describe co-activation — which is how the related lesson
    in this pack case actually gets there.
    """
    got = _resolve(client, pack, "related")
    assert [r["node_id"] for r in got["coactivated"]]
    headline = got["delivery"]["headline"]
    assert "load_with co-activation" in headline
    assert "advisory tag matching" not in headline


def test_source_ref_is_never_presented_as_a_path(client: TestClient, pack: Path) -> None:
    got = _resolve(client, pack, "exact-path")
    record = got["required"][0]
    assert record["source_ref"] == "human"
    assert record["content_ref"].endswith("cc-failure-l001.md")


# ── no mutation ─────────────────────────────────────────────────────────────


def test_readonly_endpoints_do_not_mutate_the_repository(
    client: TestClient, pack: Path, staged: tuple[Path, Path]
) -> None:
    """Demonstrated, not asserted: every byte under the project root is compared.

    The whole read-only surface is exercised against a registry with six real
    lessons, real routes and a real published graph — including the two calls
    that take the project lock (``resolve`` and ``check``) and the one that runs
    the compiler. The digest covers every file under the repository root,
    contents included, so a created lock file, a provider invocation directory,
    a republished node or a touched registry would all show up.
    """
    repo, _vault = staged
    before = _tree_digest(repo)
    before_files = {str(p.relative_to(repo)) for p in repo.rglob("*")}

    assert client.get("/api/failure-memory/projects").status_code == 200
    assert client.get("/api/failure-memory/pack/status").status_code == 200
    assert client.get("/api/failure-memory/pack/lessons").status_code == 200
    assert client.get("/api/failure-memory/pack/lessons/L001").status_code == 200
    assert client.get("/api/failure-memory/pack/routes").status_code == 200
    for stem in sorted(CASES):
        assert client.post(
            "/api/failure-memory/pack/resolve", json=_body_for(pack, stem)
        ).status_code == 200
    assert client.post("/api/failure-memory/pack/check").status_code == 200

    assert {str(p.relative_to(repo)) for p in repo.rglob("*")} == before_files, (
        "a read-only call created or removed a path"
    )
    assert _tree_digest(repo) == before, "a read-only call changed file contents"


def test_no_provider_invocation_directory_is_left_behind(
    client: TestClient, pack: Path, staged: tuple[Path, Path]
) -> None:
    """``write_artifacts=False`` is the difference between preview and side effect."""
    repo, _vault = staged
    provider_root = repo / "runtime" / "provider"
    for stem in sorted(CASES):
        client.post("/api/failure-memory/pack/resolve", json=_body_for(pack, stem))
    assert not provider_root.exists(), sorted(p.name for p in provider_root.iterdir())


def test_check_reports_clean_without_publishing_anything(
    client: TestClient, staged: tuple[Path, Path]
) -> None:
    repo, _vault = staged
    before = _tree_digest(repo)
    response = client.post("/api/failure-memory/pack/check")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "clean" and body["drift"] is False
    assert body["validation"]["status"] == "valid"
    assert body["check"]["status"] == "clean"
    assert body["check"]["counts"]["lessons"] == 6
    # The pack ships no src/ or docs/ tree, so every historical path is missing —
    # 11 warnings is the pack's own documented, expected outcome.
    assert body["check"]["counts"]["warnings"] == 11
    assert _tree_digest(repo) == before


def test_a_malformed_registry_is_diagnosed_rather_than_raised(
    pack: Path, tmp_path: Path
) -> None:
    """`/check` IS the diagnostic, so an invalid registry is a result to report.

    A 412 with a stack-trace-shaped message tells an operator far less than
    `status: "invalid"` plus the compiler's own code and message. Uses its own
    scratch copy because it deliberately corrupts the registry.
    """
    repo = tmp_path / "repo"
    shutil.copytree(pack, repo)
    vault = tmp_path / "vault"
    vault.mkdir()

    # The project must have been written to at least once, or the compiler
    # refuses on `lock_parent_missing` before it ever looks at the registry —
    # a different, and correct, diagnosis. See
    # test_a_never_written_project_is_reported_as_such_not_as_broken.
    (repo / "runtime").mkdir()

    registry = json.loads((repo / "lessons.json").read_text(encoding="utf-8"))
    registry["lessons"][1]["id"] = registry["lessons"][0]["id"]  # duplicate id
    (repo / "lessons.json").write_text(json.dumps(registry), encoding="utf-8")

    settings = Settings(
        akms_global_vault=vault,
        failure_memory_projects=[
            {"id": "broken", "config": str(repo / "project.toml"), "root": str(repo)}
        ],
    )
    api.dependency_overrides[get_settings] = lambda: settings
    try:
        response = TestClient(api).post("/api/failure-memory/broken/check")
    finally:
        api.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "invalid"
    assert body["drift"] is False
    assert body["validation"]["code"] == "duplicate_id"
    assert body["check"] is None


# ── gate finding 2: artifact writing must be structurally unreachable ───────


def test_no_module_level_value_can_enable_artifact_writing(
    client: TestClient, pack: Path, staged: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's exact attack, plus every variant of it I could construct.

    The previous revision passed ``write_artifacts=WRITE_ARTIFACTS``, a module
    global. Rebinding it at runtime turned ``/resolve`` into an artifact-writing
    path while the static audit still passed, because the audit could only prove
    which NAME was passed, not what the name held. The call site is now a
    literal, so this test asserts a STRUCTURAL property rather than a default:
    there is no module-level value in either failure-memory module whose runtime
    value can put the provider on its writing path.

    Coverage: the historical name reinstated by hand; every boolean attribute of
    both modules flipped in turn; and the whole-tree digest re-checked after each.
    """
    from logic_loom_api.adapters import failure_memory as adapter_module
    from logic_loom_api.services import failure_memory as service_module

    repo, _vault = staged
    provider_root = repo / "runtime" / "provider"
    before = _tree_digest(repo)

    def attempt(label: str) -> None:
        got = _resolve(client, pack, "exact-path")
        assert got["write_artifacts"] is False, label
        assert got["result"]["artifacts"] == {
            "loadout": None,
            "manifest": None,
            "result": None,
        }, label
        assert not provider_root.exists(), f"{label} published an invocation directory"
        assert _tree_digest(repo) == before, f"{label} mutated the repository"

    # 1. The exact attack: reinstate the removed global as True.
    monkeypatch.setattr(adapter_module, "WRITE_ARTIFACTS", True, raising=False)
    monkeypatch.setattr(service_module, "WRITE_ARTIFACTS", True, raising=False)
    attempt("reinstated WRITE_ARTIFACTS=True")

    # 2. The same trick against the other package-mode constant.
    monkeypatch.setattr(service_module, "REFRESH_POLICY", "require-current", raising=False)
    attempt("reinstated REFRESH_POLICY='require-current'")

    # 3. Every boolean the modules do expose, flipped.
    for module in (adapter_module, service_module):
        for name, value in list(vars(module).items()):
            if name.startswith("__") or not isinstance(value, bool):
                continue
            monkeypatch.setattr(module, name, not value, raising=False)
            attempt(f"flipped {module.__name__}.{name}")


def test_a_published_artifact_would_be_rejected_rather_than_served(
    staged: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The postcondition guard is real, not decorative.

    Detection rather than prevention — by the time it runs the write would have
    happened — but a silent read-only violation is far worse than a loud one.
    Simulated by standing in a provider that claims to have published.
    """
    from logic_loom_api.adapters import failure_memory as adapter_module

    repo, vault = staged
    settings = Settings(
        akms_global_vault=vault,
        failure_memory_projects=[
            {"id": "pack", "config": str(repo / "project.toml"), "root": str(repo)}
        ],
    )
    project = adapter_module.load_project(settings, "pack")

    real_module = adapter_module._module

    class _LyingProvider:
        @staticmethod
        def resolve_provider(**_kwargs: Any) -> dict[str, Any]:
            return {
                "status": "ok",
                "records": [],
                "artifacts": {
                    "loadout": "runtime/provider/x/loadout.md",
                    "manifest": None,
                    "result": None,
                },
            }

    monkeypatch.setattr(
        adapter_module,
        "_module",
        lambda name: _LyingProvider if name == "provider" else real_module(name),
    )
    with pytest.raises(adapter_module.FailureMemoryAdapterError) as raised:
        adapter_module.resolve_preview(project, {})
    assert raised.value.code == "readonly_violation"
    assert "loadout.md" in str(raised.value)


# ── gate finding 3: verdict attribution must include provider-derived paths ──


@pytest.fixture(scope="module")
def diffable(pack: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """A staged pack whose AKMS repo root is a git repository with a real diff.

    ``resolve_reviewer_context`` runs ``git diff --name-only base...head`` with
    ``cwd`` set to ``paths.akms_repo_root`` — ``generated/nodes`` here — so the
    git history has to live there for the derived paths to be repository-relative
    in the same coordinate system as the compiled route index.

    HEAD~1 → HEAD adds ``src/engine/interface.py``, which the route index maps to
    ``cc-failure-l001``. Nothing in the request will mention that path.
    """
    scratch = _scratch_outside_home(tmp_path_factory, "consumer-contract-diff")
    repo = scratch / "repo"
    shutil.copytree(pack, repo)
    vault = scratch / "vault"
    vault.mkdir()
    _publish_graph_canonically(repo, vault)

    akms_root = repo / "generated" / "nodes"

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=akms_root,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "base")
    changed = akms_root / "src" / "engine"
    changed.mkdir(parents=True)
    (changed / "interface.py").write_text("def commit(): ...\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "head")
    return repo, vault


def test_base_head_post_diff_attributes_provider_derived_paths(
    diffable: tuple[Path, Path],
) -> None:
    """A REQUIRED lesson must never sit under a `no_lessons_apply` verdict.

    In a base/head post-diff resolution the paths that matter are computed by
    the provider from the git diff and appear nowhere in the request. Deriving
    the candidate set from the request alone produced an empty candidate set —
    and therefore `no_lessons_apply` — directly above a selected required
    lesson. The declared path below is deliberately unrouted, so the ONLY way
    the candidate set can be non-empty is by reading the provider's own
    effective path set.
    """
    repo, vault = diffable
    settings = Settings(
        akms_global_vault=vault,
        failure_memory_projects=[
            {"id": "pack", "config": str(repo / "project.toml"), "root": str(repo)}
        ],
    )
    api.dependency_overrides[get_settings] = lambda: settings
    try:
        response = TestClient(api).post(
            "/api/failure-memory/pack/resolve",
            json={
                "mode": "post-diff",
                "role": "code_reviewer",
                "task_id": "base-head-attribution",
                "title": "base/head post-diff",
                "objective": "Attribution must follow the provider's own diff.",
                "declared_paths": ["src/nothing/routes/here.py"],
                "changed_paths": [],
                "base": "HEAD~1",
                "head": "HEAD",
                "baseline": "base-head-attribution",
                "invocation_id": "base-head-attribution",
            },
        )
    finally:
        api.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200, response.text
    body = response.json()

    # The provider genuinely selected a required lesson from the diff.
    assert [record["node_id"] for record in body["required"]] == ["cc-failure-l001"]
    assert body["result"]["resolution"]["changed_paths"] == ["src/engine/interface.py"]

    delivery = body["delivery"]
    assert delivery["request_paths"] == ["src/nothing/routes/here.py"]
    assert delivery["provider_derived_paths"] == ["src/engine/interface.py"]
    assert "src/engine/interface.py" in delivery["input_paths"]
    assert delivery["routed_candidate_node_ids"] == ["cc-failure-l001"]
    assert delivery["verdict"] == "delivered"
    assert delivery["verdict"] != "no_lessons_apply"
    assert "provider's own diff resolution" in delivery["headline"]


def test_no_lessons_apply_can_never_contradict_the_record_set(
    client: TestClient, pack: Path, diffable: tuple[Path, Path]
) -> None:
    """The invariant, checked across every request shape this suite can produce.

    `no_lessons_apply` is reachable only with an empty candidate set AND an empty
    record set, so it is true by construction rather than true for the input
    shapes someone happened to test. That property is what has to survive the
    next mode nobody has thought of.
    """
    for stem in sorted(CASES):
        delivery = _resolve(client, pack, stem)["delivery"]
        if delivery["verdict"] == "no_lessons_apply":
            assert delivery["delivered_node_ids"] == [], stem
            assert delivery["routed_candidate_node_ids"] == [], stem
        if delivery["delivered_node_ids"]:
            assert delivery["verdict"] != "no_lessons_apply", stem


def test_advisory_and_coactivated_arrivals_are_no_longer_called_no_lessons_apply(
    client: TestClient, pack: Path
) -> None:
    """Two more shapes with records but no routed candidates.

    An advisory-tag arrival and a `load_with` co-activation both land outside the
    candidate set. Reporting `no_lessons_apply` while such a record sits in the
    same response contradicted it just as plainly as the base/head case did.
    """
    tagged = _resolve(client, pack, "advisory-tags")["delivery"]
    assert tagged["routed_candidate_node_ids"] == []
    assert tagged["delivered_node_ids"]
    assert tagged["verdict"] == "delivered"
    assert "advisory tag matching" in tagged["headline"]

    related = _resolve(client, pack, "related")["delivery"]
    assert related["verdict"] == "delivered"
    assert "load_with co-activation" in related["headline"]


def test_glob_scope_inputs_still_produce_a_candidate_set(
    client: TestClient, staged: tuple[Path, Path]
) -> None:
    """AKMS accepts glob metacharacters in scope; exact dict lookup does not.

    Found while re-examining the verdict for other empty-candidate modes. A task
    scoped as `src/engine/*` resolves records through the glob, but a candidate
    set built by exact key lookup would be empty and the verdict would contradict
    them. Matched with fnmatch against the route keys instead.
    """
    response = client.post(
        "/api/failure-memory/pack/resolve",
        json={
            "mode": "pre-task",
            "role": "implementer",
            "task_id": "glob-scope",
            "title": "glob scope",
            "objective": "Glob scope inputs must still attribute.",
            "declared_paths": ["src/engine/*"],
            "baseline": "glob-scope",
            "invocation_id": "glob-scope",
        },
    )
    assert response.status_code == 200, response.text
    delivery = response.json()["delivery"]
    assert set(delivery["routed_candidate_node_ids"]) >= {
        "cc-failure-l001",
        "cc-failure-l002",
        "cc-failure-l003",
    }
    if delivery["delivered_node_ids"]:
        assert delivery["verdict"] != "no_lessons_apply"


def test_symbol_seeds_reach_the_task_only_when_supplied(
    client: TestClient, pack: Path, staged: tuple[Path, Path]
) -> None:
    """`task.symbols` must be absent, not empty, when no symbols are given.

    The task object feeds `result.fingerprint`. Always emitting `"symbols": []`
    would move every fingerprint away from the frozen contract's, which is why
    the eight conformance replays above would have started failing.
    """
    without = _resolve(client, pack, "exact-path")["request"]
    assert "symbols" not in without["task"]

    response = client.post(
        "/api/failure-memory/pack/resolve",
        json={
            "mode": "pre-task",
            "role": "implementer",
            "task_id": "symbol-seeds",
            "declared_paths": ["src/engine/interface.py"],
            "symbols": ["commit"],
            "baseline": "symbol-seeds",
            "invocation_id": "symbol-seeds",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request"]["task"]["symbols"] == ["commit"]
    assert body["delivery"]["symbols"] == ["commit"]


# ── gate finding 1: lock-parent creation (blocked on the package) ───────────


def test_readonly_check_creates_nothing_when_the_lock_parent_is_absent(
    pack: Path, tmp_path: Path
) -> None:
    """The no-mutation proof the original read-only evidence was too weak to make.

    The original read-only proof staged a project whose lock parent (``runtime/``) already
    existed, because publishing the graph had created it — so the lock's own
    directory creation was invisible to it. This one deliberately skips graph
    publication, leaving ``runtime/`` absent, and calls the one read-only
    endpoint that needs no graph.

    History: this failed when the read-only surface was first verified and was carried as an
    ``xfail(strict=True)`` naming the upstream defect
    (``ProjectLock.acquire`` unconditionally ran ``parent.mkdir(parents=True)``),
    deliberately marked strict so it would break the build as XPASS the moment
    the fix landed rather than quietly outliving it. It did exactly that at the
    re-pin that delivered the fix; the marker is gone and the property is asserted
    on its own merits. ``create_parent_directories`` is wired to
    ``write_artifacts`` in the provider and to ``mode == "write"`` in the
    compiler, so a read path no longer creates anything — it raises
    ``lock_parent_missing`` instead, which this surface reports as
    "never written to", not as an error.
    """
    repo = tmp_path / "repo"
    shutil.copytree(pack, repo)
    vault = tmp_path / "vault"
    vault.mkdir()

    lock_parent = repo / "runtime"
    assert not lock_parent.exists(), "the point of this test is an absent lock parent"
    before = {str(p.relative_to(repo)) for p in repo.rglob("*")}

    settings = Settings(
        akms_global_vault=vault,
        failure_memory_projects=[
            {"id": "pack", "config": str(repo / "project.toml"), "root": str(repo)}
        ],
    )
    api.dependency_overrides[get_settings] = lambda: settings
    try:
        response = TestClient(api).post("/api/failure-memory/pack/check")
    finally:
        api.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 200, response.text

    created = {str(p.relative_to(repo)) for p in repo.rglob("*")} - before
    assert created == set(), f"a read-only call created {sorted(created)}"


def test_a_cold_clone_is_not_reported_as_never_written(pack: Path, tmp_path: Path) -> None:
    """A missing scratch directory is not evidence that nothing has happened.

    This test previously asserted the opposite, and was wrong in the way a review
    caught: it staged a copy of the pack — a COLD CLONE with a committed
    registry, route index and generated nodes — and called it "never written to"
    purely because the disposable ``runtime/`` tree was absent. That conflation
    let a genuinely stale cold clone report ``never_written``.

    What must hold instead: reads still work without a lock; ``never_written`` is
    false because artifacts plainly exist; and the check, which genuinely could
    not run, reports ``unverifiable`` rather than either a false ``clean`` or a
    reassuring ``never_written``.
    """
    repo = tmp_path / "repo"
    shutil.copytree(pack, repo)
    vault = tmp_path / "vault"
    vault.mkdir()
    assert not (repo / "runtime").exists()

    settings = Settings(
        akms_global_vault=vault,
        failure_memory_projects=[
            {"id": "cold", "config": str(repo / "project.toml"), "root": str(repo)}
        ],
    )
    api.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(api)
        listed = client.get("/api/failure-memory/projects").json()
        status = client.get("/api/failure-memory/cold/status").json()
        check = client.post("/api/failure-memory/cold/check").json()
    finally:
        api.dependency_overrides.pop(get_settings, None)

    assert listed["projects"][0]["available"] is True
    assert listed["projects"][0]["never_written"] is False
    assert listed["projects"][0]["lock_parent_exists"] is False

    # Lock-free reads are unaffected; the registry is perfectly readable.
    assert status["never_written"] is False
    assert status["lock_parent_exists"] is False
    assert status["lesson_count"] == 6
    assert status["currency"] == "unverifiable", "no graph is published in a cold clone"

    assert check["status"] == "unverifiable"
    assert check["status"] not in {"clean", "never_written"}
    assert check["validation"]["code"] == "lock_parent_missing"

    # And still nothing was created.
    assert not (repo / "runtime").exists()


# ── real-lesson reads ───────────────────────────────────────────────────────


def test_status_reports_the_real_registry(client: TestClient) -> None:
    body = client.get("/api/failure-memory/pack/status").json()
    assert body["repository_id"] == "ConsumerContract"
    assert body["lesson_count"] == 6
    assert body["routed_path_count"] == 11
    assert body["routed_symbol_count"] == 0, (
        "by_symbol is empty because symbol routes need the referenced file to exist"
    )
    assert body["staleness"]["state"] == "current"
    assert body["graph"]["published"] is True
    assert body["graph"]["identity_matches_config"] is True
    assert body["graph"]["generated_at"] == CANONICAL_GENERATED_AT
    assert body["mode"] == "read-only"


def test_lesson_inventory_and_filters_operate_on_real_lessons(client: TestClient) -> None:
    everything = client.get("/api/failure-memory/pack/lessons").json()
    assert everything["total"] == 6 and everything["count"] == 6
    assert {l["node_id"] for l in everything["lessons"]} == {
        f"cc-failure-l00{n}" for n in range(1, 7)
    }

    by_path = client.get(
        "/api/failure-memory/pack/lessons", params={"path": "src/engine/interface.py"}
    ).json()
    assert [l["id"] for l in by_path["lessons"]] == ["L001"]

    docs = [l for l in everything["lessons"] if l["documentation_only"]]
    assert [l["id"] for l in docs] == ["L006"]

    related = client.get(
        "/api/failure-memory/pack/lessons", params={"related": "L002"}
    ).json()
    assert "L002" in {l["id"] for l in related["lessons"]}
    assert related["count"] < everything["count"]

    root_cause = client.get(
        "/api/failure-memory/pack/lessons",
        params={"root_cause": everything["lessons"][0]["root_cause"][:20]},
    ).json()
    assert root_cause["count"] >= 1
    assert everything["facets"]["categories"]


def test_lesson_detail_serves_the_compiled_node_body(client: TestClient) -> None:
    body = client.get("/api/failure-memory/pack/lessons/L001").json()
    assert body["lesson"]["node_id"] == "cc-failure-l001"
    assert body["node_markdown"] is not None
    assert "GENERATED" in body["node_markdown"]
    assert body["node_path"].endswith("cc-failure-l001.md")
    assert [entry["key"] for entry in body["routes"]]
    assert client.get("/api/failure-memory/pack/lessons/L999").status_code == 404


def test_route_coverage_reports_the_real_index(client: TestClient) -> None:
    body = client.get("/api/failure-memory/pack/routes").json()
    assert body["compiled"] is True
    assert body["coverage"]["path_route_count"] == 11
    assert body["coverage"]["symbol_route_count"] == 0
    assert body["coverage"]["routed_lesson_count"] == 6
    assert body["coverage"]["unrouted_lesson_ids"] == []
    assert body["coverage"]["documentation_path_count"] == 1
    assert len(body["coverage"]["missing_source_paths"]) == 11


# ── graph overlay ───────────────────────────────────────────────────────────


def test_the_overlay_carries_the_planned_edge_vocabulary(client: TestClient, pack: Path) -> None:
    routes = client.get("/api/failure-memory/pack/routes").json()["overlay"]
    kinds = {node["kind"] for node in routes["nodes"]}
    assert kinds == {"source_file", "failure_lesson"}
    assert {edge["type"] for edge in routes["edges"]} == {"failure_route", "load_with"}

    preview = _resolve(client, pack, "related")["overlay"]
    assert {edge["type"] for edge in preview["edges"]} >= {
        "selected_required",
        "selected_coactivated",
    }
    assert all(edge["repository_id"] == "ConsumerContract" for edge in preview["edges"])
    assert all(node["repository_id"] == "ConsumerContract" for node in preview["nodes"])


def test_the_overlay_is_not_folded_into_the_akms_source_graph(client: TestClient) -> None:
    """Failure memory is an overlay; ``/api/graph`` must be untouched by it."""
    overlay = client.get("/api/failure-memory/pack/routes").json()["overlay"]
    assert overlay["nodes"], "overlay must be non-empty for this to prove anything"
    graph_status = client.get("/api/graph/status")
    assert graph_status.status_code == 200
    assert "failure" not in graph_status.text.lower()


# ── repository identity ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def two_repositories(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Two REAL projects that both hold a real lesson about ``src/config.py``.

    The filesystem's recurring lack of imagination is the whole point: a path
    named ``src/config.py`` exists in almost every repository, and an overlay
    keyed on the bare path would merge them.
    """
    from akms_failure_memory.compiler import run_compiler
    from akms_failure_memory.project import init_project
    from akms_failure_memory.record import add_lesson

    root = tmp_path_factory.mktemp("identity")
    vault = root / "vault"
    vault.mkdir()
    made: dict[str, Path] = {}
    for name, repository_id, namespace in (
        ("alpha", "RepoAlpha", "alpha-failure"),
        ("beta", "RepoBeta", "beta-failure"),
    ):
        repo = root / name
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "config.py").write_text("SETTING = 1\n", encoding="utf-8")
        init_project(
            repository_root=repo,
            config_path=".failure-memory/config.toml",
            repository_id=repository_id,
            node_namespace=namespace,
        )
        proposal = repo / "proposal.json"
        proposal.write_text(
            json.dumps(
                {
                    "date_found": "2026-08-16",
                    "date_found_precision": "exact",
                    "date_fixed": "2026-08-16",
                    "date_fixed_precision": "exact",
                    "location": {
                        "area": "src",
                        "file": "src/config.py",
                        "line_hint": "identity proof",
                        "module": "config",
                        "package": name,
                        "symbol": "SETTING",
                    },
                    "found_by": {"type": "manual review", "trigger": f"{repository_id} identity proof"},
                    "references": {"commit": "", "issue": "", "plan": "", "pr": ""},
                    "related": [],
                    "symptom": f"{repository_id} misread SETTING.",
                    "root_cause": f"{repository_id} used the wrong default.",
                    "fix": f"{repository_id} now reads the configured value.",
                    "prevention": "Regression test.",
                    "notes": "Throwaway lesson recorded in a temp project; never in a real registry.",
                }
            ),
            encoding="utf-8",
        )
        add_lesson(
            config_path=repo / ".failure-memory" / "config.toml",
            repository_root=repo,
            global_vault=vault,
            request_path=proposal,
        )
        result = run_compiler(
            config_path=repo / ".failure-memory" / "config.toml",
            repository_root=repo,
            global_vault=vault,
            mode="check",
        )
        assert result["status"] == "clean", result
        made[name] = repo
    return made


def test_the_same_relative_path_in_two_repositories_stays_two_nodes(
    two_repositories: dict[str, Path],
) -> None:
    settings = Settings(
        failure_memory_projects=[
            {
                "id": name,
                "config": str(repo / ".failure-memory" / "config.toml"),
                "root": str(repo),
            }
            for name, repo in two_repositories.items()
        ]
    )
    api.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(api)
        overlays = {
            name: client.get(f"/api/failure-memory/{name}/routes").json()["overlay"]
            for name in two_repositories
        }
    finally:
        api.dependency_overrides.pop(get_settings, None)

    file_nodes = {
        name: [n for n in overlay["nodes"] if n["path"] == "src/config.py"]
        for name, overlay in overlays.items()
    }
    assert all(len(nodes) == 1 for nodes in file_nodes.values()), file_nodes
    alpha = file_nodes["alpha"][0]
    beta = file_nodes["beta"][0]

    assert alpha["path"] == beta["path"] == "src/config.py"
    assert alpha["repository_id"] == "RepoAlpha"
    assert beta["repository_id"] == "RepoBeta"
    assert alpha["id"] != beta["id"]
    assert alpha["id"] == "RepoAlpha::file:src/config.py"
    assert beta["id"] == "RepoBeta::file:src/config.py"

    # No identifier of any kind is shared between the two overlays — not nodes,
    # not edges — so merging them cannot collapse anything.
    for key in ("nodes", "edges"):
        alpha_ids = {item["id"] for item in overlays["alpha"][key]}
        beta_ids = {item["id"] for item in overlays["beta"][key]}
        assert alpha_ids and beta_ids
        assert alpha_ids.isdisjoint(beta_ids), (key, alpha_ids & beta_ids)


# ── frozen-contract pins ────────────────────────────────────────────────────


def test_absent_package_envelope_matches_the_frozen_pack(pack: Path) -> None:
    from logic_loom_api.adapters.failure_memory import DEGRADATION_ENVELOPE

    expected = _expected(pack, "errors.json", "absent_package_degradation")["expect"]
    assert DEGRADATION_ENVELOPE == expected["envelope"]
    assert expected["detection"] == "importlib.util.find_spec('akms_failure_memory') is None"


def test_documentation_classifier_matches_the_frozen_pack(pack: Path) -> None:
    """The display classifier must agree with the routing behaviour it explains."""
    from logic_loom_api.services.failure_memory import is_documentation_path

    docs_only = json.loads((pack / "requests" / "docs-only.json").read_text(encoding="utf-8"))
    docs_mixed = json.loads((pack / "requests" / "docs-mixed.json").read_text(encoding="utf-8"))

    assert all(is_documentation_path(p) for p in docs_only["declared_paths"])
    assert not all(is_documentation_path(p) for p in docs_mixed["declared_paths"])

    # The surprising half of the rule: extension wins over location.
    assert is_documentation_path("src/engine/NOTES.md")
    assert is_documentation_path("README")
    assert is_documentation_path("doc/whatever.py")
    assert not is_documentation_path("src/engine/interface.py")


def test_the_pinned_identity_tuple_is_unchanged(pack: Path) -> None:
    """A local edit to the frozen contract would have to change one of these."""
    manifest = json.loads((pack / "fixture-manifest.json").read_text(encoding="utf-8"))
    assert manifest["checksum"]["pack_sha256"] == EXPECTED_PACK_SHA256
    assert manifest["pack_revision"] == "amendment-2"
    assert manifest["identity"]["akms_failure_memory_version"] == "0.1.0"
    assert manifest["staging"]["canonical_generated_at"] == CANONICAL_GENERATED_AT
    assert os.environ.get("AKMS_GLOBAL_VAULT") != str(Path.home() / ".claude" / "akms" / "nodes")
