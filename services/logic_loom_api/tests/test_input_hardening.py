"""Input and secrets hardening (QA triage batch 2).

Covers: prompt-library id traversal, node file reads outside the node folders,
API keys in GET /api/app/config, command settings, and the error envelope
(no tracebacks, no 500 on non-JSON bodies or NUL bytes).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from logic_loom_api.deps import reload_settings
from logic_loom_api.errors import install_handlers
from logic_loom_api.main import api
from logic_loom_api.prompts.store import PromptStore
from logic_loom_api.routes.app import SECRET_MASK
from logic_loom_api.safe_paths import InvalidIdentifier, ensure_under, safe_id


@pytest.fixture
def env(tmp_path: Path):
    workspace = tmp_path / "akms-workspace"
    vault = tmp_path / "vault"
    nodes = tmp_path / "nodes"
    vault.mkdir()
    nodes.mkdir()
    values = {
        "LOGIC_LOOM_CONFIG": str(tmp_path / "config.toml"),
        "LOGIC_LOOM_VAULT_PATH": str(vault),
        "LOGIC_LOOM_AKMS_WORKSPACE": str(workspace),
        # json.dumps escapes Windows backslashes.
        "LOGIC_LOOM_NODES_FOLDERS": json.dumps([str(nodes)]),
    }
    with patch.dict(os.environ, values):
        reload_settings()
        yield {"tmp": tmp_path, "workspace": workspace, "vault": vault, "nodes": nodes}
    reload_settings()


@pytest.fixture
def client(env) -> TestClient:
    return TestClient(api)


# ── safe_paths ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("good", ["default", "akms.node_gen_skill", "strict-v2", "A_1"])
def test_safe_id_accepts(good: str) -> None:
    assert safe_id(good) == good


@pytest.mark.parametrize(
    "bad", ["", "../x", "..", ".hidden", "a/b", "a\\b", "a\x00b", "x" * 129, "has space"]
)
def test_safe_id_rejects(bad: str) -> None:
    with pytest.raises(InvalidIdentifier):
        safe_id(bad)


def test_ensure_under_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(InvalidIdentifier):
        ensure_under(root, root / ".." / "elsewhere.json")
    assert ensure_under(root, root / "ok.json") == root / "ok.json"


# ── prompt library ──────────────────────────────────────────────────────────


def test_store_refuses_traversal_ids(tmp_path: Path) -> None:
    store = PromptStore(tmp_path / "lib")
    for call in (
        lambda: store.profile_path("../../../../tmp/llprobe"),
        lambda: store.schema_path("../escape"),
        lambda: store.variant_path("akms.node_gen_skill", "../../x"),
    ):
        with pytest.raises(InvalidIdentifier):
            call()


def test_profile_post_with_traversal_id_is_422_and_writes_nothing(client: TestClient, env) -> None:
    target = env["tmp"] / "llprobe.json"
    r = client.post(
        "/api/prompts/profiles",
        json={"id": f"../../../{env['tmp'].name}/llprobe", "name": "x"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "invalid_identifier"
    assert not target.exists()


def test_profile_post_with_normal_id_still_works(client: TestClient) -> None:
    r = client.post("/api/prompts/profiles", json={"id": "my-profile", "name": "Mine"})
    assert r.status_code == 200, r.text


# ── node files ──────────────────────────────────────────────────────────────


def test_nodes_file_outside_node_folders_is_422(client: TestClient, env) -> None:
    outside = env["tmp"] / "secret.txt"
    outside.write_text("top secret", encoding="utf-8")
    r = client.get("/api/nodes/file", params={"path": str(outside)})
    assert r.status_code == 422
    assert "top secret" not in r.text


def test_nodes_file_inside_node_folder_is_read(client: TestClient, env) -> None:
    node = env["nodes"] / "n.md"
    node.write_text("---\nid: n\n---\nbody", encoding="utf-8")
    r = client.get("/api/nodes/file", params={"path": str(node)})
    assert r.status_code == 200
    assert r.json()["frontmatter"]["id"] == "n"


def test_nodes_file_binary_is_415(client: TestClient, env) -> None:
    blob = env["nodes"] / "blob.md"
    blob.write_bytes(b"\xff\xfe\x00\x81binary")
    r = client.get("/api/nodes/file", params={"path": str(blob)})
    assert r.status_code == 415


def test_nodes_open_outside_node_folders_is_422(client: TestClient, env) -> None:
    r = client.post("/api/nodes/open", json={"path": sys.executable})
    assert r.status_code == 422


@pytest.mark.skipif(os.name == "nt", reason="POSIX opener path")
def test_nodes_open_without_editor_or_opener_explains(
    client: TestClient, env, monkeypatch
) -> None:
    """QA batch 7: Open in Editor gave no feedback when nothing could open."""
    from logic_loom_api.routes import nodes as nodes_mod

    node = env["nodes"] / "n.md"
    node.write_text("---\nid: n\n---\nbody", encoding="utf-8")
    monkeypatch.setattr(nodes_mod.shutil, "which", lambda _name: None)
    spawned: list[object] = []
    monkeypatch.setattr(nodes_mod.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    r = client.post("/api/nodes/open", json={"path": str(node)})
    assert r.status_code == 503
    assert "Editor command" in r.text
    assert spawned == []


def test_nodes_open_reports_missing_editor_command(client: TestClient, env, monkeypatch) -> None:
    from logic_loom_api.routes import nodes as nodes_mod

    node = env["nodes"] / "n.md"
    node.write_text("---\nid: n\n---\nbody", encoding="utf-8")
    monkeypatch.setenv("LOGIC_LOOM_NODES_EDITOR", "no-such-editor-xyz")
    reload_settings()

    def _missing(*_a, **_k):
        raise FileNotFoundError("no-such-editor-xyz")

    monkeypatch.setattr(nodes_mod.subprocess, "Popen", _missing)
    r = client.post("/api/nodes/open", json={"path": str(node)})
    assert r.status_code == 503
    assert "no-such-editor-xyz" in r.text


def test_nodes_open_with_editor_says_so(client: TestClient, env, monkeypatch) -> None:
    from logic_loom_api.routes import nodes as nodes_mod

    node = env["nodes"] / "n.md"
    node.write_text("---\nid: n\n---\nbody", encoding="utf-8")
    monkeypatch.setenv("LOGIC_LOOM_NODES_EDITOR", "myeditor --wait")
    reload_settings()
    monkeypatch.setattr(nodes_mod.subprocess, "Popen", lambda *a, **k: None)
    r = client.post("/api/nodes/open", json={"path": str(node)})
    assert r.status_code == 200
    assert r.json()["via"] == "editor"


# ── secrets ─────────────────────────────────────────────────────────────────


def test_config_get_masks_api_keys(client: TestClient) -> None:
    r = client.post("/api/app/config", json={"openai_api_key": "sk-real-secret"})
    assert r.status_code == 200, r.text
    cfg = client.get("/api/app/config").json()
    assert cfg["openai_api_key"] == SECRET_MASK
    assert cfg["anthropic_api_key"] is None
    assert "sk-real-secret" not in client.get("/api/app/config").text


def test_posting_the_mask_back_keeps_the_stored_key(client: TestClient, env) -> None:
    client.post("/api/app/config", json={"openai_api_key": "sk-real-secret"})
    client.post("/api/app/config", json={"openai_api_key": SECRET_MASK})
    toml = (env["tmp"] / "config.toml").read_text(encoding="utf-8")
    assert "sk-real-secret" in toml
    assert SECRET_MASK not in toml


def test_empty_key_still_clears(client: TestClient, env) -> None:
    client.post("/api/app/config", json={"openai_api_key": "sk-real-secret"})
    client.post("/api/app/config", json={"openai_api_key": ""})
    assert client.get("/api/app/config").json()["openai_api_key"] is None


# ── command settings ────────────────────────────────────────────────────────


def test_editor_must_name_an_existing_program(client: TestClient) -> None:
    r = client.post("/api/app/config", json={"nodes_editor": "definitely-not-a-program-xyz --wait"})
    assert r.status_code == 422
    r = client.post("/api/app/config", json={"nodes_editor": 'code "unterminated'})
    assert r.status_code == 422


def _fake_program(tmp: Path, name: str) -> str:
    """An executable file called ``name``; validation only checks it exists."""
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    prog = bin_dir / name
    prog.write_text("#!/bin/sh\nexit 0\n")
    prog.chmod(0o755)
    return str(prog)


def test_editor_accepts_a_known_editor_and_clearing(client: TestClient, env) -> None:
    code = _fake_program(env["tmp"], "code")
    r = client.post("/api/app/config", json={"nodes_editor": f'"{code}" --wait'})
    assert r.status_code == 200, r.text
    assert client.post("/api/app/config", json={"nodes_editor": ""}).status_code == 200


@pytest.mark.parametrize(
    "editor",
    [
        f'"{sys.executable}" -c pass',  # not an editor
        "/bin/echo",  # not an editor (QA: ran through /api/nodes/open)
        "touch /tmp/llpwn #",  # QA: accepted in v0.3.3
    ],
)
def test_editor_rejects_programs_that_are_not_editors(client: TestClient, editor: str) -> None:
    if editor.startswith("/bin/") and not Path(editor).exists():
        pytest.skip("no /bin/echo here")
    r = client.post("/api/app/config", json={"nodes_editor": editor})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("args", ["-c '!touch /tmp/x'", "--eval '(shell-command \"x\")'", "--wait /etc/passwd"])
def test_editor_rejects_arguments_that_are_not_plain_flags(client: TestClient, env, args: str) -> None:
    gvim = _fake_program(env["tmp"], "gvim")
    r = client.post("/api/app/config", json={"nodes_editor": f'"{gvim}" {args}'})
    assert r.status_code == 422, r.text


def test_open_dash_a_app_is_allowed(client: TestClient, env) -> None:
    opener = _fake_program(env["tmp"], "open")
    r = client.post("/api/app/config", json={"nodes_editor": f'"{opener}" -a TextEdit'})
    assert r.status_code == 200, r.text


def test_zsum_command_must_be_python_uv_or_zsum(client: TestClient, env) -> None:
    ok = client.post("/api/app/config", json={"zsum_python_cmd": f'"{sys.executable}" -m zsum.cli'})
    assert ok.status_code == 200, ok.text
    uv = _fake_program(env["tmp"], "uv")
    assert client.post("/api/app/config", json={"zsum_python_cmd": f'"{uv}" run zsum'}).status_code == 200
    echo = _fake_program(env["tmp"], "echo")
    assert client.post("/api/app/config", json={"zsum_python_cmd": echo}).status_code == 422
    r = client.post("/api/app/config", json={"zsum_python_cmd": f'"{sys.executable}" -c pass'})
    assert r.status_code == 422


def test_config_post_never_echoes_a_key(client: TestClient) -> None:
    r = client.post("/api/app/config", json={"openai_api_key": "sk-real-secret", "llm_provider": "claude"})
    assert r.status_code == 200
    assert "sk-real-secret" not in r.text
    assert r.json()["applied"]["openai_api_key"] == SECRET_MASK
    assert r.json()["applied"]["llm_provider"] == "claude"


# ── path-exists ─────────────────────────────────────────────────────────────


def test_path_exists_answers_inside_configured_folders(client: TestClient, env) -> None:
    r = client.get("/api/app/path-exists", params={"path": str(env["vault"])})
    assert r.status_code == 200
    assert r.json()["is_dir"] is True


def test_path_exists_refuses_other_paths(client: TestClient) -> None:
    r = client.get("/api/app/path-exists", params={"path": "/etc/passwd"})
    assert r.status_code == 422


def test_path_exists_nul_is_422(client: TestClient) -> None:
    r = client.get("/api/app/path-exists", params={"path": "a\x00b"})
    assert r.status_code == 422


def test_path_exists_allows_yaml_under_home(client: TestClient, env, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: env["tmp"]))
    r = client.get("/api/app/path-exists", params={"path": str(env["tmp"] / "x" / "zsum.config.yaml")})
    assert r.status_code == 200
    assert r.json()["exists"] is False


# ── prompt variants ─────────────────────────────────────────────────────────


def test_variant_with_unsafe_filename_is_rejected(client: TestClient, env) -> None:
    body = {
        "slot": "zsum.paper_card",
        "name": "evil",
        "filename": "../../../../tmp/x.md",
        "content": "hello",
    }
    r = client.post("/api/prompts/variant", json=body)
    assert r.status_code == 400
    assert "unsafe filename" in r.text


# ── error envelope ──────────────────────────────────────────────────────────


def test_non_json_body_is_422_not_500(client: TestClient) -> None:
    r = client.post(
        "/api/prompts/profiles",
        content=b"\x00\xffnot json",
        headers={"content-type": "text/plain"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def _boom_app() -> FastAPI:
    app = FastAPI()
    install_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("internal detail that must not leak")

    @app.get("/nul")
    def nul() -> None:
        Path("a\x00b").read_text()

    return app


def test_500_has_no_traceback() -> None:
    r = TestClient(_boom_app(), raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert "traceback" not in body["error"]["detail"]
    assert "internal detail" not in r.text


def test_nul_byte_reaching_the_filesystem_is_422() -> None:
    r = TestClient(_boom_app(), raise_server_exceptions=False).get("/nul")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_identifier"


def test_split_command_keeps_windows_backslashes(monkeypatch: pytest.MonkeyPatch) -> None:
    from logic_loom_api import safe_paths

    monkeypatch.setattr(safe_paths.os, "name", "nt")
    assert safe_paths.split_command(r'"C:\Program Files\Ed\ed.exe" -w') == [
        r"C:\Program Files\Ed\ed.exe",
        "-w",
    ]
    assert safe_paths.split_command(r"C:\tools\ed.exe") == [r"C:\tools\ed.exe"]
    with pytest.raises(ValueError):
        safe_paths.split_command('"unterminated')
