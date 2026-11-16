"""Behaviour contract for ``/api/failure-memory/*`` (read-only surface).

Covers the parts that must hold with or without the optional package installed:
discovery, mode gating, degradation, explicit-configuration-only project
resolution, and the operator endpoints' typed refusal.

Real-data proofs — fixture conformance, the delivered-nowhere distinction, the
mutation audit and repository identity — live in
``test_failure_memory_conformance.py``, deliberately separate: five defects in
this program were invisible purely because registries held zero lessons, so any
test that would still pass against an empty registry is not evidence of much.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.adapters import failure_memory as adapter
from logic_loom_api.config import Settings
from logic_loom_api.deps import get_settings
from logic_loom_api.main import api

pytestmark = pytest.mark.integration

PER_PROJECT_GETS = ("status", "lessons", "routes")
PER_PROJECT_POSTS = ("resolve", "check")
OPERATOR_POSTS = ("record", "refresh", "compile")


@pytest.fixture
def client() -> TestClient:
    return TestClient(api)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    api.dependency_overrides.pop(get_settings, None)


def _use(settings: Settings) -> None:
    api.dependency_overrides[get_settings] = lambda: settings


def _post(client: TestClient, url: str):
    """POST helper — /resolve needs a body, /check and the operator routes don't."""
    body = {"declared_paths": ["src/example.py"]} if url.endswith("/resolve") else None
    return client.post(url, json=body)


# ── discovery ───────────────────────────────────────────────────────────────


def test_projects_is_always_200_even_with_nothing_configured(client: TestClient) -> None:
    _use(Settings())
    response = client.get("/api/failure-memory/projects")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "read-only", "read-only must be the default mode"
    assert body["operator"] is False
    assert body["projects"] == []
    assert "never discovers projects by scanning" in (body["reason"] or "")


def test_projects_never_discovers_a_failure_memory_directory_on_disk(
    client: TestClient, tmp_path: Path
) -> None:
    """The plan's hardest configuration rule, tested rather than assumed.

    A perfectly valid, initialised ``.failure-memory/`` project is planted where
    a recursive walk would certainly find it. Because it is not listed in
    ``[[failure_memory_projects]]``, Logic-Loom must not see it — neither in the
    projects list nor by id.
    """
    project = pytest.importorskip("akms_failure_memory.project")
    repo = tmp_path / "undeclared-repo"
    repo.mkdir()
    project.init_project(
        repository_root=repo,
        config_path=".failure-memory/config.toml",
        repository_id="Undeclared",
        node_namespace="undeclared-failure",
    )
    assert (repo / ".failure-memory" / "config.toml").is_file()

    _use(Settings(logic_loom_root=tmp_path, cache_path=tmp_path / "cache"))
    listed = client.get("/api/failure-memory/projects").json()
    assert listed["projects"] == []
    assert client.get("/api/failure-memory/undeclared/status").status_code == 404


# ── mode gating ─────────────────────────────────────────────────────────────


def test_disabled_mode_blocks_every_per_project_endpoint(client: TestClient) -> None:
    _use(Settings(failure_memory_mode="disabled"))
    listed = client.get("/api/failure-memory/projects").json()
    assert listed["enabled"] is False and listed["available"] is False

    for name in PER_PROJECT_GETS:
        response = client.get(f"/api/failure-memory/anything/{name}")
        assert response.status_code == 503, name
        assert response.json()["error"]["code"] == "failure_memory_disabled"
    for name in PER_PROJECT_POSTS:
        response = _post(client, f"/api/failure-memory/anything/{name}")
        assert response.status_code == 503, name
        assert response.json()["error"]["code"] == "failure_memory_disabled"


