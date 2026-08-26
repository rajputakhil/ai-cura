"""
DSPy-based structured extraction for AI-CURA's literature-dependent ACMG/AMP
criteria (PS3, PS4, PM3, PS2_PM6, PP1, PP4, PVS1_RNA).

This replaces the hand-rolled prompt + regex-JSON-scraping approach that used
to live in llm.py (_LIT_CRITERIA_PROMPT / _PAPER_PROMPT / _parse_lit_criteria /
_parse_paper) with a typed DSPy Signature. The output schema is enforced by
DSPy's adapter layer instead of a regex, so a model that wraps its answer in
prose, or a reasoning model that emits a <think> block, gets retried against
the schema instead of silently producing an empty criteria list.

Backend-agnostic by design: dspy.LM() points at either Anthropic or a local
Ollama model depending on the same `backend` / `model` arguments already used
throughout llm.py, so callers don't need to change how they choose a backend.
"""

import os
from typing import Literal, Optional

import dspy
from pydantic import BaseModel, Field

# Same allowed criterion codes / strengths as the original AI-CURA prompts
# (Ma et al. 2026 Supplementary Methods) -- see llm.py's _LIT_CRITERIA_PROMPT.
_CriterionCode = Literal["PS3", "PS4", "PM3", "PS2_PM6", "PP1", "PP4", "PVS1_RNA"]
_Strength = Literal["supporting", "moderate", "strong", "very_strong"]


class LitCriterion(BaseModel):
    code: _CriterionCode
    strength: _Strength = Field(description="Evidence strength for this criterion")
    met: bool = Field(default=True, description="Whether the criterion applies to this variant")
    evidence: str = Field(
        description="One sentence citing what the paper's AUTHORS explicitly report "
                     "-- never cited work or assumptions"
    )


class ExtractLiteratureCriteria(dspy.Signature):
    """Apply the literature-DEPENDENT ACMG/AMP criteria to a variant by reading one
    primary paper. Only use evidence the paper's authors explicitly report FOR THIS
    VARIANT -- never assumptions, deductions, or work the paper merely cites.

    Criteria: PS3 (functional assay; needs wild-type AND null controls), PS4 (case
    enrichment vs. controls), PM3 (recessive gene, found in trans with a known
    pathogenic variant), PS2_PM6 (confirmed/assumed de novo), PP1 (cosegregation
    with disease across affected family members), PP4 (phenotype highly specific
    to the gene), PVS1_RNA (RNA study confirms a splicing loss-of-function effect).

    If no literature-dependent criteria apply, return an empty list.
    """

    variant: str = dspy.InputField(desc="target variant, e.g. NM_007294.4:c.5266dupC")
    paper_text: str = dspy.InputField(desc="full text of one primary paper (or excerpt)")
    source: str = dspy.OutputField(
        desc="first author's surname and recruiting institution/hospital if stated, "
             "else 'unknown' -- used to detect overlapping patient cohorts across papers"
    )
    criteria: list[LitCriterion] = dspy.OutputField(
        desc="one entry per criterion that applies; empty list if none do"
    )


_lm_cache: dict = {}


def _ollama_api_base(url: str) -> str:
    """llm.py's OLLAMA_URL is the /api/generate endpoint; DSPy's ollama_chat
    backend (via litellm) wants just the host, e.g. http://localhost:11434."""
    for suffix in ("/api/generate", "/api/chat"):
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return url


def configure(backend: str = "claude", model: Optional[str] = None,
              api_key: Optional[str] = None) -> None:
    """Point DSPy at the right backend/model. Cached by (backend, model) so
    repeated calls in a loop (e.g. one per uploaded paper) don't reinstantiate
    the client every time."""
    cache_key = (backend, model, bool(api_key))
    if _lm_cache.get("key") == cache_key:
        return
    if backend == "ollama":
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
        lm = dspy.LM(
            f"ollama_chat/{model or 'deepseek-r1:1.5b'}",
            api_base=_ollama_api_base(ollama_url),
            api_key="",
        )
    else:
        key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        lm = dspy.LM(f"anthropic/{model or 'claude-haiku-4-5-20251001'}", api_key=key)
    dspy.configure(lm=lm)
    _lm_cache["key"] = cache_key


class LiteratureCriteriaExtractor(dspy.Module):
    """ChainOfThought so the reasoning trace is available to callers for QC
    review (surfaced as `raw`), not just the final structured criteria list."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractLiteratureCriteria)

    def forward(self, variant: str, paper_text: str):
        return self.extract(variant=variant, paper_text=paper_text[:12000])


def extract_literature_criteria(variant_str: str, paper_text: str,
                                 backend: str = "ollama", model: Optional[str] = None,
                                 api_key: Optional[str] = None):
    """Run the extractor and translate its output into the plain-dict shape the
    rest of AI-CURA already expects (the shape _parse_lit_criteria/_parse_paper
    used to hand-build via regex): {code, strength, direction, met, evidence}.

    Returns (source, criteria_dicts, raw_reasoning_text).
    """
    configure(backend=backend, model=model, api_key=api_key)
    extractor = LiteratureCriteriaExtractor()
    pred = extractor(variant=variant_str, paper_text=paper_text)
    criteria_dicts = [
        {
            "code": c.code,
            "strength": c.strength,
            "direction": "pathogenic",
            "met": c.met,
            "evidence": str(c.evidence)[:300],
        }
        for c in pred.criteria
    ]
    raw = getattr(pred, "reasoning", "") or str(pred)
    return pred.source, criteria_dicts, raw
