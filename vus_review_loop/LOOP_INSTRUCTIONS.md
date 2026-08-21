# Loop Instructions

You are running the literature/curator-review half of AI-CURA's VUS
re-review system. The deterministic half already ran (as `../recheck.py`,
either manually or on a schedule) and left its results in
`../outputs/recheck_*.json` and `../data/variant_registry.json`.

## Before You Start
1. Read `TASK.md`.
2. Read `PROGRESS.md`.
3. Read every `../outputs/recheck_*.json` file whose `checked_at` date is
   newer than this loop's last run (see `PROGRESS.md`'s "Last Run" date).
4. Read `../data/variant_registry.json` (read-only) for the variants named
   in those reports — note each variant's `literature_last_uploaded` field.
5. Check `../data/` for literature files (e.g. `.txt`/`.pdf`) whose
   modification time is newer than the variant's `literature_last_uploaded`,
   or that a curator has otherwise pointed you to.

## What You Should Do
For each variant with a fresh `recheck_*.json` report:

- If no new literature exists since `literature_last_uploaded`: note that
  literature-dependent criteria (PS2, PM6, PS3, PS4, PM3, PP1, PP4,
  PVS1_RNA) remain unassessed and this variant still needs a manual paper
  check. Do not guess at evidence that doesn't exist.
- If new literature does exist: read it and reason about which
  literature-dependent ACMG criteria it supports, the same way
  `src/llm.py`'s `assess_literature_criteria` does — per-paper extraction,
  then aggregate, applying cohort de-duplication (flag papers sharing an
  author or institution as the same patient cohort so PS4/PM3 aren't
  double-counted, per the AI-CURA paper's fig. S4).
- Combine the deterministic result (from the `recheck_*.json` report) with
  your literature assessment into a **recommended** classification. Mark it
  clearly as a recommendation, not a final call.

Write a curator-facing note to `outputs/curator-review-<date>.md`
including:
- Which variants were reviewed and why (fresh recheck report vs. due date)
- The deterministic (automatable) result and what changed, if anything
- Whether new literature was found, and if so, the criteria it supports
  with cited evidence
- A recommended classification, explicitly labeled "recommendation — not
  final"
- Anything that needs human review (ambiguous evidence, conflicting
  papers, possible cohort overlap you're unsure about)

After writing the note, update `PROGRESS.md` with:
- Date of this run
- Which variants were reviewed
- Summary of what happened
- Output produced
- What the next run should do
- Anything that needs human review

## Safety Rules
- Do not modify `../recheck.py`, anything in `../src/`, or `../main.py`.
- Do not modify `../data/variant_registry.json` — it is owned by the
  deterministic Python loop.
- Do not modify or delete any literature file in `../data/`.
- Only write to `outputs/curator-review-<date>.md` and this folder's
  `PROGRESS.md`.
- Never state that a classification is final or has been applied — always
  phrase it as a recommendation awaiting curator sign-off.
- If evidence is ambiguous, conflicting, or you're unsure whether two
  papers share a cohort, stop and flag it under "Needs Human Review"
  rather than guessing.

## Scheduled Run Policy
When this loop runs on a schedule:
- If there are no fresh `recheck_*.json` reports and no new literature,
  write a short "nothing new to review" note (or just update `PROGRESS.md`
  quietly) rather than a full report.
- If the same variant is flagged as "awaiting new literature" for two
  consecutive runs with still nothing new, do not keep re-flagging it the
  same way — note it once under "Needs Human Review" and stop repeating.
- Keep scheduled output short unless there is something a curator actually
  needs to act on.

## Verification Checklist
Before ending the run, verify the following:
### Required Files
- `outputs/curator-review-<date>.md` exists for this run (or the run
  legitimately had nothing new, per the Scheduled Run Policy).
- `PROGRESS.md` exists.
### Required Note Sections
`outputs/curator-review-<date>.md` must include:
- Variants reviewed and why
- Deterministic result / what changed
- Literature findings (or explicit "no new literature")
- Recommended classification, labeled as a recommendation
- Anything needing human review
### State Update
`PROGRESS.md` must include:
- Date of the current run
- Variants reviewed
- Summary of what happened
- Output produced
- What the next run should do
- Whether human review is needed
### Safety Boundary
Only these may be modified:
- `outputs/curator-review-<date>.md` (or other files inside this folder's `outputs/`)
- `PROGRESS.md`
`../recheck.py`, `../src/`, `../main.py`, `../data/variant_registry.json`,
and any literature file must remain untouched. If any of them was modified,
stop and report the issue.

## Failure Policy
If verification fails:
1. If the failure is a missing section in the curator note, fix it once.
2. If `PROGRESS.md` was not updated, update it once.
3. If any forbidden file was modified, stop immediately and report the issue.
4. If the same verification check fails twice, stop and mark the run as needing human review.

## Iteration Limit
- Maximum attempts per run: 2.
- If verification still fails after 2 attempts, stop and mark the run as
  needing human review in `PROGRESS.md` rather than continuing to retry.