def test_absent_package_returns_the_frozen_degradation_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The package being absent must degrade the FEATURE, not the application.

    The body is the consumer-contract degradation envelope verbatim, because
    ``provider-result.v1.json`` pins ``status`` to the constant ``"ok"`` and has
    no legal shape for a failed resolution. ``test_failure_memory_conformance``
    pins these exact bytes against the fetched pack.
    """
    monkeypatch.setattr(adapter, "failure_memory_available", lambda: False)
    _use(Settings())

    listed = client.get("/api/failure-memory/projects")
    assert listed.status_code == 200, "discovery must not 503 — the app stays up"
    assert listed.json()["package_installed"] is False
    assert listed.json()["degradation"] == adapter.DEGRADATION_ENVELOPE

    for name in PER_PROJECT_GETS:
        response = client.get(f"/api/failure-memory/anything/{name}")
        assert response.status_code == 503, name
        assert response.json() == adapter.DEGRADATION_ENVELOPE
    for name in PER_PROJECT_POSTS:
        response = _post(client, f"/api/failure-memory/anything/{name}")
        assert response.status_code == 503, name
        assert response.json() == adapter.DEGRADATION_ENVELOPE


def test_the_rest_of_the_application_is_unaffected_by_the_package_being_absent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapter, "failure_memory_available", lambda: False)
    _use(Settings())
    assert client.get("/api/app/health").json()["status"] == "ok"
    assert client.get("/api/app/version").status_code == 200
    assert client.get("/api/graph/status").status_code == 200


def test_degradation_envelope_is_copied_not_shared(client: TestClient) -> None:
    first = adapter.degradation_envelope()
    first["records"].append("tampered")
    assert adapter.degradation_envelope() == adapter.DEGRADATION_ENVELOPE


# ── project resolution ──────────────────────────────────────────────────────


def test_unknown_project_id_is_404_not_a_lookup_on_disk(client: TestClient) -> None:
    _use(Settings(failure_memory_projects=[{"id": "known", "config": "/nowhere/config.toml"}]))
    response = client.get("/api/failure-memory/other/status")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_project"


def test_a_configured_project_whose_config_is_missing_degrades_to_a_row(
    client: TestClient,
) -> None:
    """One broken entry must not take the whole projects list down."""
    _use(Settings(failure_memory_projects=[{"id": "gone", "config": "/nowhere/config.toml"}]))
    listed = client.get("/api/failure-memory/projects").json()
    assert listed["projects"][0]["available"] is False
    assert listed["projects"][0]["error_code"] == "config_missing"
    assert client.get("/api/failure-memory/gone/status").status_code == 412


def test_a_config_outside_its_declared_root_is_rejected(
    client: TestClient, tmp_path: Path
) -> None:
    """Guards the reviewer's "multi-project paths cannot escape configured roots".

    Pointing ``config`` at repository A while declaring ``root`` as repository B
    would read A's policy against B's files.
    """
    project = pytest.importorskip("akms_failure_memory.project")
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    project.init_project(
        repository_root=repo_a,
        config_path=".failure-memory/config.toml",
        repository_id="A",
        node_namespace="a-failure",
    )
    _use(
        Settings(
            failure_memory_projects=[
                {
                    "id": "crossed",
                    "config": str(repo_a / ".failure-memory" / "config.toml"),
                    "root": str(repo_b),
                }
            ]
        )
    )
    response = client.get("/api/failure-memory/crossed/status")
    assert response.status_code == 412
    assert response.json()["error"]["code"] == "path_escape"


# ── operator endpoints ──────────────────────────────────────────────────────


def test_operator_endpoints_are_refused_in_read_only_mode(client: TestClient) -> None:
    _use(Settings())
    for name in OPERATOR_POSTS:
        response = client.post(f"/api/failure-memory/anything/{name}")
        assert response.status_code == 403, name
        body = response.json()
        assert body["code"] == "operator_mode_required"
        assert body["operator"] is False
        assert body["mutating"] is False


def test_operator_mode_is_the_only_thing_that_unlocks_writing(
    client: TestClient, tmp_path: Path
) -> None:
    """Operator endpoints answer 403 in read-only mode and reach code in operator mode.

    The read-only half is the control; the operator half exists so this test
    cannot pass by the endpoints simply being broken. In operator mode the
    request gets past the guard and fails later on its own merits — here on a
    missing global vault — which is a completely different response shape.
    """
    project = pytest.importorskip("akms_failure_memory.project")
    repo = tmp_path / "repo"
    repo.mkdir()
    project.init_project(
        repository_root=repo,
        config_path=".failure-memory/config.toml",
        repository_id="Operator",
        node_namespace="operator-failure",
    )
    before = _tree_digest(repo)
    entry = {"id": "op", "config": str(repo / ".failure-memory" / "config.toml")}

    _use(Settings(failure_memory_projects=[entry]))
    for name in OPERATOR_POSTS:
        response = client.post(f"/api/failure-memory/op/{name}", json={})
        assert response.status_code == 403, name
        assert response.json()["code"] == "operator_mode_required"
        assert response.json()["mutating"] is False
    assert _tree_digest(repo) == before, "read-only mode mutated the repository"

    _use(Settings(failure_memory_mode="operator", failure_memory_projects=[entry]))
    accepted = client.post("/api/failure-memory/op/compile", json={})
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["status"] == "queued", "202 must never claim success"


def test_operator_mode_cannot_be_flipped_over_http() -> None:
    """Write permission must not be one unauthenticated POST away.

    ``failure_memory_mode`` is deliberately absent from ``ConfigUpdate`` and from
    the first-run payload, so operator mode can only be turned on by editing
    ``config.toml`` or setting ``LOGIC_LOOM_FAILURE_MEMORY_MODE``.
    """
    from logic_loom_api.routes.app import ConfigUpdate, FirstRunPayload

    assert "failure_memory_mode" not in ConfigUpdate.model_fields
    assert "failure_memory_projects" not in ConfigUpdate.model_fields
    assert "failure_memory_mode" not in FirstRunPayload.model_fields


def test_config_endpoint_reports_the_mode_read_only(client: TestClient) -> None:
    _use(Settings(failure_memory_mode="operator"))
    body = client.get("/api/app/config").json()
    assert body["failure_memory_mode"] == "operator"
    assert body["failure_memory_projects"] == []


# ── helper ──────────────────────────────────────────────────────────────────


def _tree_digest(root: Path) -> str:
    """Stable digest of every file under *root*, contents included."""
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def test_settings_parse_the_documented_toml_shape(tmp_path: Path) -> None:
    """The multi-project block from the plan must load verbatim."""
    config = tmp_path / "config.toml"
    config.write_text(
        'failure_memory_mode = "read-only"\n'
        "\n"
        "[[failure_memory_projects]]\n"
        'id = "logic-loom"\n'
        'config = "/path/to/Logic-Loom/.failure-memory/config.toml"\n'
        "\n"
        "[[failure_memory_projects]]\n"
        'id = "whatter"\n'
        'config = "/path/to/WhichWhereWhatter/.failure-memory/config.toml"\n'
        "\n"
        "[[failure_memory_projects]]\n"
        'id = "numerixweave"\n'
        'config = "/path/to/NumerixWeave/dev/knowledge/failure-memory.toml"\n'
        'root = "/path/to/NumerixWeave"\n',
        encoding="utf-8",
    )
    settings = Settings.load(config_path=config)
    assert settings.failure_memory_mode == "read-only"
    assert [p.id for p in settings.failure_memory_projects] == [
        "logic-loom",
        "whatter",
        "numerixweave",
    ]
    assert settings.failure_memory_project("numerixweave").declared_root() == Path(
        "/path/to/NumerixWeave"
    )
    assert settings.failure_memory_project("nope") is None


def test_openapi_exposes_exactly_the_planned_surface(client: TestClient) -> None:
    paths = {
        path
        for path in json.loads(client.get("/openapi.json").text)["paths"]
        if path.startswith("/api/failure-memory")
    }
    assert paths == {
        # Read-only
        "/api/failure-memory/projects",
        "/api/failure-memory/{project}/status",
        "/api/failure-memory/{project}/lessons",
        "/api/failure-memory/{project}/lessons/{lesson_id}",
        "/api/failure-memory/{project}/routes",
        "/api/failure-memory/{project}/resolve",
        "/api/failure-memory/{project}/check",
        # Operator — 403 unless failure_memory_mode = "operator"
        "/api/failure-memory/{project}/record",
        "/api/failure-memory/{project}/refresh",
        "/api/failure-memory/{project}/compile",
        # Diagnostics + jobs, readable in any mode
        "/api/failure-memory/{project}/write-diagnostics",
        "/api/failure-memory/jobs",
        "/api/failure-memory/jobs/{job_id}",
    }
