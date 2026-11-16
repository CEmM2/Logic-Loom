# Build from source

A release build has two parts: the Python sidecar frozen with PyInstaller
(`logic-loom-api`, or `logic-loom-api.exe` on Windows), and the Tauri desktop package
that bundles it as a resource. Build each platform on that platform; cross-compiling this
stack is not supported.

| Platform | Sidecar | Desktop packages |
| --- | --- | --- |
| macOS | `logic-loom-api` | `.app`, `.dmg` |
| Linux (Ubuntu/Debian) | `logic-loom-api` | `.AppImage`, `.deb` |
| Windows 11 | `logic-loom-api.exe` | NSIS `-setup.exe`, `.msi` |

## Prepare the machine

All platforms need Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 20 or later,
pnpm 11 (`corepack enable && corepack install --global pnpm@11.0.0`) and Rust stable.

=== "Ubuntu / Debian"

    ```bash
    sudo apt update
    sudo apt install -y build-essential curl wget git pkg-config libssl-dev \
      libgtk-3-dev libwebkit2gtk-4.1-dev libayatana-appindicator3-dev \
      librsvg2-dev patchelf libfuse2 file ca-certificates
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

    On Ubuntu 22.04, Python 3.12 may need to come from the deadsnakes PPA.

    A Linux build only runs on systems whose glibc is at least as new as the build
    machine's. Build release packages on the oldest distribution you want to support;
    the official builds use Ubuntu 22.04 (glibc 2.35) and check that nothing they ship
    needs a newer glibc. A package built on a current distribution will not start on
    older ones.

=== "Windows 11"

    Use native PowerShell, not WSL.

    ```powershell
    winget install --id Git.Git -e
    winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
    winget install --id Rustlang.Rustup -e
    winget install --id OpenJS.NodeJS.LTS -e
    winget install --id Python.Python.3.12 -e
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```

    Restart after installing the Visual Studio Build Tools. If `cl` is not found, build
    from the "x64 Native Tools" prompt. Windows 11 normally has the WebView2 runtime; install
    the Evergreen runtime if it is missing.

=== "macOS"

    ```bash
    xcode-select --install
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

Then, in the repository:

```bash
pnpm install
uv sync
```

## Build

Build the whole app for the current OS (this builds the sidecar first):

```bash
pnpm logic-loom:build
```

Or use the explicit per-OS commands:

```bash
pnpm logic-loom:package:macos
pnpm logic-loom:package:linux
pnpm logic-loom:package:windows     # in PowerShell
```

To build only the sidecar: `pnpm sidecar:build:macos`, `sidecar:build:linux` or
`sidecar:build:windows`. It lands in `apps/desktop/src-tauri/binaries/`.

The sidecar build uses its own `.venv-sidecar-build`, so packages installed in your
development venv do not leak into a release build. It installs exactly the dependency set
`uv.lock` records (AKMS, MechDSL and zotero-summarizer come from PyPI) and bundles every
optional mode: the AKMS tabs and Learn, the MechDSL emit bridge, Failure Memory and the
Records browser. The bundled AKMS agent assets and zsum prompts are read from those
installed packages (`pnpm sync:resources`), so no source checkout of any of them is needed.
Set `LOGIC_LOOM_SIDECAR_CORE_ONLY=1` to build a zsum-only sidecar instead.

After bumping a dependency (`uv lock --upgrade-package <pkg>`, then the matching pin row in
`dev/STATUS.md`), rebuild the sidecar so the frozen binary picks up the new code and assets.

## Smoke-test the sidecar

Before trusting a desktop package, run the frozen sidecar on its own:

```bash
./apps/desktop/src-tauri/binaries/logic-loom-api --port 17321
curl http://127.0.0.1:17321/api/app/health
```

On Windows, run `.\apps\desktop\src-tauri\binaries\logic-loom-api.exe --port 17321`.

## Where the packages land

Under `apps/desktop/src-tauri/target/release/bundle/`:

- macOS: `macos/Logic-Loom.app`, `dmg/*.dmg`
- Linux: `appimage/*.AppImage`, `deb/*.deb`
- Windows: `nsis/*.exe`, `msi/*.msi`

## Checksums

Ship a checksum manifest with every set of artifacts:

```bash
shasum -a 256 Logic-Loom-* > SHA256SUMS.txt
```

## Troubleshooting

- **The AppImage does not start (Linux):** install `libfuse2`. If it reports
  ``version `GLIBC_2.xx' not found``, it was built on a newer distribution than yours;
  see the glibc note under [Prepare the machine](#prepare-the-machine).
- **`link.exe` or `cl` not found (Windows):** the C++ build tools are missing or the shell
  was opened before they were installed. Reboot, or build from the Native Tools prompt.
- **The app opens but says the API did not become ready:** run the sidecar directly as
  above and read its output; the desktop shell only waits for the
  `LOGIC_LOOM_API_READY:<port>` line.
- **zotero-summarizer is required:** the packaged sidecar always bundles it, because the
  core routes import it. A build without zsum would need code changes.
