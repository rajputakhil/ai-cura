# DSPy migration (this branch)

This branch (`dspy-migration`) replaces the hand-rolled prompt + regex-JSON
parsing used for the literature-*dependent* ACMG/AMP criteria with a typed
[DSPy](https://github.com/stanfordnlp/dspy) signature. `main` is left
untouched so the two can be compared directly.

## What changed

- **New:** `src/dspy_modules.py` -- a DSPy `Signature`
  (`ExtractLiteratureCriteria`) and `Module` (`LiteratureCriteriaExtractor`)
  that extract PS3 / PS4 / PM3 / PS2_PM6 / PP1 / PP4 / PVS1_RNA from a paper,
  plus `configure()` to point DSPy at either the Claude or Ollama backend
  (same `backend` / `model` args used everywhere else in the app).
- **Changed:** `src/llm.py` -- `assess_literature_criteria()` and
  `assess_literature_criteria_multi()` now call `dspy_modules.py` instead of
  building `_LIT_CRITERIA_PROMPT` / `_PAPER_PROMPT` by hand and scraping JSON
  out of the response with `_parse_lit_criteria()` / `_parse_paper()`. Return
  shapes are unchanged, so `app.py` needed no changes.
- **Unchanged:** `synthesize()`, `assess_pvs1_rna()`, `assess_ps3()`,
  `assess_pp1()`, and everything in `acmg.py` / `cnv.py` / `apis.py` /
  `classifier.py` -- this migration is scoped to the two functions that had
  the brittle prompt+regex pattern.
- The old prompt constants and parsers (`_LIT_CRITERIA_PROMPT`,
  `_PAPER_PROMPT`, `_parse_lit_criteria`, `_parse_paper`) are left in `llm.py`,
  marked as superseded, purely as a reference to the original Ma et al.
  Supplementary Methods prompt text -- nothing calls them anymore.

## Setup

```bash
pip install -r requirements.txt   # now includes dspy + pydantic
```

Same env vars as `main`: `ANTHROPIC_API_KEY` for the Claude backend, or a
running `ollama serve` for the local backend (`OLLAMA_URL` if not default).

## Comparing against `main`

The literature-*independent* path (VEP/gnomAD/ClinVar -> deterministic ACMG
rules) is untouched on both branches, so `tests/benchmark.py` will score
identically there. The interesting comparison is on literature-dependent
extraction quality -- upload the same paper(s) via the Streamlit app
(`streamlit run app.py`) on each branch and compare the criteria extracted,
or script it directly:

```python
from src.llm import assess_literature_criteria

criteria, raw = assess_literature_criteria(
    variant_str="NM_007294.4:c.5266dupC",
    paper_text=open("data/sample_paper.txt").read(),
    backend="ollama",   # or "claude"
)
print(criteria)
```

## Optimizing further (not done yet on this branch)

DSPy's value beyond "typed output instead of regex" is its optimizers
(`dspy.MIPROv2`, `dspy.BootstrapFewShot`), which search for better
instructions/few-shot examples against a metric you define. That needs a
small labeled set of `(variant, paper_text) -> gold criteria` examples --
`data/sample_paper.txt` / `sample_paper_2.txt` are a starting point, but
there's no gold-labeled criteria set for them yet. Once you've hand-labeled
~15-20 such examples, wrap them as `dspy.Example`s, write a metric that
scores overlap between predicted and gold `(code, met)` pairs, and run an
optimizer over `LiteratureCriteriaExtractor` -- that's the next step, not
included in this branch.
