"""
Optimize LiteratureCriteriaExtractor against a small hand-labeled example set.

Usage:
  python scripts/optimize_literature_extractor.py --backend ollama --model llama3.1:8b
  python scripts/optimize_literature_extractor.py --backend claude

What this does:
  1. Loads data/dspy_trainset/gold_labels.json + the paper text files next to
     it (7 synthetic papers, each engineered to isolate one or two literature-
     dependent criteria, including a clean negative and a PP1-vs-PM3 contrast
     pair -- see that file for what each one tests).
  2. Scores the CURRENT (hand-written prompt) extractor against all 7 as a
     baseline.
  3. Runs dspy.BootstrapFewShot, which tries the extractor on the trainset,
     keeps the demonstrations where it did well, and compiles a new version
     that includes those as few-shot examples alongside the instructions.
  4. Scores the optimized version the same way.
  5. If (and only if) the optimized version scores higher, saves it to
     src/dspy_compiled_literature_extractor.json. LiteratureCriteriaExtractor
     in src/dspy_modules.py loads this file automatically if present, so no
     other code needs to change to start using the optimized version.

Known limitation: with only 7 examples, this evaluates the optimizer on the
same set it bootstrapped demonstrations from (not a held-out dev set) -- a
real held-out split needs more labeled examples than we have yet. Treat the
"optimized" score as a sanity check that it didn't get worse, not proof it
generalizes. Grow data/dspy_trainset/ (more papers + gold_labels.json entries)
before trusting this for anything beyond a first pass.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import dspy
from src.dspy_modules import LiteratureCriteriaExtractor, configure

TRAINSET_DIR = Path(__file__).parent.parent / "data" / "dspy_trainset"
COMPILED_PATH = Path(__file__).parent.parent / "src" / "dspy_compiled_literature_extractor.json"


def load_examples():
    labels = json.loads((TRAINSET_DIR / "gold_labels.json").read_text())
    examples = []
    for entry in labels:
        paper_text = (TRAINSET_DIR / entry["paper"]).read_text()
        gold = frozenset((g["code"], g["met"]) for g in entry["gold"])
        ex = dspy.Example(
            variant=entry["variant"],
            paper_text=paper_text,
            gold=gold,
        ).with_inputs("variant", "paper_text")
        examples.append(ex)
    return examples


def criteria_set(criteria):
    """Turn a predicted list of LitCriterion (pydantic objects) into a
    comparable set of (code, met) tuples."""
    return {(c.code, c.met) for c in criteria}


def metric(example, prediction, trace=None):
    """F1 over (code, met) pairs between predicted and gold criteria.
    Both empty (correct negative) scores a perfect 1.0."""
    predicted = criteria_set(prediction.criteria)
    gold = example.gold
    if not predicted and not gold:
        return 1.0
    if not predicted or not gold:
        return 0.0
    tp = len(predicted & gold)
    precision = tp / len(predicted)
    recall = tp / len(gold)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def evaluate(module, examples, label):
    scores = []
    for ex in examples:
        try:
            pred = module(variant=ex.variant, paper_text=ex.paper_text)
            score = metric(ex, pred)
        except Exception as e:
            print(f"  [{ex.variant}] ERROR: {e}")
            score = 0.0
        scores.append(score)
        print(f"  [{label}] {ex.variant[:40]:40} score={score:.2f}")
    avg = sum(scores) / len(scores) if scores else 0.0
    print(f"  {label} average F1: {avg:.3f}  ({len(examples)} examples)\n")
    return avg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="ollama", choices=["ollama", "claude"])
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    configure(backend=args.backend, model=args.model)

    examples = load_examples()
    print(f"Loaded {len(examples)} labeled examples from {TRAINSET_DIR}\n")

    print("=== Baseline (current hand-written prompt) ===")
    baseline_score = evaluate(LiteratureCriteriaExtractor(), examples, "baseline")

    print("=== Running dspy.BootstrapFewShot ===")
    optimizer = dspy.BootstrapFewShot(metric=metric, max_bootstrapped_demos=4, max_labeled_demos=4)
    optimized = optimizer.compile(LiteratureCriteriaExtractor(), trainset=examples)

    print("=== Optimized ===")
    optimized_score = evaluate(optimized, examples, "optimized")

    print(f"Baseline avg F1:  {baseline_score:.3f}")
    print(f"Optimized avg F1: {optimized_score:.3f}")

    if optimized_score > baseline_score:
        optimized.save(str(COMPILED_PATH))
        print(f"\nOptimized version is better -- saved to {COMPILED_PATH}")
        print("src/dspy_modules.py will load this automatically from now on.")
    else:
        print("\nOptimized version did not beat the baseline on this small set -- "
              "not saving anything. This can happen with only 7 examples; "
              "try again after growing data/dspy_trainset/, or with a different "
              "--backend/--model.")


if __name__ == "__main__":
    main()
