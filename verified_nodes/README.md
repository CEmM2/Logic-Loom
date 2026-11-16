# Curated Established Node Library — `verified_nodes/`

This directory is the **human-curated source tree** for Tier A (established)
AKMS knowledge nodes.  It is the authoritative source from which
`scripts/sync_akms_nodes.py` (P3-2) copies only `status: established` nodes
into the shippable `resources/akms_nodes/established/` tree.  The guard
`scripts/check_resources_established_only.py` (P3-3) then asserts that every
node that reaches the shipped tree carries `status: established`.

This tree is:
- **Separate** from any AKMS `Nodes_Vault/` (Non-Goal #1 of the
  bundling plan).  Nodes_Vault is the upstream working vault; this tree holds
  only nodes that have passed human review.
- **Not** automatically shipped — nodes must have `status: established` and be
  synced by P3-2 before they reach the binary.

---

## Directory layout

```
verified_nodes/
├── README.md              ← this file (the contract)
├── PROVENANCE.toml        ← ship-provenance sidecar, keyed by node id
├── <domain>/
│   └── <node-id>.md       ← one file per established node
```

Nodes are grouped by `domain` sub-directory for readability, but the sync
script operates on `**/*.md` so the nesting depth does not matter.  `README.md`
and `PROVENANCE.toml` are NOT nodes and are excluded from node collection.

---

## The contract

An established node in this tree is a **valid AKMS v2 node** that:

1. **Passes `akms.schema.validators` (`GlobalNodeFrontmatter`).**  The node
   frontmatter is validated by the same parser the graph compiler uses
   (`graph_adapter.scan_graph_sources` → `parse_node_frontmatter_from_dict`).
   This is what "superset of what the graph compiler reads" means concretely:
   there is exactly one schema, and these nodes must satisfy it.
2. **Carries `status: established`.**  This is the Tier-A ship gate that P3-2's
   sync filter and P3-3's build guard both key on.

> **Important — the frontmatter schema forbids extra keys.**
> `GlobalNodeFrontmatter` has `model_config extra="forbid"`.  You **cannot**
> add ship-provenance fields (`verified_by`, `verified_at`, `version`,
> `source_ref`) to a node's YAML frontmatter — the validator rejects them.
> Ship-provenance lives in `PROVENANCE.toml` instead (see below).

### AKMS v2 node frontmatter (validated schema)

Required keys (enforced by `GlobalNodeFrontmatter`):

| Key | Type | Constraint |
|-----|------|-----------|
| `id` | string | unique node identifier; also the `PROVENANCE.toml` key |
| `title` | string | human-readable name |
| `domain` | string | knowledge domain |
| `tags` | list[string] | searchable tags |
| `status` | enum | **must be `established`** for this tree (enum: `draft`, `tentative`, `established`, `deprecated`) |
| `confidence` | float | epistemic confidence, 0–1 |
| `source` | enum | AKMS origin enum: `human`, `agent`, `hybrid`, `generated` (NOT a free-text citation) |

Optional keys (also validated when present):

| Key | Type | Notes |
|-----|------|-------|
| `subdomain` | string | finer-grained grouping |
| `confidence_floor` | float | minimum confidence |
| `edges` | list[StructuralEdge] | each edge needs `to`, `type`, `weight` (optional `note`) |
| `context_size` | enum | `small`, `medium`, `large` |
| `reading_priority` | enum | `full`, `summary`, `pitfalls-only` |
| `content_ref` | string/null | external content reference |
| `akms_schema` | string | schema marker; defaults to `v2` |

**Edge `type` enum** (`EdgeType`): `requires`, `feeds-into`, `refines`,
`contradicts`, `pitfall`, `implements`.  Every edge also requires a numeric
`weight`.

### Ship-provenance sidecar — `PROVENANCE.toml`

Because the node frontmatter cannot carry provenance, it lives in
`PROVENANCE.toml`, a TOML file keyed by the node's `id`.  Every node MUST have
a matching entry.  Each entry requires:

| Key | Type | Purpose |
|-----|------|---------|
| `verified_by` | string | who approved this node |
| `verified_at` | string | ISO-8601 date of review/approval |
| `version` | string | content/schema version; bump on material change |
| `source_ref` | string | human-readable citation (the free-text provenance the AKMS `source` enum cannot express) |

Example entry:

```toml
[elastic-isotropic]
verified_by = "human-expert"
verified_at = "2026-07-01"
version = "1.0"
source_ref = "Timoshenko & Goodier (1951), Theory of Elasticity, 3rd ed."
```

Downstream: P4-2 surfaces this provenance/version on API responses; P5-1's
promotion tooling stamps it.

### Example node

```yaml
---
id: my-node
title: "My Node Title"
domain: computational-mechanics
subdomain: plasticity
status: established
source: human
tags:
  - plasticity
confidence: 0.9
akms_schema: v2
edges:
  - to: elastic-isotropic
    type: requires
    weight: 0.8
context_size: medium
reading_priority: full
---
```

with a matching `PROVENANCE.toml` entry under `[my-node]`.

---

## Authoring workflow

1. Draft the node as a standard AKMS v2 markdown file with `status: tentative`.
2. After human review, flip `status` to `established` and confirm the node
   validates via `akms.schema.validators` (`GlobalNodeFrontmatter`).
3. Add/update the node's entry in `PROVENANCE.toml`
   (`verified_by`, `verified_at`, `version`, `source_ref`).
4. Place the file under the matching domain sub-directory.
5. Run `scripts/sync_akms_nodes.py` (P3-2) to copy it into the resources tree.
6. CI runs `scripts/check_resources_established_only.py` (P3-3) to assert the
   shipped tree contains only established nodes.

---

## What does NOT belong here

- Nodes still under review (`status: tentative` or `status: draft`).
- Upstream `Nodes_Vault` content — copy and promote individually.
- Generated or experimental nodes that have not been human-verified.
