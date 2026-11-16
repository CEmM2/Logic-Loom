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
[Logic-Loom Releases page](https://github.com/CEmM2/Logic-Loom/releases/latest). Each
release attaches the platform installers and a `SHA256SUMS.txt` manifest.

!!! note "Older releases"
    Releases up to 0.3.0 were published from
    [CEmM2/Logic-Loom-releases](https://github.com/CEmM2/Logic-Loom-releases/releases).
    That repository is retired after 0.3.0.

Check a download against the manifest before installing:

```bash
shasum -a 256 -c SHA256SUMS.txt --ignore-missing
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

Use the `.AppImage` or the `.deb`.

```bash
chmod +x Logic-Loom-*.AppImage
./Logic-Loom-*.AppImage
# or
sudo apt install ./Logic-Loom-*.deb
```

On Ubuntu 22.04 and later, an AppImage may need `libfuse2`
(`sudo apt install libfuse2`).

## Next

Continue with [First run](first-run.md).
