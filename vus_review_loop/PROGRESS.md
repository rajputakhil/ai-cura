# Loop Progress

## Current State
- Status: Manual setup — not yet run
- Main objective: Draft curator-facing literature re-review notes for variants flagged by ../recheck.py
- Current focus: Validate manually before scheduling
- Last updated:

## Last Run
- Date:
- Trigger:
- Summary:
- Variants reviewed:
- Output produced:

## Open Items
-

## Blockers
-

## Needs Human Review
-

## Next Run Should
- Read `TASK.md`, `PROGRESS.md`, and `LOOP_INSTRUCTIONS.md`.
- Check `../outputs/` for new `recheck_*.json` reports since the last run.
- Check `../data/variant_registry.json` (read-only) for variants due or flagged.
- Check `../data/` for literature files added since each variant's `literature_last_uploaded`.
- Write `outputs/curator-review-<date>.md`.
- Update this file before stopping.

## Decisions Made
- This loop is read-only with respect to `../recheck.py`, `../data/variant_registry.json`, and any source files — it only ever writes drafts.
- Classification changes are always recommendations for a human curator, never final.
- The Python deterministic loop and this Claude loop must never write to the same state file, to avoid races/conflicting updates.

## Do Not Repeat
-
