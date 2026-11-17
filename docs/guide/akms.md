# AKMS mode

AKMS mode connects Logic-Loom to [AKMS](https://cemm2.github.io/AKMS/), the Adaptive
Knowledge Management System: a graph of reusable knowledge nodes (concepts, derivations,
pitfalls) built from your sources. Logic-Loom uses it to group papers into batches,
generate and review nodes, explore the graph and compile learning material.

It needs the `akms` extra, which release builds include. No AKMS source checkout is
needed; everything runs from the installed packages and your AKMS workspace.

## Tabs

| Tab | What it does |
| --- | --- |
| Overview | Readiness of each AKMS feature, with setup hints |
| Batches | Your batch definitions, plus starter batches seeded on first run |
| Picker | Assign papers to batches and NotebookLM notebooks, then generate and validate nodes. Lists every batch from the Batches tab and picks up changes without a restart |
| Nodes | Browse, review and promote generated nodes; established nodes are badged separately from drafts. **Open in Editor** uses the editor command from zsum ▸ Settings ▸ Nodes Review, or your system's default app when none is set, and says which it used |
| Graph | Read-only graph explorer (below) |
| Learn | Compile graph slices into learning material |
| Prompts | The prompt library: bundled defaults, your variants and active profiles |
| Workflows | The workflow library: bundled and your own workflow definitions |
| Runs | Every node-generation and workflow run, with outputs and metadata |
| Settings | The LLM provider chooser, workspace location and Claude assets |

## Node generation

From the Picker, three workflows run as background jobs with streamed logs:

- **Generate (Claude)**: through the Claude Code CLI, optionally grounded in NotebookLM
  through `nlm_mcp_url`.
- **Generate (NLM)**: directly through NotebookLM.
- **Validate nodes**: checks generated nodes against their source notebooks.

Each run records the exact prompts, workflow and inputs it used under
`<akms_workspace>/runs/<run_id>/`, so it can be compared or rerun.

## Curated library and starter batches

Release builds ship a read-only library of **established** nodes, shown in the graph next
to your own with their provenance. You can copy an established node into your vault to
edit it, or report an error in one. Starter batches are a separate, editable seed pack:
they are yours to run and change, and never mixed into the established library.

## Graph

The Graph tab is read-only. It compiles the AKMS graph into Logic-Loom's cache
(`~/.cache/logic-loom/graph/graph.json`) and never writes to your source vault.

- **Refresh** compiles a fresh graph.
- **Filtered view** narrows by text, domain, status, tag and node origin.
- **Ego view** centres on one node with a chosen depth and direction.
- **Cluster view** slices by tag or domain.
- The **inspector** shows a node's metadata and edges, with links to open it in **Nodes**
  or your editor, or send the selection to **Learn**.

The graph source vault is resolved in this order:

1. `akms_global_vault`, if set
2. `<akms_repo>/Packages/Nodes_Vault`, only when a developer checkout is configured
3. the `AKMS_GLOBAL_VAULT` environment variable
4. `~/.claude/akms/nodes`

If none of them exists, the Graph tab shows a setup message.

## Learn

Learn compiles a slice of the graph into a **Learning Source Packet**: a reproducible
artifact with a reading order, per-node sections, pitfalls, assessment items and code
links, which it then exports in the format you pick. The available modes and exporters
come from the installed `akms-learn`; modes that need an LLM are disabled until a provider
is configured. The History list under the form keeps your latest 50 compiles on this
computer, across restarts. See the [akms-learn docs](https://cemm2.github.io/AKMS/reference/akms-learn/index.html).

## More on AKMS

The [AKMS documentation](https://cemm2.github.io/AKMS/) covers the graph model, node
format, the batch picker and the node-generation tools in depth.
