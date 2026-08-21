"""
Simplified ClinGen CNV dosage scoring.

Implements Section 2 (dosage-sensitive gene/region overlap) of the ACMG/ClinGen
Technical Standards for CNV interpretation (Riggs et al., Genet Med 2020) — the
single most decisive section. A complete overlap of an established (score 3)
haploinsufficient gene for a LOSS, or triplosensitive gene for a GAIN, scores
1.00 points and is classified Pathogenic on its own.

This is intentionally a SUBSET of the full framework. Full CNV curation also
weighs gene number (Section 3), case/literature evidence (Section 4), and
inheritance (Section 5). For complete scoring use the ClinGen CNV calculator:
https://cnvcalc.clinicalgenome.org/

Classification point ranges (per the standard):
  >= 0.99            Pathogenic
  0.90 to 0.98       Likely Pathogenic
  -0.89 to 0.89      Variant of Uncertain Significance
  -0.90 to -0.98     Likely Benign
  <= -0.99           Benign
"""

import json
from pathlib import Path

_DATA = Path(__file__).parent.parent / "data" / "clingen_dosage.json"

# Section 2 point contributions by dosage score (approximation of the standard)
_POINTS = {3: 1.00, 2: 0.45, 1: 0.15, 40: -0.30, 30: 0.0, 0: 0.0}


def _load():
    if not _DATA.exists():
        return {"genes": []}
    return json.load(open(_DATA))


def _norm_chrom(c):
    return str(c).replace("chr", "").replace("Chr", "").strip()


def _overlap(a0, a1, b0, b1):
    return a0 <= b1 and b0 <= a1


def _classify(score):
    if score >= 0.99:
        return "Pathogenic"
    if score >= 0.90:
        return "Likely Pathogenic"
    if score <= -0.99:
        return "Benign"
    if score <= -0.90:
        return "Likely Benign"
    return "Variant of Uncertain Significance"


def score_cnv(chrom, start, end, cnv_type):
    """Score a CNV against the ClinGen dosage map (Section 2 only).

    Returns a dict:
      classification, score, cnv_type, overlaps (list of dicts),
      gene_count, summary.
    """
    data = _load()
    chrom = _norm_chrom(chrom)
    is_loss = str(cnv_type).upper() in ("DEL", "LOSS", "DELETION")
    dosage_key = "hi" if is_loss else "ts"
    direction_word = "haploinsufficient (loss)" if is_loss else "triplosensitive (gain)"

    overlaps = []
    for g in data.get("genes", []):
        if _norm_chrom(g["chrom"]) != chrom:
            continue
        if not _overlap(start, end, g["start"], g["end"]):
            continue
        dscore = g.get(dosage_key, 0)
        pts = _POINTS.get(dscore, 0.0)
        # Determine if fully contained (complete overlap) vs partial
        complete = start <= g["start"] and end >= g["end"]
        overlaps.append({
            "name": g["name"],
            "dosage_score": dscore,
            "points": pts,
            "complete": complete,
            "condition": g.get("condition", ""),
        })

    # Section 2 contribution = strongest single established overlap
    relevant = [o for o in overlaps if o["points"] != 0.0]
    score = max([o["points"] for o in relevant], default=0.0)
    # A partial overlap of an established gene is slightly downgraded
    top = max(relevant, key=lambda o: o["points"], default=None)
    if top and not top["complete"] and top["points"] == 1.00:
        score = 0.90  # partial overlap of established gene -> Likely Pathogenic band

    classification = _classify(score)

    if overlaps:
        hits = ", ".join(f"{o['name']} ({dosage_key.upper()}={o['dosage_score']})" for o in overlaps)
        summary = (
            f"{cnv_type} on chr{chrom}:{start:,}-{end:,} (~{(end-start)/1e6:.2f} Mb).\n"
            f"Overlaps {len(overlaps)} dosage-mapped gene/region: {hits}.\n"
            f"Section 2 ({direction_word}) score = {score:+.2f} -> {classification}.\n"
            "Note: simplified ClinGen Section 2 only. Sections 3-5 (gene count, case "
            "evidence, inheritance) require the full ClinGen CNV calculator."
        )
    else:
        summary = (
            f"{cnv_type} on chr{chrom}:{start:,}-{end:,} (~{(end-start)/1e6:.2f} Mb).\n"
            "No overlap with an established dosage-sensitive gene/region in the bundled "
            "ClinGen subset. Full curation (case evidence, gene content, inheritance) "
            "is needed — see the ClinGen CNV calculator."
        )

    return {
        "classification": classification,
        "score": score,
        "cnv_type": cnv_type,
        "is_loss": is_loss,
        "overlaps": overlaps,
        "gene_count": len(overlaps),
        "summary": summary,
    }
