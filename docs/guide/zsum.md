# zsum mode

zsum mode runs the [zotero-summarizer](https://github.com/CEmM2/zotero-summarizer)
pipeline: it reads your Better BibTeX export, extracts text from your PDFs, writes a
structured card per paper and a digest per collection, and publishes them as an
interlinked Obsidian vault.

It needs only the core install and a `vault_path`.

## Tabs

| Tab | What it does |
| --- | --- |
| Overview | Pipeline status at a glance |
| Pipeline | Run stages (extract, skeleton, cards, digests, publish, all, stats, clear-cache) with live log streaming |
| Vault | Read and search the published vault, with wikilink resolution and LaTeX math; dot-folders such as `.github` are hidden |
| Collections | Browse the collection hierarchy with digest state and per-paper card state |
| Prompts | Edit the prompts the pipeline uses; your edits are kept separately from the bundled defaults |
| Runs | Inspect and rerun past pipeline runs |
| Settings | zsum options and the zsum LLM provider override. If the zsum config file doesn't exist yet, Settings offers to create a starter one |

## Extraction modes

Set `extraction.mode` in your zsum config:

| Mode | Behaviour |
| --- | --- |
| `pages` (default) | First N and last M pages of each PDF |
| `sections` | Table-of-contents and heading-detected sections; falls back to page ranges when too few are found |
| `notebooklm` | Hands card and digest generation to NotebookLM; papers it fails on fall back to page ranges |

## Commands outside the UI

The bundled zotero-summarizer also has commands that are not buttons yet. They augment an
existing vault without a full rerun:

| Command | What it does |
| --- | --- |
| `zsum citations` | Citation counts from OpenAlex, with Semantic Scholar as fallback, cached for 30 days by default |
| `zsum reviews` | Marks review, survey, meta-analysis and tutorial papers |
| `zsum restructure` | Suggests renames, splits and merges for your collection hierarchy; never moves papers |
| `zsum nlm upload\|cards\|digests\|all\|status\|cleanup` | The NotebookLM pipeline, step by step |
| `zsum setup-model --size ...` | Configures a local Ollama model |

## What you get

The published vault has a library index, a note per collection with its digest, a note
per paper with its structured summary, and wikilinks between them.

## Search backends

Vault search uses `ripgrep` by default. Set `search_backend = "qmd"` and
`vault_qmd_collection` to use a QMD collection instead.
