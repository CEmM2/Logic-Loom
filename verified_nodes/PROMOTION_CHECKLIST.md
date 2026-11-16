# Promotion Checklist — Draft (Tier B) → Established (Tier A)

This checklist defines what **`status: established`** means *in practice* for the
curated node library in `verified_nodes/`. It is the **human-review gate** in the
promotion path:

> generated draft (Tier B) → NLM-validated → **human-reviewed (this checklist)** →
> `status: established` → eligible to ship (Tier A).

Tooling (`scripts/promote_node.py`) mechanises the *stamping* — it flips
`status: established` and records provenance in `PROVENANCE.toml`. It does **not**
replace review. The build guard (`scripts/check_resources_established_only.py`,
P3-3) is the mechanical *backstop*: a promotion that misses required frontmatter or
fails the AKMS schema compile fails the build. Neither the tool nor the guard can
judge whether the *content* is correct — that is what this checklist is for.

A reviewer must be able to answer **yes** to every item below before promoting.

---

## 1. Content correctness

- [ ] The **summary** accurately states what the node is about; no placeholder
      text (`TBD`, `[PLACEHOLDER …]`, `lorem ipsum`) remains.
- [ ] The **core concept / equations / algorithm** are technically correct and
      match the cited sources.
- [ ] **Known pitfalls** are real, specific, and actionable (not generic filler).
- [ ] **References** are real, locatable, and support the claims made.
- [ ] The node is self-contained enough to be useful without the notebook or
      paper it was extracted from.

## 2. Frontmatter (validated by `GlobalNodeFrontmatter`)

- [ ] `id` is unique, stable, and slug-shaped (it is also the `PROVENANCE.toml`
      key and the destination filename).
- [ ] `title`, `domain`, `tags`, `confidence`, `source` are all present and
      accurate. `source` is the AKMS **enum** (`human`/`agent`/`hybrid`/
      `generated`), *not* a free-text citation.
- [ ] `confidence` honestly reflects epistemic certainty (0–1).
- [ ] Any `edges` point at nodes that exist (or are being promoted together),
      use a valid `type` (`requires`, `feeds-into`, `refines`, `contradicts`,
      `pitfall`, `implements`), and carry a numeric `weight`.
- [ ] **No provenance keys in frontmatter.** `verified_by`, `verified_at`,
      `version`, `source_ref` (and `provenance`) must **not** appear here —
      `GlobalNodeFrontmatter` is `extra="forbid"` and will reject them. They
      belong in `PROVENANCE.toml`. (`promote_node.py` refuses a node that
      carries them in frontmatter.)

## 3. Provenance (recorded in `PROVENANCE.toml`)

- [ ] `verified_by` — the reviewer's handle / identity (who approved it).
- [ ] `verified_at` — ISO-8601 date (`YYYY-MM-DD`) of this review.
- [ ] `version` — content/schema version; bump on any material content change.
- [ ] `source_ref` — free-text citation for the content (the human-readable
      provenance the AKMS `source` enum cannot express).

All four are **required and non-empty**; `promote_node.py` refuses an
incomplete promotion.

## 4. Placement & no clobber

- [ ] The node is being placed under the correct `<domain>/` sub-directory.
- [ ] Promotion is **not** silently overwriting an existing curated node
      (`promote_node.py` is non-destructive and refuses a clobber; deliberate
      re-promotion means removing/renaming the old node first, with intent).

## 5. Post-promotion verification

- [ ] `uv run python scripts/sync_akms_nodes.py` copies the node into the
      shipped resources tree.
- [ ] `uv run python scripts/check_resources_established_only.py` (P3-3 guard)
      passes — every shipped node carries `status: established` and compiles
      against `GlobalNodeFrontmatter`.

---

## How to promote

Once every box above is checked, stamp and file the node with the promotion tool:

```bash
uv run python scripts/promote_node.py \
    path/to/reviewed-draft.md \
    --domain computational-mechanics \
    --verified-by human-expert \
    --verified-at 2026-07-01 \
    --version 1.0 \
    --source-ref "Timoshenko & Goodier (1951), Theory of Elasticity, 3rd ed."
```

This stamps `status: established`, writes the `PROVENANCE.toml` entry keyed by
the node's `id`, and places the file at `verified_nodes/<domain>/<id>.md`.

`uv run` is required — `promote_node.py` imports `python-frontmatter` (and, when
available, `akms`), which are managed dependencies and not on the bare system
interpreter.

> **Human review remains the gate.** The checklist plus the P3-3 guard *reduce*
> but do not *eliminate* the risk of a bad node shipping — a reviewer who stamps
> without reading can still promote a wrong node. Treat promotion as a
> deliberate, accountable act.
