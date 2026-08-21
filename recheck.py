#!/usr/bin/env python3
"""
AI-CURA periodic VUS re-review — deterministic half of the recheck loop.

Re-runs the existing classifier (VEP/gnomAD/ClinVar — all live, free, no-auth
public APIs already used by main.py) against previously registered variants
and diffs the result against a persistent snapshot in
data/variant_registry.json. This script never re-assesses literature-
dependent criteria (PS2, PM6, PS3, PS4, PM3, PP1, PP4, PVS1_RNA) — those need
human-uploaded papers and are handled by the companion Claude loop in
vus_review_loop/. This script only drafts data; it never finalizes a
classification for a real variant record.

Examples
--------
  # Register a variant for future re-review (first-time classification)
  python recheck.py register "NM_007294.4:c.5266dupC"

  # Manually trigger a re-check right now (the "manual trigger" from TASK.md)
  python recheck.py variant "NM_007294.4:c.5266dupC"

  # Batch sweep: re-check everything last reviewed >6 months ago
  python recheck.py due --months 6
"""

import argparse
import json
import os
from datetime import datetime, timezone

from src.classifier import VariantClassifier
from src.registry import (
    load_registry, save_registry, snapshot_from_report,
    diff_snapshots, is_due, upsert_entry,
)

OUTPUTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")

LITERATURE_NOTE = (
    "PS2/PM6/PS3/PS4/PM3/PP1/PP4/PVS1_RNA were NOT re-assessed automatically "
    "(they require human-uploaded literature). Check for new papers manually, "
    "or via the vus_review_loop, before treating this variant as fully up to date."
)


def _process_variant(variant_id: str, registry: dict, genome: str = "GRCh38") -> dict:
    """Re-classify one variant (deterministic criteria only), diff against the
    registry, update the registry in place, and write a report file to
    outputs/ only if something automatable actually changed."""
    classifier = VariantClassifier(genome=genome, use_llm=False)
    report = classifier.classify(variant_id)
    report_dict = report.to_dict()
    new_snapshot = snapshot_from_report(report_dict)

    old_entry = registry.get(variant_id)
    old_snapshot = None
    if old_entry:
        old_snapshot = {
            "classification": old_entry["last_classification"],
            "criteria": old_entry["criteria_snapshot"],
        }

    diff = diff_snapshots(old_snapshot, new_snapshot)
    variant_type = report.variant.variant_type.value if report.variant.variant_type else "UNKNOWN"
    upsert_entry(registry, variant_id, variant_type, new_snapshot)

    changed = diff["classification_changed"] or bool(diff["criteria_changed"])
    result = {
        "variant_id": variant_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "automatable_change": changed,
        "diff": diff,
        "literature_note": LITERATURE_NOTE,
    }

    if changed or diff["is_new"]:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        safe_id = "".join(c if c.isalnum() else "_" for c in variant_id)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        report_path = os.path.join(OUTPUTS_DIR, f"recheck_{safe_id}_{date_str}.json")
        with open(report_path, "w") as f:
            json.dump(result, f, indent=2)
        result["report_path"] = report_path

    return result


def cmd_register(args):
    registry = load_registry()
    _process_variant(args.variant, registry, genome=args.genome)
    save_registry(registry)
    print(f"Registered {args.variant}: {registry[args.variant]['last_classification']}")


def cmd_variant(args):
    registry = load_registry()
    result = _process_variant(args.variant, registry, genome=args.genome)
    save_registry(registry)
    if result["automatable_change"]:
        d = result["diff"]
        print(f"CHANGED  {args.variant}: {d['old_classification']} -> {d['new_classification']}")
        print(f"  report: {result.get('report_path')}")
    else:
        print(f"no change  {args.variant}: {registry[args.variant]['last_classification']}")
    print(f"  note: {result['literature_note']}")


def cmd_due(args):
    registry = load_registry()
    due_ids = [vid for vid, entry in registry.items() if is_due(entry, months=args.months)]
    if not due_ids:
        print(f"No variants due for re-review (>{args.months} months since last check).")
        return
    print(f"{len(due_ids)} variant(s) due for re-review:")
    for vid in due_ids:
        result = _process_variant(vid, registry, genome=args.genome)
        if result["automatable_change"]:
            d = result["diff"]
            print(f"  CHANGED  {vid}: {d['old_classification']} -> {d['new_classification']}  (report: {result.get('report_path')})")
        else:
            print(f"  no change  {vid}")
    save_registry(registry)


def main():
    parser = argparse.ArgumentParser(
        prog="recheck",
        description="AI-CURA periodic VUS re-review (deterministic half of the recheck loop)",
    )
    parser.add_argument("--genome", default="GRCh38", choices=["GRCh37", "GRCh38"])
    sub = parser.add_subparsers(dest="command", required=True)

    p_register = sub.add_parser("register", help="Add/update a variant in the registry with a fresh classification")
    p_register.add_argument("variant")
    p_register.set_defaults(func=cmd_register)

    p_variant = sub.add_parser("variant", help="Manually trigger a re-check for one variant right now")
    p_variant.add_argument("variant")
    p_variant.set_defaults(func=cmd_variant)

    p_due = sub.add_parser("due", help="Re-check every registered variant last reviewed more than N months ago")
    p_due.add_argument("--months", type=int, default=6)
    p_due.set_defaults(func=cmd_due)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
