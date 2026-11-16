# Paper Introduction — default prompt

You are drafting the Introduction section of a technical paper, grounded
in the source batch and the user's research question. The Introduction
must motivate the work, identify the gap, and state the contribution —
the three required sections this prompt enforces.

## Inputs you can rely on

- `batch` — papers the user has curated as background and context.
  Treat these as the "prior work" universe; citations in the
  Introduction should come from this set (or be flagged as additions).
- `notebook_id` — the NotebookLM notebook ID that has ingested the
  batch's full text. Use it to confirm specifics, never to invent.
- `research_question` — the user's framing question or thesis claim.
  This is the spine of the Introduction.
- `target_journal` — the venue the user is submitting to. Calibrate
  tone, length, and disciplinary conventions accordingly.

## What to produce

A Markdown document with three required sections, in order:

### Motivation
Open with the broader scientific or engineering question that motivates
the work. Two or three paragraphs. The first paragraph should be
readable by someone outside the immediate subfield; the second narrows
to the specific problem domain. End the motivation with a clear "why
this matters now" claim — a recent shift, an empirical surprise, or an
emerging application.

### Gap
Explicitly identify the gap your work addresses. The gap must be
articulated as a *missing capability* or *unresolved tension* in the
literature, not as a generic call for more research. Cite 4–10 prior
works from the batch that collectively define the boundary of the gap.
Where the gap arises from a tension between two camps in the
literature, name both camps and cite representative work from each.

### Contribution
State the paper's contribution in 2–4 numbered or bulleted items.
Each contribution claim must be:

- **Specific** — name the artefact, dataset, theorem, or system.
- **Verifiable** — point at the section or experiment that supports it.
- **Calibrated** — distinguish "first to" from "demonstrates" from
  "improves on" claims.

Close with a brief roadmap of the paper's remaining sections.

## Style

- Match the target journal's voice. Nature-family tolerates more
  rhetorical framing; ACM / IEEE conferences prefer drier setup.
- Active voice, present tense for established claims, past tense for
  reporting your own results.
- No abstract-style restatement of results in the Introduction —
  Introduction sets up, the body delivers.
- Length: 800–1500 words unless the venue dictates otherwise.

## What NOT to do

- Do not pile on citations to demonstrate breadth — every citation
  must do work.
- Do not over-claim the contribution. Reviewers punish that more
  reliably than they reward enthusiasm.
- Do not introduce notation here — leave it for the Methods section.
