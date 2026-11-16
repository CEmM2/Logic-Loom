# Known issues

Caveats that apply across releases. Anything specific to one version is in that
release's notes on the [Releases page](https://github.com/CEmM2/Logic-Loom/releases).

## Install warnings

- **macOS Gatekeeper:** builds that are not notarized need the right-click ▸ **Open**
  route on first launch. See [Install](install/index.md#macos).
- **Windows SmartScreen:** unsigned builds show a trust warning until code signing is in
  place.

## Linux

- **glibc 2.35 or newer** (Ubuntu 22.04, Debian 12 or later) is required by the Linux
  packages. The v0.3.0 Linux packages needed glibc 2.43 by mistake; use v0.3.1 or later.
- **The `.deb` needs the distribution's WebKitGTK.** If it cannot be installed, use the
  AppImage, which bundles it, or
  [run from source in a browser](developers/index.md#run-in-a-browser-without-webkitgtk).
- **Blank window on some GPU drivers:** if the window opens empty, start the app with
  `WEBKIT_DISABLE_DMABUF_RENDERER=1`.

## External tools stay external

Logic-Loom does not install or sign in to Zotero, Better BibTeX, your PDFs or any LLM
CLI or service. If one is missing or misconfigured, the run that needs it fails with a
message naming it.

NotebookLM features need you to be signed in to Google in the environment the `nlm` CLI
uses.

## Gated features

Some tabs stay gated until their requirements are met, and show a setup message instead:

- **AKMS Graph** needs a readable source vault (see [AKMS mode](guide/akms.md#graph)).
- **Learn** modes that need an LLM are disabled until a provider is configured.
- **Failure Memory** and **Records** show nothing until you list projects or stores in
  `config.toml`.
- **MechDSL verify** needs Taichi, which is not bundled; the app offers to install it.

## Some flows are CLI-only

The zotero-summarizer enrichment commands (`zsum citations`, `zsum reviews`,
`zsum restructure`) and the step-by-step NotebookLM pipeline (`zsum nlm ...`) are
available from the bundled CLI but do not have buttons in the Pipeline tab yet. See
[zsum mode](guide/zsum.md#commands-outside-the-ui).

## Workspace search can lag

Outputs from new workflow runs and large imports appear in workspace search after the
next index rebuild. Use **Rebuild index** in the search box.

## Platform coverage

Not every release ships every platform. Check the assets attached to the release you
want.
