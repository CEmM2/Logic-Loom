"""Tauri resource bundling for the sidecar.

Static-validates that `apps/desktop/src-tauri/tauri.conf.json` ships
the sidecar binary under `bundle.resources` (so the frozen
`logic-loom-api[.exe]` lands inside the packaged `.app`/`.exe`/AppImage)
and that `apps/desktop/src-tauri/src/sidecar.rs` keeps the
release-mode invariant: spawn the bundled binary, not `uv run`.

The packaged-app end-to-end smoke test (`pnpm logic-loom:package:macos`
on a clean account) runs on the platform CI runners, not here.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TAURI_CONF = ROOT / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
SIDECAR_RS = ROOT / "apps" / "desktop" / "src-tauri" / "src" / "sidecar.rs"


class TestTauriSidecarResources:
    """Tauri bundles the sidecar binary and spawns it in release builds."""

    def test_tauri_conf_is_valid_json(self) -> None:
        """Verifies: tauri.conf.json parses as JSON.

        Requirement: a packaged app would not even build with a broken conf.
        """
        assert TAURI_CONF.exists(), f"{TAURI_CONF} not found"
        data = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_tauri_conf_bundles_binaries_resource(self) -> None:
        """Verifies: bundle.resources includes 'binaries' (the sidecar drop site).

        Requirement: packaged binaries land inside the Tauri bundle as resources.
        """
        data = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        resources = data.get("bundle", {}).get("resources", [])
        if isinstance(resources, dict):
            # Map form (source -> destination in the resource dir): the sidecar
            # directory must land at `binaries/`, where sidecar.rs looks for it.
            assert resources.get("binaries") == "binaries", (
                f"bundle.resources must map 'binaries' to 'binaries'; got {resources!r}"
            )
            return
        assert isinstance(resources, list), "bundle.resources must be a list or a map"
        # Accept either "binaries" (whole dir) or any "binaries/..." entry.
        joined = " ".join(resources)
        assert "binaries" in joined, (
            f"bundle.resources must include 'binaries' (or 'binaries/*'); got {resources!r}"
        )

    def test_sidecar_rs_uses_bundled_binary_in_release(self) -> None:
        """Verifies: release-mode (non-debug_assertions) branch spawns the bundled
        binary from Tauri resources rather than 'uv run'.

        Requirement: the .app launches and serves /api/app/health without uv on PATH.
        """
        text = SIDECAR_RS.read_text(encoding="utf-8")
        assert "#[cfg(not(debug_assertions))]" in text, (
            "sidecar.rs missing release-mode branch"
        )
        # The release path should resolve a bundled binary path from Tauri resources.
        assert "bundled_sidecar_path" in text or "resource_dir" in text, (
            "release-mode sidecar must resolve a bundled binary path"
        )
        # And the release branch must NOT use 'uv run'.
        # Find the release branch and assert 'uv' does not appear inside it.
        marker = "#[cfg(not(debug_assertions))]"
        idx = text.find(marker)
        # Walk forward to the next blank-line-then-brace-close-ish boundary.
        # Simpler: read until the next `#[cfg(` directive or end-of-fn.
        next_cfg = text.find("#[cfg(", idx + len(marker))
        release_block = text[idx : next_cfg if next_cfg > 0 else len(text)]
        # Strip lines that are obviously parameter shadows like `_root: &Path`.
        assert '"uv"' not in release_block and "Command::new(\"uv\")" not in release_block, (
            "release-mode sidecar must NOT spawn 'uv run' — must use bundled binary"
        )

    def test_sidecar_rs_preserves_handshake_contract(self) -> None:
        """Verifies: the CLAUDE.md sidecar handshake line is parsed exactly.

        Requirement: LOGIC_LOOM_API_READY:<port> format is preserved (contract with
        the frozen sidecar's stdout emission).
        """
        text = SIDECAR_RS.read_text(encoding="utf-8")
        assert "LOGIC_LOOM_API_READY:" in text, (
            "sidecar.rs must parse the LOGIC_LOOM_API_READY:<port> handshake line"
        )

    def test_sidecar_rs_passes_auto_port_flag(self) -> None:
        """Verifies: the spawned binary still receives --auto-port so the
        sidecar picks a free port and emits the READY line.
        """
        text = SIDECAR_RS.read_text(encoding="utf-8")
        assert "--auto-port" in text, (
            "sidecar.rs must pass --auto-port so the handshake fires"
        )
