"""
Persistent state for periodic VUS re-review.

AI-CURA's classifier is a one-shot pipeline (README: "No local database; all
data is fetched live from public APIs"). This module adds the one thing a
recurring re-check loop needs that a single run doesn't: a small per-variant
record of what was true last time, so a later run can tell whether anything
actually changed.

Stores, per variant: when it was last reviewed, its last classification, and
a snapshot of which ACMG/ClinGen criteria were met. Literature-dependent
criteria (PS2, PM6, PS3, PS4, PM3, PP1, PP4, PVS1_RNA) are intentionally not
tracked here for automated diffing — they require human-uploaded papers and
are out of scope for this deterministic half of the loop.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

DEFAULT_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "variant_registry.json"
)


def load_registry(path: str = DEFAULT_REGISTRY_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def save_registry(registry: dict, path: str = DEFAULT_REGISTRY_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(registry, f, indent=2, sort_keys=True)


def snapshot_from_report(report_dict: dict) -> dict:
    """Build a comparable snapshot from a ClassificationReport.to_dict() result."""
    return {
        "classification": report_dict.get("classification"),
        "gnomad_af": report_dict.get("gnomad_af"),
        "criteria": {
            c["code"]: {
                "met": c["met"],
                "strength": c["strength"],
                "direction": c["direction"],
                "evidence": c["evidence"],
            }
            for c in report_dict.get("criteria", [])
        },
    }


def diff_snapshots(old: Optional[dict], new: dict) -> dict:
    """Compare two snapshots. Returns what changed; empty criteria_changed
    and classification_changed=False means nothing automatable moved."""
    if old is None:
        return {
            "is_new": True,
            "classification_changed": False,
            "old_classification": None,
            "new_classification": new["classification"],
            "criteria_changed": [],
        }

    changed = []
    old_criteria = old.get("criteria", {})
    new_criteria = new.get("criteria", {})
    for code, new_val in new_criteria.items():
        old_val = old_criteria.get(code)
        if old_val is None or old_val.get("met") != new_val.get("met"):
            changed.append({
                "code": code,
                "old_met": old_val.get("met") if old_val else None,
                "new_met": new_val.get("met"),
                "evidence": new_val.get("evidence"),
            })
    for code in old_criteria:
        if code not in new_criteria:
            changed.append({
                "code": code,
                "old_met": old_criteria[code].get("met"),
                "new_met": None,
                "evidence": "criterion no longer evaluated",
            })

    return {
        "is_new": False,
        "classification_changed": old.get("classification") != new.get("classification"),
        "old_classification": old.get("classification"),
        "new_classification": new.get("classification"),
        "criteria_changed": changed,
    }


def is_due(entry: dict, months: int = 6) -> bool:
    """True if a registry entry has never been reviewed, or was last reviewed
    more than `months` ago."""
    last = entry.get("last_reviewed")
    if not last:
        return True
    last_dt = datetime.fromisoformat(last)
    return datetime.now(timezone.utc) - last_dt >= timedelta(days=months * 30)


def upsert_entry(registry: dict, variant_id: str, variant_type: str, snapshot: dict) -> dict:
    """Write/update a variant's registry entry with a fresh snapshot.
    Preserves literature_last_uploaded and notes if already present."""
    entry = registry.get(variant_id, {})
    entry.update({
        "variant_id": variant_id,
        "variant_type": variant_type,
        "last_reviewed": datetime.now(timezone.utc).isoformat(),
        "last_classification": snapshot["classification"],
        "criteria_snapshot": snapshot["criteria"],
        "gnomad_af": snapshot.get("gnomad_af"),
    })
    entry.setdefault("literature_last_uploaded", None)
    entry.setdefault("notes", "")
    registry[variant_id] = entry
    return entry
