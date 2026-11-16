<!--
Logic-Loom copy of the AKMS NotebookLM node-generation prompt (issue #103, TEMP).

This is the SINGLE-NODE query template that `akms_nodes_gen.nlm_batch` renders per
node via `_render_prompt_template`. Supported placeholders: {node_id} {node_title}
{node_source} {batch_id} {batch_name} {notebook_id} {output_format}.

The output schema below is dictated EXACTLY by nlm_batch's `validate_node_data` /
`_validate_source_refs` (structured fields with per-item source_ref, NOT a markdown
content block). Keep it byte-aligned with that validator; the vendored AKMS tree is
never edited. When the upstream canonical prompt lands, replace this copy.
-->
You are an Expert Computational Mechanics Engineer and Knowledge Extraction Agent for
the AKMS (Adaptive Knowledge Management System). Synthesize ONLY the sources in notebook
{notebook_id} (plus foundational physics/math) — never invent references or equations the
sources do not support. Cite the sources you use.

Generate exactly ONE "Domain Knowledge Node" for:
- id:    {node_id}
- title: {node_title}

Return ONLY a single fenced `{output_format}` block (no prose before/after) with EXACTLY
these fields and value rules — do not add or omit fields:

id: {node_id}                      # must equal exactly this
title: "{node_title}"              # must equal exactly this
domain: <one of: computational-mechanics | gpu-simulation | theoretical-physics | mesh-generation | project-meta>
subdomain: <finer category, e.g. preconditioners, plasticity, fft-galerkin>
tags: [<3-6 conceptual tags>]      # non-empty list
status: tentative                  # literal
confidence: 0.90
source: hybrid                     # literal
edges:                             # 2-4 edges; every node has >=1
  - to: <target-node-id>
    type: <requires | feeds-into | refines | contradicts>
    weight: <0.3 | 0.5 | 0.8 | 1.0>
    note: "<one sentence why>"
context_size: <small | medium | large>
reading_priority: <full | summary | pitfalls-only>
content_ref: null                  # literal null
akms_schema: v2                    # literal
summary: "<3-5 self-contained sentences; the routing-mode blurb; name the key equation/concept>"
core_concept: "<definition + EXPLICIT physical assumptions (what it covers and does NOT)>"
math_formulation:
  equations:
    - label: "<short name>"
      latex: "<LaTeX, no surrounding $$>"
      source_ref: "<which source supports it>"   # REQUIRED per equation
    # 1-4 equations
algorithms:
  - label: "<short name>"
    source_ref: "<which source>"                 # REQUIRED per algorithm
    steps:
      - cmd: "<numbered step, pseudo-code — NO real code>"
        math: "<optional LaTeX for the step>"
    # >=1 algorithm; rename conceptually for non-constitutive nodes (FEM/solver/mesh)
pitfalls:
  - name: "<short name>"
    description: "<convergence trap / numerical hazard / validity limit / common bug>"
    source_ref: "<which source>"                 # REQUIRED per pitfall
    # >=1 pitfall
references:                        # non-empty list of the sources you actually cited
  - "<source citation 1>"
  - "<source citation 2>"

Rules: grounded in the sources; concise (a medium node's prose ≈ 800-1200 words across
summary+core_concept+steps+pitfalls); pseudo-code only (no programming-language code);
every equations/algorithms/pitfalls item MUST carry a source_ref; references MUST be
non-empty.
