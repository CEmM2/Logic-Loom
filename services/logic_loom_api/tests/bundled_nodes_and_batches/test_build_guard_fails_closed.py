"""Regression tests: the established-only build guard fails closed on missing AKMS.

Previously ``check_resources_established_only.py`` treated the AKMS schema compile as optional: on ``ImportError`` it set
``_AKMS_AVAILABLE = False`` and ran ONLY the status check, so in any build env
where ``akms`` is not importable a structurally schema-invalid (but
``status: established``) node passes the mechanical ship gate. For a
release/build guard that advertises "mechanically impossible to ship a bad
node", fail-open is the wrong posture.

The guard now FAILS CLOSED: when ``akms`` is not importable it exits
non-zero by default, with a ``--allow-missing-akms`` / ``--dev`` opt-out that
restores the status-only fallback for local/core-only runs.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_GUARD_SCRIPT = _REPO_ROOT / "scripts" / "check_resources_established_only.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        "check_resources_established_only_p4f3", _GUARD_SCRIPT
    )
    assert spec is not None and spec.loader is not None, (
        f"Cannot load {_GUARD_SCRIPT} — file missing or unreadable"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestEstablishedGuardFailsClosed:
    """Fail-closed posture regression for the established-only build guard."""

    @pytest.mark.unit
    def test_fails_closed_when_akms_unimportable(self, monkeypatch):
        """akms not importable + no opt-out → exit non-zero (refuse to certify)."""
        guard = _load_guard()
        monkeypatch.setattr(guard, "_AKMS_AVAILABLE", False)
        monkeypatch.setattr(guard, "_GlobalNodeFrontmatter", None)

        rc = guard.main([])
        assert rc != 0, (
            "guard must FAIL CLOSED (non-zero) when akms is unimportable and no "
            f"--allow-missing-akms opt-out is given; got {rc}"
        )

    @pytest.mark.unit
    def test_opt_out_flag_allows_status_only_when_akms_absent(self, monkeypatch):
        """akms absent BUT --allow-missing-akms → status-only fallback runs.

        On the real repo the established tree is either absent (gitignored build
        artifact) or clean (status: established), so the status-only scan is
        clean → exit 0. The point is the opt-out re-enables the graceful path.
        """
        guard = _load_guard()
        monkeypatch.setattr(guard, "_AKMS_AVAILABLE", False)
        monkeypatch.setattr(guard, "_GlobalNodeFrontmatter", None)

        assert guard.main(["--allow-missing-akms"]) == 0
        # Alias flag behaves identically.
        assert guard.main(["--dev"]) == 0

    @pytest.mark.unit
    def test_offender_scan_fails_closed_even_with_opt_out(self, monkeypatch, tmp_path):
        """The opt-out does NOT weaken the status check: a non-established node
        under the scanned tree is still an offender → non-zero.

        We point the guard's default-root resolver at a planted tree so the
        status-only path (akms absent) still catches a ``status: draft`` node.
        """
        guard = _load_guard()
        monkeypatch.setattr(guard, "_AKMS_AVAILABLE", False)
        monkeypatch.setattr(guard, "_GlobalNodeFrontmatter", None)

        planted = tmp_path / "established"
        (planted / "domain").mkdir(parents=True)
        (planted / "domain" / "bad.md").write_text(
            "---\nid: bad\nstatus: draft\n---\n\n# bad\n", encoding="utf-8"
        )
        monkeypatch.setattr(guard, "_established_root_default", lambda _repo: planted)

        assert guard.main(["--allow-missing-akms"]) != 0, (
            "a status:draft node must still be an offender under status-only mode"
        )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "script_name",
        ["build_sidecar_macos.sh", "build_sidecar_linux.sh", "build_sidecar_windows.ps1"],
    )
    def test_build_scripts_provision_akms_for_guard(self, script_name):
        """Because the guard FAILS CLOSED, every sidecar
        build script that invokes it must PROVISION akms (so the full schema
        compile runs) and fall back to --allow-missing-akms only for a genuine
        core-only checkout. Otherwise fail-closed would break legitimate builds.

        Static assertion so a future revert to a bare `uv run python guard`
        (which would fail closed on an unprovisioned env) is caught.
        """
        script = _REPO_ROOT / "scripts" / script_name
        text = script.read_text(encoding="utf-8")
        assert "check_resources_established_only.py" in text, (
            f"{script_name} no longer invokes the guard?"
        )
        # The full-bundle branch provisions the extra so the schema compile runs.
        assert "--package logic-loom-api --extra akms" in text, (
            f"{script_name} must run the guard with the akms extra provisioned "
            "so the fail-closed guard can run the full GlobalNodeFrontmatter compile"
        )
        # Core-only branch explicitly opts out (status-only) rather than wedging.
        assert "--allow-missing-akms" in text, (
            f"{script_name} must pass --allow-missing-akms on the core-only path"
        )

    @pytest.mark.unit
    def test_akms_present_path_unchanged(self):
        """Sanity: with akms importable (dev env) the guard certifies the real
        tree exactly as before — the fix does not regress the happy path."""
        guard = _load_guard()
        if not guard._AKMS_AVAILABLE:
            pytest.skip("akms not installed in this env; happy-path covered elsewhere")
        # Real repo established tree is clean (or absent) → exit 0.
        assert guard.main([]) == 0
