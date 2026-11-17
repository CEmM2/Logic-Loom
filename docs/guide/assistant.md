# Assistant mode

Assistant mode helps you find papers, collect them into source batches, run research
workflows over them and search everything Logic-Loom has produced. It needs only the core
install.

## Tabs

| Tab | What it does |
| --- | --- |
| Overview | Start points for the tasks below |
| Discover | Search OpenAlex and Semantic Scholar and see which results are already in your Zotero library |
| Batches | Source batches: named sets of papers and notes to run workflows over; a batch can also be sent to AKMS |
| Workflows | Research workflows such as a literature survey, related-work section, method comparison or annotated bibliography |
| Outputs | Browse, compare and rerun workflow outputs |
| Prompts | The prompts those workflows use |
| Runs | Every Assistant run, with its inputs and metadata |
| Settings | Discovery and workspace settings |

## Discover and related papers

Discover searches both services and marks each result that matches your Zotero catalog.
From a paper you can explore related work in five ways: citations, references, similar
papers, co-citation and co-reference, each with ranking presets. To start from the
papers already in a batch, open the batch's **Related** tab (the Overview's "Find related
papers" card takes you there). A batch's **Runs** tab lists the workflow runs made from it.

Set `openalex_user_email` to use the OpenAlex polite pool (required for OpenAlex
requests). API keys for OpenAlex and Semantic Scholar are optional and raise rate limits.
Results are cached in the Assistant workspace.

## Workspace search

Press **⌘K** (macOS) or **Ctrl-K** (Windows, Linux) on any Assistant page. One search box
covers:

- Zotero sources in your batches
- discovered abstracts
- zsum paper cards and collection digests
- AKMS nodes
- workflow outputs and their run metadata

The index updates when you change a batch. Outputs from new runs and large imports are
picked up on the next rebuild: use **Rebuild index** in the search box when results look
stale or empty.

## Where it stores data

The Assistant workspace (by default `Assistant-Workspace` next to the AKMS workspace)
holds `batches.json`, the discovery cache and the search index at `index/fts.sqlite`.
