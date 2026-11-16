# Meet The Logic-Loom

![banner image](./assets/banner.png)

<p align="center"><em>It gathers the scrolls from the shelves in the hall, it thinks up the links that connect them all!<br>It takes a messy math-scribble and turns it to speed, then watches the agents to ensure they succeed.</em></p>

Logic-Loom is a desktop app for research workflows. It turns a Zotero library into a
searchable Obsidian vault with [zotero-summarizer](https://github.com/CEmM2/zotero-summarizer),
and adds optional knowledge-graph tooling from [AKMS](https://cemm2.github.io/AKMS/), paper
discovery, a computational-mechanics compiler from [MechDSL](https://cemm2.github.io/MechDSL/),
and read-only views of agent memory.

**Documentation: <https://cemm2.github.io/Logic-Loom/>**

## Six modes

| Mode | What it is for |
| --- | --- |
| **zsum** | Run the zotero-summarizer pipeline and browse the published vault |
| **AKMS** | Batch node generation, node review, the read-only graph explorer, Learn |
| **Assistant** | Paper discovery, source batches, workflow runs, Cmd-K workspace search |
| **MechDSL** | Compile `% mechanics` LaTeX into FEM solver code |
| **Failure Memory** | Inspect `.failure-memory` lesson registries, with an opt-in operator mode |
| **Records** | Browse akms-records stores and their record graph |

Each mode is described in the [user guide](https://cemm2.github.io/Logic-Loom/guide/zsum.html).

## Install

Download the installer for your platform from the
[Releases page](https://github.com/CEmM2/Logic-Loom/releases/latest), then follow
[Install](https://cemm2.github.io/Logic-Loom/install/index.html) and
[First run](https://cemm2.github.io/Logic-Loom/install/first-run.html).

## Run from source

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 20+, pnpm 11 and Rust stable.

```bash
uv sync                  # core (zsum, Assistant); add --all-packages --all-extras for every mode
pnpm install
pnpm logic-loom:dev      # starts the sidecar, Vite and the Tauri shell together
```

Tests:

```bash
uv run pytest services/logic_loom_api/tests -q
pnpm --filter logic-loom-desktop test
```

[Developer setup](https://cemm2.github.io/Logic-Loom/developers/index.html),
[Architecture](https://cemm2.github.io/Logic-Loom/developers/architecture.html) and
[Build from source](https://cemm2.github.io/Logic-Loom/developers/build-from-source.html)
have the details.

## Layout

```text
apps/desktop/             # Tauri app (Rust shell + React frontend)
services/logic_loom_api/  # Python FastAPI sidecar
packaging/                # sidecar freezer entrypoints
scripts/                  # build, release and docs helpers
docs/                     # the documentation site (MkDocs)
```

## Documentation

The site is built from `docs/` with MkDocs Material and published on every release tag.

```bash
uv sync --all-packages --all-extras --group docs
pnpm docs:serve          # live preview
pnpm docs:build          # regenerate the reference pages and build strictly
```

## License

MIT
