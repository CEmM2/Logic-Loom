# Install

These steps are for release builds. To run from source, see
[Developer setup](../developers/index.md).

## Before you install

Logic-Loom works on top of your own research setup. It does not install or sign in to
any of these for you:

- [Zotero](https://www.zotero.org) with your library
- the [Better BibTeX](https://retorque.re/zotero-better-bibtex/) plug-in, with an
  auto-updating JSON export
- your PDFs, reachable from Zotero
- an LLM backend: a local model (Ollama, LM Studio), an agent CLI (Claude Code, Codex,
  Gemini), NotebookLM through the `nlm` CLI, or an API key. See
  [LLM providers](llm-providers.md).

## Download

Get the latest installer from the
[installer releases page](https://github.com/CEmM2/Logic-Loom-releases/releases/latest).
Each release attaches the platform installers and a `SHA256SUMS.txt` manifest.

!!! note "Two release pages"
    Installers are built and published from
    [CEmM2/Logic-Loom-releases](https://github.com/CEmM2/Logic-Loom-releases/releases).
    The [source repository's releases](https://github.com/CEmM2/Logic-Loom/releases)
    carry only the source archives for the same version.

Check a download against the manifest before installing. On Linux:

```bash
sha256sum -c SHA256SUMS.txt --ignore-missing
```

On macOS:

```bash
shasum -a 256 -c SHA256SUMS.txt --ignore-missing
```

On Windows (PowerShell), compare the printed hash with the matching line in
`SHA256SUMS.txt`:

```powershell
Get-FileHash .\Logic-Loom_<version>_x64-setup.exe -Algorithm SHA256
```

Not every platform is guaranteed on every release. Check the assets attached to the
release you want. If yours is missing, you can
[build from source](../developers/build-from-source.md).

## macOS

1. Download the `.dmg`.
2. Open it and drag `Logic-Loom.app` into **Applications**.
3. Launch it from **Applications**.

If the build is not notarized, macOS may block the first launch. Right-click
`Logic-Loom.app`, choose **Open**, then confirm **Open**. If Gatekeeper still refuses,
remove the quarantine flag:

```bash
xattr -dr com.apple.quarantine /Applications/Logic-Loom.app
```

## Windows

1. Download the NSIS `-setup.exe` installer (or the `.msi`).
2. Run it.
3. If SmartScreen warns about an unsigned app, choose **More info**, then
   **Run anyway**, but only for a download whose checksum you verified.

## Linux

Linux builds are x86_64 and need glibc 2.35 or newer: Ubuntu 22.04 or later, Debian 12
or later, or another distribution of the same age. Check yours with `ldd --version`.

Use the `.AppImage` or the `.deb`.

```bash
chmod +x Logic-Loom-*.AppImage
./Logic-Loom-*.AppImage
# or
sudo apt install ./Logic-Loom-*.deb
```

The two differ in what they need from your system:

- The **AppImage** carries its own WebKitGTK and GTK libraries, so it needs nothing from
  your package manager except, on some systems, `libfuse2` to mount itself
  (`sudo apt install libfuse2`, or run it with `--appimage-extract-and-run`).
- The **`.deb`** uses your distribution's WebKitGTK (`libwebkit2gtk-4.1-0`), which `apt`
  installs with it. If you cannot install that package, for example because your
  package mirror is unreachable, use the AppImage.

!!! warning "v0.3.0 Linux builds"
    The v0.3.0 Linux packages were built against glibc 2.43 by mistake and do not start
    on Ubuntu 24.04, Debian 13 or older. Use v0.3.1 or later, or
    [run from source](../developers/index.md#run-in-a-browser-without-webkitgtk).

If no release build runs on your machine, you can
[run Logic-Loom from source in a browser](../developers/index.md#run-in-a-browser-without-webkitgtk),
which needs neither the installer nor WebKitGTK.

## Next

Continue with [First run](first-run.md).
