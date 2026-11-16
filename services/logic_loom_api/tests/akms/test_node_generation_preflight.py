"""Node-generation preflight route tests.

Missing claude returns ``{name: "claude_cli", ok: false,
detail: "not found on PATH"}`` without raising.

The preflight delegates to ``akms_dispatch.preflight_node_generation``
which is unit-tested at the helper level; the route layer is exercised
via FastAPI TestClient to verify the JSON shape and HTTP semantics.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from logic_loom_api.routes import akms_dispatch as akms_dispatch_routes


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(
        akms_dispatch_routes.router,
        prefix="/api/akms/node-generation",
    )
    return app


def _stub_binary(tmp_path: Path, name: str, rc: int = 0, stdout: str = "ok") -> Path:
    """Drop a small executable script that mimics a CLI for preflight tests.

    POSIX: a `.py` with a shebang + executable bit. Windows: a `.bat`
    wrapper that invokes the .py via the current Python interpreter
    (shebangs are not honoured by CreateProcess on Windows and `.py`
    is not in PATHEXT by default).
    """
    py_body = textwrap.dedent(
        f"""\
        import sys
        sys.stdout.write({stdout!r} + "\\n")
        sys.exit({rc})
        """
    )
    if os.name == "nt":
        py_path = tmp_path / f"{name}.py"
        py_path.write_text(py_body, encoding="utf-8")
        bat = tmp_path / f"{name}.bat"
        bat.write_text(
            f'@echo off\r\n"{sys.executable}" "{py_path}" %*\r\n',
            encoding="utf-8",
        )
        return bat
    p = tmp_path / name
    p.write_text(f"#!{sys.executable}\n{py_body}", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _structured_path_only(tmp_path: Path, fakes: dict[str, Path], monkeypatch):
    """Make ``shutil.which`` resolve ``fakes`` and nothing else."""

    def fake_which(cmd: str, *args, **kwargs):
        # Allow the configured fakes; otherwise return None so the
        # preflight surfaces a structured "not found" check.
        if cmd in fakes:
            return str(fakes[cmd])
        return None

    monkeypatch.setattr("logic_loom_api.akms_dispatch.shutil.which", fake_which)


class TestNodeGenerationPreflight:
    """Preflight route: structured per-check results, never raises."""

    def test_preflight_returns_structured_shape(self, tmp_path, monkeypatch):
        """Even with everything missing, the response shape is
        ``{ok: bool, checks: [{name, ok, detail}]}``."""
        # Force every binary to be absent.
        _structured_path_only(tmp_path, {}, monkeypatch)
        client = TestClient(_app())
        resp = client.post("/api/akms/node-generation/preflight", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert "ok" in body and isinstance(body["ok"], bool)
        assert "checks" in body and isinstance(body["checks"], list)
        # The preflight runs 12 checks.
        assert len(body["checks"]) == 12
        for c in body["checks"]:
            assert set(c.keys()) >= {"name", "ok", "detail"}

    def test_preflight_missing_claude_reports_structured_fail(
        self, tmp_path, monkeypatch
    ):
        """Missing claude shows up as a structured failed check
        with detail ``"not found on PATH"``, no raise."""
        # No claude available; nlm fakely present so we isolate the claude check.
        _structured_path_only(tmp_path, {"nlm": _stub_binary(tmp_path, "nlm")}, monkeypatch)
        client = TestClient(_app())
        resp = client.post(
            "/api/akms/node-generation/preflight",
            json={"claude_binary": "claude", "nlm_binary": "nlm"},
        )
        assert resp.status_code == 200
        checks = {c["name"]: c for c in resp.json()["checks"]}
        assert checks["claude_cli"]["ok"] is False
        assert checks["claude_cli"]["detail"] == "not found on PATH"
        # claude_version dependent on claude_cli — also structured fail.
        assert checks["claude_version"]["ok"] is False

    def test_preflight_missing_nlm_reports_structured_fail(
        self, tmp_path, monkeypatch
    ):
        """Missing ``nlm`` shows up as a structured failed check, no raise."""
        _structured_path_only(
            tmp_path, {"claude": _stub_binary(tmp_path, "claude")}, monkeypatch
        )
        client = TestClient(_app())
        resp = client.post(
            "/api/akms/node-generation/preflight",
            json={"claude_binary": "claude", "nlm_binary": "nlm"},
        )
        assert resp.status_code == 200
        checks = {c["name"]: c for c in resp.json()["checks"]}
        assert checks["nlm_cli"]["ok"] is False
        assert checks["nlm_cli"]["detail"] == "not found on PATH"
        assert checks["nlm_notebook_list"]["ok"] is False

    def test_preflight_top_level_ok_is_aggregate(self, tmp_path, monkeypatch):
        """``ok`` at the top level is ``all(check.ok for check in checks)``."""
        _structured_path_only(tmp_path, {}, monkeypatch)
        client = TestClient(_app())
        resp = client.post("/api/akms/node-generation/preflight", json={})
        assert resp.status_code == 200
        body = resp.json()
        # With no tools present, top-level ok must be False.
        assert body["ok"] is False
        # And at least one individual check is False to back it up.
        assert any(not c["ok"] for c in body["checks"])

    def test_preflight_includes_all_twelve_checks(self, tmp_path, monkeypatch):
        """All 12 preflight checks are present by name."""
        _structured_path_only(tmp_path, {}, monkeypatch)
        client = TestClient(_app())
        resp = client.post("/api/akms/node-generation/preflight", json={})
        names = {c["name"] for c in resp.json()["checks"]}
        expected = {
            "akms",
            "akms_nodes_gen",
            "bundled_assets",
            "runtime_materialisable",
            "claude_cli",
            "claude_version",
            "nlm_cli",
            "nlm_notebook_list",
            "nlm_id",
            "plan_json",
            "output_dir_writable",
            "akms_vault",
        }
        assert names == expected

    def test_preflight_empty_body_is_accepted(self, tmp_path, monkeypatch):
        """The route accepts an empty body — used by the frontend's
        initial 'is AKMS workable?' smoke check before the user has
        selected an NLM id / plan path."""
        _structured_path_only(tmp_path, {}, monkeypatch)
        client = TestClient(_app())
        resp = client.post("/api/akms/node-generation/preflight")
        assert resp.status_code == 200
        assert "checks" in resp.json()
