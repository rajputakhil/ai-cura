# VUS Re-Review Loop (Claude half)

## Goal
This is the literature/curator-facing half of AI-CURA's hybrid VUS re-review
system. The deterministic half (`../recheck.py`) re-runs the live-API-based
ACMG criteria (VEP/gnomAD/ClinVar) on a schedule or on demand and records the
result in `../data/variant_registry.json`. This loop picks up from there: it
checks whether new literature exists for flagged variants, reasons about the
literature-dependent ACMG criteria (PS2, PM6, PS3, PS4, PM3, PP1, PP4,
PVS1_RNA) the way `src/llm.py` does, and drafts a curator-facing re-review
note. It never re-implements the deterministic criteria and never touches
the registry file.

## Expected Output
Each run should produce or update:
- `outputs/curator-review-<date>.md` (one note per run, or per flagged variant)
- `PROGRESS.md`

## Division of Labor (important — read before changing anything)
- `../recheck.py` and `../data/variant_registry.json` are owned by the
  **Python deterministic loop**. This Claude loop must treat them as
  **read-only** context, never write to them, and never claim to have
  updated a variant's official classification.
- This loop owns only its own `outputs/` folder and its own `PROGRESS.md`.
  Its output is always a **draft recommendation** for a human curator —
  never a final classification.

## Scope
Claude may read: `TASK.md`, `PROGRESS.md`, `LOOP_INSTRUCTIONS.md` in this
folder; `../outputs/recheck_*.json` (the deterministic loop's reports);
`../data/variant_registry.json` (read-only, for context); `../data/*.txt`
and any other literature files a curator has added.

Claude should not modify source files (`../src/`, `../main.py`,
`../recheck.py`), the registry, uploaded literature files, or any file
outside this folder's `outputs/` and `PROGRESS.md`. Claude should not
finalize, sign off on, or apply a classification — only draft one for
review.
