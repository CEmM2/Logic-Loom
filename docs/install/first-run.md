# First run

## 1. Install the tools Logic-Loom drives

Logic-Loom calls these tools but never installs or signs in to them. A missing tool shows
up as a clear error in the run dialog or in a preflight check.

| Tool | Needed for | Sign-in |
| --- | --- | --- |
| Zotero + Better BibTeX | Every zsum flow (cards, digests, publish) | Local |
| An LLM backend | Cards, digests, node generation, Learn modes that need an LLM | See [LLM providers](llm-providers.md) |
| Claude Code CLI (`claude`) | Claude-backed AKMS node generation | `claude` login |
| `nlm` CLI | NotebookLM-backed generation | Browser sign-in to Google |

## 2. Point Logic-Loom at your vault

On first launch, a setup dialog asks for the one required setting: the **vault path**,
the folder zotero-summarizer publishes your Obsidian vault into. It also lets you pick
the vault search backend (`ripgrep`, the default, or `qmd`).

The other fields are optional:

- **AKMS graph vault**: the source vault the Graph tab compiles. Leave it blank to use
  the default resolution (see [AKMS mode](../guide/akms.md#graph)).
- **AKMS / zotero-summarizer source checkouts**: for developers only. Release builds run
  everything from the bundled packages.

You can change all of these later under **Settings**. They are saved to
`~/.config/logic-loom/config.toml`; see [Configuration](configuration.md).

## 3. Pick an LLM provider

Open **AKMS ▸ Settings** and choose a provider. One choice drives Learn, zsum and node
generation, with optional per-consumer overrides. See [LLM providers](llm-providers.md).

## 4. Check the AKMS workspace

On first start the sidecar creates a workspace for prompts, workflows, batches, runs and
generated nodes:

| OS | Default workspace |
| --- | --- |
| macOS | `~/Library/Application Support/Logic-Loom/AKMS-Workspace` |
| Windows | `%LOCALAPPDATA%\Logic-Loom\AKMS-Workspace` |
| Linux | `~/.local/share/logic-loom/AKMS-Workspace` |

The Assistant keeps its own workspace next to it (`Assistant-Workspace`), unless you set
`assistant_workspace_colocate = true`. The first start also seeds a set of starter
batches into an empty batch store (turn that off with `seed_starter_batches = false`).

Under **AKMS ▸ Settings ▸ Claude Assets** you can install, edit and reset the bundled
agent assets used for node generation. A reset moves your edited files to a backup folder
first.

## Where things live

| Path | What |
| --- | --- |
| `~/.config/logic-loom/config.toml` | Settings |
| `~/.cache/logic-loom/` | Logic-Loom's cache: run history, compiled graph (`graph/graph.json`) |
| `<akms_workspace>/` | Batches, prompt and workflow libraries, runs, generated nodes |
| `<assistant_workspace>/` | Assistant batches, discovery cache, search index (`index/fts.sqlite`) |

The workspaces are plain files. Copying them somewhere else is a complete backup, and
installing a new version over the old one keeps them.

## Reporting a problem

Open an issue on [GitHub](https://github.com/CEmM2/Logic-Loom/issues) with your OS and
version, the Logic-Loom version, your `config.toml` with any keys removed, and what you
expected to happen. Check [Known issues](../known-issues.md) first.
