# VUS Re-Review Loop — Design Notes

A periodic re-review system for AI-CURA, designed using Claude Loop
Engineering principles (trigger → context → action → verification → state
update → decision), and implemented as a **hybrid** of a plain Python
scheduled script and a Claude-orchestrated loop.

## Why a loop, and why hybrid

Variant classification is a one-time judgment that can go stale: ACMG/AMP
guidance is explicit that a VUS should be periodically revisited as new
population-frequency data, ClinVar submissions, or literature accumulate.
That makes VUS re-review a genuinely good loop candidate — it repeats
(recommended every ~6 months), it's checkable (ACMG/ClinGen criteria are
themselves a checklist), and it has a safe stopping point (always draft,
never auto-finalize).

But not all of it needs an LLM. Re-running VEP/gnomAD/ClinVar and diffing
the result is deterministic and cheap — that's ordinary code, not a
reasoning task. Re-checking literature for new evidence and drafting a
curator-facing note *is* a reasoning task Claude is well suited for. So the
loop is split accordingly:

| Half | What it does | Owns |
|---|---|---|
| **Python** (`recheck.py`, `src/registry.py`) | Re-runs the existing live-API classifier (VEP/gnomAD/ClinVar), diffs against the last snapshot, updates state | `data/variant_registry.json` |
| **Claude loop** (`vus_review_loop/`) | Reads the Python side's output, checks for new literature, reasons about literature-dependent criteria, drafts a curator note | `vus_review_loop/outputs/`, `vus_review_loop/PROGRESS.md` |

Each half only ever writes to its own state. Neither touches the other's
files — this avoids the two automations racing on the same file, and keeps
the permission boundary simple to reason about.

## Loop anatomy

**Trigger** — `python recheck.py variant <id>` for an on-demand manual
check, or `python recheck.py due --months 6` for a scheduled sweep over
every registered variant last reviewed more than 6 months ago (cron / Task
Scheduler on the Python side; the Claude loop half can run afterward, e.g.
via a Cowork scheduled task, or on the same cadence).

**Context** — `data/variant_registry.json` (per-variant last classification
and criteria snapshot — the thing AI-CURA didn't have before, since the
base tool is explicitly stateless: "No local database; all data is fetched
live from public APIs," per the original README). The Claude loop's context
is the Python side's fresh `outputs/recheck_*.json` reports plus any new
literature files in `data/`.

**Action** — Python: re-classify via `VariantClassifier(use_llm=False)`,
which reuses the exact same VEP/gnomAD/ClinVar calls the CLI already makes.
Claude: read new literature (if any), assess the literature-dependent ACMG
criteria (PS2, PM6, PS3, PS4, PM3, PP1, PP4, PVS1_RNA) the way `src/llm.py`
does for the interactive app — per-paper extraction, cohort de-duplication,
aggregation — then draft a combined recommendation.

**Verification** — Python: did the registry actually update, was a report
only written when something automatable changed (not on every no-op run).
Claude: does the curator note have every required section, is the
recommendation explicitly labeled non-final, were only the allowed files
touched.

**State update** — Python updates `variant_registry.json` in place. Claude
updates its own `PROGRESS.md` — never the registry.

**Decision** — no automatable change and no new literature → stay quiet
(update state, no report); something changed or new literature exists →
produce a draft for the curator; ambiguous evidence or possible cohort
overlap → flag for human review rather than guess.

## What's real vs. what's a demo boundary (say this in the interview)

- The Python re-check genuinely re-runs live public APIs (Ensembl VEP,
  gnomAD, NCBI ClinVar) — same code path as the interactive app, just
  headless and diffed against history.
- It intentionally does **not** re-assess literature-dependent criteria
  automatically — those require a human to supply papers (or the Claude
  loop to read newly supplied ones). This mirrors a real limitation
  honestly rather than papering over it.
- The state file (`variant_registry.json`) is a JSON file, appropriate for
  a prototype with a handful of demo variants. A production version at lab
  scale would move this into a real database or LIMS integration — the
  loop's *shape* (trigger/context/action/verification/state/decision)
  doesn't change, only where the state lives.
- Every path in this design stops at a draft. No code here ever writes to
  an "official" classification — that's a deliberate Level 2 permission
  ladder choice (read + draft only), appropriate for anything touching
  clinical/diagnostic output.

## Talking point (short version)

"AI-CURA's base tool is a one-shot classifier — you give it a variant, it
gives you a classification, and it forgets. Real curation isn't one-shot:
ACMG guidance expects VUS calls to be revisited as evidence accumulates. I
designed a re-review loop on top of the existing tool without changing its
core: a small Python layer adds the missing persistent state and re-runs
the same deterministic APIs on a schedule, and a Claude-orchestrated layer
handles the part that's actually a reasoning task — reading new literature
and drafting an updated recommendation. Every path stops at a draft for a
human curator; nothing auto-finalizes a classification."
