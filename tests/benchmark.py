"""
Benchmark AI-CURA prototype against the 150 ClinGen-curated variants from
Ma et al. (2026) Table S12.

Usage:
  python tests/benchmark.py                  # run all 150, literature-independent only
  python tests/benchmark.py --limit 20       # quick run on first 20 variants
  python tests/benchmark.py --csv            # save results to benchmark_results.csv

Classification matching logic mirrors the paper:
  Exact match         — prototype classification = human curator classification
  Adjacent match      — within one category (e.g. Likely P vs P)
  Mismatch            — more than one category apart

This benchmark tests ONLY the literature-independent ACMG criteria (same as the
"Reaccessed literature-independent ACMG rules" column in Table S12). The paper's
full accuracy (89-100%) comes from adding literature-dependent criteria via LLM.

Note: live API calls to Ensembl VEP, gnomAD, and ClinVar are made for each variant.
Expect ~5-10 seconds per variant. The --limit flag is recommended for quick demos.
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.variant import parse_variant
from src.apis import vep_annotate, gnomad_frequency, clinvar_search_hgvs
from src.acmg import apply_criteria

_BENCHMARK_FILE = Path(__file__).parent.parent / "data" / "benchmark_variants.json"

# Canonical category ordering for adjacency check
_ORDER = [
    "Benign",
    "Likely Benign",
    "Variant of Uncertain Significance",
    "Likely Pathogenic",
    "Pathogenic",
]
_ALIASES = {
    "VUS": "Variant of Uncertain Significance",
    "Likely benign": "Likely Benign",
    "Likely pathogenic": "Likely Pathogenic",
}


def normalise(label: str) -> str:
    if label is None:
        return "Unknown"
    label = label.strip()
    return _ALIASES.get(label, label)


def match_type(predicted: str, expected: str) -> str:
    p = normalise(predicted)
    e = normalise(expected)
    if p == e:
        return "exact"
    try:
        dist = abs(_ORDER.index(p) - _ORDER.index(e))
        return "adjacent" if dist == 1 else "mismatch"
    except ValueError:
        return "mismatch"


def classify_variant(entry: dict) -> dict:
    """Run the classification pipeline for one benchmark variant."""
    raw = entry["variant"]
    # Strip parenthetical gene name from HGVS: "NM_000018.4(ACADVL):c.1605+6T>C" → "NM_000018.4:c.1605+6T>C"
    import re
    clean = re.sub(r'\([^)]+\)', '', raw).split(' ')[0].strip()

    try:
        v = parse_variant(clean)
    except ValueError:
        return {"variant": raw, "error": f"parse error: {clean}"}

    try:
        vep = vep_annotate(clean) if v.hgvs_c else {}
    except Exception as e:
        vep = {"_error": str(e)}

    try:
        gnomad = {}
        if v.chromosome and v.position and v.ref and v.alt:
            gnomad = gnomad_frequency(v.chromosome, v.position, v.ref, v.alt)
    except Exception as e:
        gnomad = {"_error": str(e)}

    try:
        clinvar = clinvar_search_hgvs(clean, entry.get("gene", ""))
    except Exception as e:
        clinvar = [{"_error": str(e)}]

    try:
        result = apply_criteria(vep, gnomad, clinvar, getattr(v, "hgvs_p", None))
        classification = result.classification
        criteria_met = [c.code for c in result.criteria if c.met]
    except Exception as e:
        classification = "Error"
        criteria_met = []

    return {
        "gene": entry["gene"],
        "variant": raw,
        "expected_human": entry.get("human_classification", ""),
        "expected_clingen": entry.get("clingen_classification", ""),
        "lit_indep_rules_paper": entry.get("lit_indep_rules", ""),
        "predicted": classification,
        "criteria_met": ", ".join(criteria_met),
        "match_vs_human": match_type(classification, entry.get("human_classification", "")),
        "match_vs_clingen": match_type(classification, entry.get("clingen_classification", "")),
    }


_SHORT = {
    "Benign": "B", "Likely Benign": "LB",
    "Variant of Uncertain Significance": "VUS",
    "Likely Pathogenic": "LP", "Pathogenic": "P",
}


def _print_confusion_matrix(results):
    """5x5 confusion matrix (expected human vs predicted) + per-class precision/recall."""
    labels = _ORDER  # B, LB, VUS, LP, P
    idx = {l: i for i, l in enumerate(labels)}
    mat = [[0] * len(labels) for _ in labels]

    usable = 0
    for r in results:
        exp = normalise(r.get("expected_human", ""))
        pred = normalise(r.get("predicted", ""))
        if exp in idx and pred in idx:
            mat[idx[exp]][idx[pred]] += 1
            usable += 1

    short = [_SHORT[l] for l in labels]
    print("CONFUSION MATRIX  (rows = expected human, cols = predicted)")
    print("            " + "".join(f"{s:>6}" for s in short))
    for i, l in enumerate(labels):
        print(f"  {_SHORT[l]:>4} (exp) " + "".join(f"{mat[i][j]:>6}" for j in range(len(labels))))

    print(f"\nPER-CLASS METRICS  ({usable} classifiable variants)")
    print(f"  {'Class':<6}{'Prec':>8}{'Recall':>8}{'F1':>8}{'Support':>9}")
    for i, l in enumerate(labels):
        tp = mat[i][i]
        col = sum(mat[r][i] for r in range(len(labels)))   # predicted as i
        row = sum(mat[i])                                   # actually i
        prec = tp / col if col else 0.0
        rec = tp / row if row else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        print(f"  {_SHORT[l]:<6}{prec:>8.2f}{rec:>8.2f}{f1:>8.2f}{row:>9}")

    diag = sum(mat[i][i] for i in range(len(labels)))
    print(f"\n  Overall exact accuracy (classifiable only): {diag}/{usable} "
          f"({100*diag/usable:.1f}%)" if usable else "  No classifiable variants.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Benchmark AI-CURA against ST12 ClinGen variants")
    parser.add_argument("--limit", type=int, default=0, help="Only run first N variants (0 = all)")
    parser.add_argument("--csv", action="store_true", help="Save results to benchmark_results.csv")
    args = parser.parse_args()

    with open(_BENCHMARK_FILE) as f:
        variants = json.load(f)

    if args.limit:
        variants = variants[:args.limit]

    print(f"Running benchmark on {len(variants)} variants from Ma et al. ST12...\n")

    results = []
    for i, entry in enumerate(variants, 1):
        print(f"[{i:3}/{len(variants)}] {entry['gene']:10} {entry['variant'][:50]:<52}", end=" ", flush=True)
        t0 = time.time()
        r = classify_variant(entry)
        elapsed = time.time() - t0
        match = r.get("match_vs_human", "error")
        symbol = {"exact": "✓", "adjacent": "~", "mismatch": "✗"}.get(match, "!")
        print(f"{symbol} {r.get('predicted', 'error'):35} [{elapsed:.1f}s]")
        results.append(r)
        time.sleep(0.3)  # polite to APIs

    # Summary
    exact    = sum(1 for r in results if r.get("match_vs_human") == "exact")
    adjacent = sum(1 for r in results if r.get("match_vs_human") == "adjacent")
    mismatch = sum(1 for r in results if r.get("match_vs_human") == "mismatch")
    errors   = sum(1 for r in results if "error" in r)
    total    = len(results)

    print(f"""
{'='*60}
BENCHMARK RESULTS (literature-independent criteria only)
Compared against: Human curator classification (Table S12)
{'='*60}
  Exact match    : {exact:3} / {total} ({100*exact/total:.1f}%)
  Adjacent match : {adjacent:3} / {total} ({100*adjacent/total:.1f}%)
  Mismatch       : {mismatch:3} / {total} ({100*mismatch/total:.1f}%)
  Errors         : {errors:3} / {total}
{'='*60}
Note: The paper achieves 89-100% accuracy by ALSO applying
literature-dependent criteria (PS3, PS4, PM3, PP1, PP4, etc.)
via LLM. This benchmark only tests the automated pipeline.
""")

    _print_confusion_matrix(results)

    if args.csv:
        out = Path("benchmark_results.csv")
        fieldnames = ["gene", "variant", "expected_human", "expected_clingen",
                      "lit_indep_rules_paper", "predicted", "criteria_met",
                      "match_vs_human", "match_vs_clingen"]
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(results)
        print(f"Results saved to {out}")


if __name__ == "__main__":
    main()
