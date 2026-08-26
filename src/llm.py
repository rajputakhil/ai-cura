"""
LLM synthesis layer for AI-CURA.
Prompts are verbatim from Ma et al. (2026) Supplementary Methods (pages 3-9).
Requires ANTHROPIC_API_KEY (only used with --llm flag).
"""

import json
import os
from pathlib import Path
from typing import Optional

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

from .acmg import ACMGResult

# Ollama endpoint (local by default)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")

_DATA_DIR = Path(__file__).parent.parent / "data"


def _load_json(filename):
    p = _DATA_DIR / filename
    return json.load(open(p)) if p.exists() else []


_PP1_KB = _load_json("pp1_knowledgebase.json")  # Table S9
_PS4_KB = _load_json("ps4_thresholds.json")      # Table S11

_SYS = (
    "You are a clinical genomics expert applying ACMG/AMP variant classification guidelines. "
    "Be concise and evidence-based. Flag conflicts explicitly. Note uncertainties."
)

# ---------------------------------------------------------------------------
# Verbatim prompts from Ma et al. (2026) Supplementary Methods
# ---------------------------------------------------------------------------

_PVS1_RNA = """Examine the attached paper. Determine if it included any RNA study to investigate
the splicing effect of the target variant {variant}.

If RNA-based functional studies (RNA-seq, RT-PCR, minigene assay) are present, determine:
- The splicing consequence: intron retention, exon skipping, cryptic splice site, alternative
  splice site, or other abnormality
- The downstream mRNA/protein effect: does it disrupt the reading frame and trigger NMD,
  or is the frame preserved?
- Whether the proportion of alternative transcript is "complete", "near complete",
  "incomplete", or "not specified"

Output a table: presence/absence of RNA study, study type, tissue, splice effect,
mRNA/protein effect."""

_PS3 = """Examine all papers and supplementary materials for experimental evidence that
the target variant {variant} has functional impact. Only consider experiments by the
paper authors, not cited work.

Check for normal/wild-type AND abnormal/null controls (target variant must not be the
abnormal control). Do not make assumptions.

PS3_Supporting applies if:
- Deleterious functional impact confirmed AND both control types used
OR
- Multi-cellular model organism used (fly, zebrafish, mice)

Output a table: functional impacts, PS3_Supporting applicable, evidence type
(in vitro/in vivo/model organism), controls used, MAVE study performed."""

_PS4 = """From the attached paper, output:
List of authors: [names and affiliations]
Description of patients: [where recruited, recruitment period]
Number of patients with target variant {variant}: [count]

Count only probands directly recruited by the authors (not cited cases).
For each family, count only the proband."""

_PP1 = """Using the PP1 scoring knowledgebase below and all available family data,
find the number of cosegregations for target variant {variant}.

PP1 Scoring Knowledgebase (Table S9):
{pp1_kb}

Rules:
- "Affected" = has phenotype, not just carrier status
- Only count genotypes confirmed by genetic tests (DNA/RNA-based)
- Do not count the proband as an affected cosegregation
- For de novo probands: do not count their unaffected parents
- Obligate carriers count as having the variant without genetic confirmation

Output total score and PP1 strength:
Supporting (0.5-0.9 pts), Moderate (1-2.9 pts), Strong (>=3 pts)."""

_PP4 = """Target variant: {variant}

Find all individuals carrying this variant. Record only phenotypes directly stated by
the authors - do not assume or deduce.

For phenotypes specified as cohort inclusion criteria, consider all patients as meeting them.
Record gene panels sequenced (assume all genes if WES/WGS was performed).

Assign PP4 strength (Supporting/Moderate/Strong) based on: phenotype specificity to the gene,
comprehensiveness of genetic workup, and how characteristic the features are."""

_SYNTHESIS = """A variant has been analyzed by the AI-CURA automated pipeline (ACMG/AMP).

Variant: {variant}
Automated Classification (literature-independent only): {classification}
Pathogenic score: {p_score} | Benign score: {b_score}

Criteria evaluated:
{criteria_table}

ClinVar evidence:
{clinvar}

gnomAD allele frequency: {af}

Please provide:
1. A 3-4 sentence clinical interpretation explaining this classification
2. Any conflicting signals a human curator should check
3. Which literature-dependent criteria (PS2/PM6, PS3, PS4, PM3, PP1, PP4, PVS1_RNA)
   could change this result and what evidence would be needed

Note: Literature-dependent criteria are NOT included in the automated score above."""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _criteria_table(result):
    lines = []
    for c in result.criteria:
        if c.code == "CNV_NOTE":
            continue
        lines.append(f"  [{'MET' if c.met else 'not met'}] {c.code} ({c.strength}, {c.direction}): {c.evidence}")
    return "\n".join(lines)


def _clinvar_text(hits):
    if not hits:
        return "No ClinVar entries found."
    out = []
    for h in hits[:3]:
        if h.get("_error"):
            out.append(f"  Error: {h['_error']}")
        else:
            out.append(f"  - {h.get('clinical_significance','unknown')} "
                       f"({h.get('review_status','')}) | {h.get('title','')[:80]}")
    return "\n".join(out)


def _pp1_kb_text():
    if not _PP1_KB:
        return "Knowledgebase not loaded."
    return "\n".join(
        f"  {e['segregation_type']}: {e['points']} pts"
        + (f" - {e['remark']}" if e.get("remark") else "")
        for e in _PP1_KB
    )


def _call_claude(prompt, key, max_tokens=512):
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        system=_SYS,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _call_ollama(prompt, model="deepseek-r1:1.5b", max_tokens=600):
    """Call a locally-running Ollama model. Requires `ollama serve` running.

    DeepSeek-R1 emits its chain-of-thought inside <think>...</think>. We give
    it a generous token budget then strip the reasoning block so the panel
    shows only the final answer.
    """
    if not _REQUESTS_AVAILABLE:
        return "[Ollama backend needs the `requests` package.]"
    import re
    # R1 models need ~2x the budget: half for thinking, half for the answer.
    is_reasoning = "r1" in model.lower() or "reason" in model.lower()
    budget = max_tokens * 3 if is_reasoning else max_tokens
    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "prompt": f"{_SYS}\n\n{prompt}",
                "stream": False,
                "options": {"num_predict": budget, "temperature": 0.2},
            },
            timeout=300,
        )
        r.raise_for_status()
        text = r.json().get("response", "").strip()
        # Strip <think>...</think> reasoning traces
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        if not cleaned and text:
            # Reasoning never closed — return a truncated preview so panel isn't blank
            preview = text.replace("<think>", "").strip()
            return ("[Model used all tokens on internal reasoning. Partial output:]\n\n"
                    + preview[:1500])
        return cleaned or "[Empty response from local model.]"
    except requests.exceptions.ConnectionError:
        return ("[Ollama not reachable at " + OLLAMA_URL + ". "
                "Start it with `ollama serve` or open the Ollama desktop app.]")
    except Exception as e:
        return f"[Ollama call failed: {e}]"


def _dispatch(prompt, backend, api_key, model, max_tokens=600):
    """Route to the right backend. `backend` in {'claude', 'ollama'}."""
    if backend == "ollama":
        return _call_ollama(prompt, model=model or "deepseek-r1:1.5b", max_tokens=max_tokens)
    # default: claude
    if not api_key or not _ANTHROPIC_AVAILABLE:
        return None
    return _call_claude(prompt, api_key, max_tokens=max_tokens)

# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def synthesize(variant_str, acmg_result, clinvar_hits, gnomad_af,
               api_key=None, backend="claude", model=None):
    """Main synthesis: automated criteria + LLM clinical interpretation.

    backend: 'claude' (Anthropic API) or 'ollama' (local model).
    model:   optional model name for the chosen backend.
    """
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    af_str = f"{gnomad_af:.4f}" if gnomad_af is not None else "absent from gnomAD"
    prompt = _SYNTHESIS.format(
        variant=variant_str,
        classification=acmg_result.classification,
        p_score=acmg_result.path_score,
        b_score=acmg_result.benign_score,
        criteria_table=_criteria_table(acmg_result),
        clinvar=_clinvar_text(clinvar_hits),
        af=af_str,
    )
    try:
        out = _dispatch(prompt, backend, key, model, max_tokens=600)
        if out is None:
            return _fallback(acmg_result)
        return out
    except Exception as e:
        return f"[LLM failed: {e}]\n\n{_fallback(acmg_result)}"


# NOTE: superseded by dspy_modules.ExtractLiteratureCriteria -- kept only as a
# reference to the original Ma et al. Supplementary Methods prompt text.
# assess_literature_criteria() / assess_literature_criteria_multi() no longer
# call this or _parse_lit_criteria() below.
_LIT_CRITERIA_PROMPT = """You are applying the literature-DEPENDENT ACMG/AMP criteria for
variant classification. These are the criteria that cannot be scored from databases alone
and require reading a primary paper.

Target variant: {variant}

Read the paper text below. Determine which of these criteria are supported by evidence the
paper's authors explicitly report FOR THIS VARIANT. Do NOT assume, deduce, or use cited work.

Criteria (all are pathogenic-direction):
- PS3       functional assay shows a damaging effect (needs wild-type AND null controls)
- PS4       variant significantly enriched in affected cases vs. controls
- PM3       recessive gene: variant found in trans with a known pathogenic variant
- PS2_PM6   confirmed (PS2) or assumed (PM6) de novo occurrence
- PP1       cosegregation with disease across affected family members
- PP4       phenotype highly specific to this gene / well-characterized workup
- PVS1_RNA  RNA study confirms a splicing loss-of-function effect

Paper text:
\"\"\"
{paper}
\"\"\"

Respond with ONLY a JSON array. For each criterion that APPLIES, output an object:
{{"code": "PS3", "strength": "supporting", "met": true, "evidence": "<one sentence citing the paper>"}}
Allowed strength values: "supporting", "moderate", "strong", "very_strong".
If NO literature-dependent criteria apply, output exactly: []
Output only the JSON array — no preamble, no explanation."""


def _parse_lit_criteria(raw):
    """Parse the LLM's JSON verdict into criterion dicts. Robust to messy output."""
    import re
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except Exception:
        return []
    valid = {"supporting", "moderate", "strong", "very_strong"}
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code", "")).strip()
        if not code:
            continue
        strength = str(it.get("strength", "supporting")).strip().lower()
        if strength not in valid:
            strength = "supporting"
        out.append({
            "code": code,
            "strength": strength,
            "direction": "pathogenic",
            "met": bool(it.get("met", True)),
            "evidence": "[literature/LLM] " + str(it.get("evidence", ""))[:300],
        })
    return out


def assess_literature_criteria(variant_str, paper_text, backend="ollama",
                               model=None, api_key=None):
    """Read a paper, extract literature-dependent ACMG criteria as structured verdicts.

    DSPy-backed (see dspy_modules.py): the output schema is enforced by the
    adapter instead of a hand-rolled regex, so malformed model output gets
    retried against the schema rather than silently dropped.

    Returns (criteria_list, raw_llm_output). criteria_list is a list of dicts ready
    to be folded into the ACMG score via acmg.add_literature_criteria().
    """
    paper = (paper_text or "").strip()
    if not paper:
        return [], "No paper text provided."
    try:
        from .dspy_modules import extract_literature_criteria
    except ImportError:
        return [], "DSPy is not installed -- run `pip install dspy` (see requirements.txt)."
    try:
        _source, criteria, raw = extract_literature_criteria(
            variant_str, paper, backend=backend, model=model, api_key=api_key
        )
    except Exception as e:
        return [], f"[Literature assessment failed: {e}]"
    for c in criteria:
        c["evidence"] = "[literature/LLM] " + c["evidence"]
    return criteria, raw


# ---------------------------------------------------------------------------
# Multi-paper assessment (faithful to AI-CURA: summarize each paper, then
# aggregate; de-duplicate overlapping patient cohorts per fig. S4)
# ---------------------------------------------------------------------------

# NOTE: superseded by dspy_modules.ExtractLiteratureCriteria -- kept only as a
# reference to the original prompt text. Not called by
# assess_literature_criteria_multi() anymore (see _parse_paper() below, also unused).
_PAPER_PROMPT = """You are applying the literature-DEPENDENT ACMG/AMP criteria for one variant,
reading ONE paper at a time.

Target variant: {variant}

Read the paper below. Report only evidence the authors explicitly state FOR THIS VARIANT.
Do NOT assume, deduce, or use cited work.

Criteria (all pathogenic-direction): PS3 (functional, needs WT + null controls), PS4 (case
enrichment), PM3 (in trans with a pathogenic variant), PS2_PM6 (de novo), PP1 (cosegregation),
PP4 (phenotype specificity), PVS1_RNA (RNA splicing loss-of-function).

Also report the study's SOURCE so overlapping patient cohorts can be detected: the first
author's surname and the recruiting institution/hospital if stated.

Paper text:
\"\"\"
{paper}
\"\"\"

Respond with ONLY a JSON object:
{{"source": "<first-author surname> | <institution or hospital or 'unknown'>",
  "criteria": [{{"code": "PS3", "strength": "supporting", "met": true, "evidence": "<one sentence>"}}]}}
Allowed strength: "supporting", "moderate", "strong", "very_strong". Empty list if none apply.
Output only the JSON object."""

_STRENGTH_RANK = {"supporting": 1, "moderate": 2, "strong": 3, "very_strong": 4}
_COUNT_RULES = {"PS4", "PM3"}   # count-based rules most exposed to cohort double-counting


def _parse_paper(raw):
    """Parse one paper's JSON object → (source, criteria_list)."""
    import re
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return "unknown", []
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return "unknown", []
    source = str(obj.get("source", "unknown")).strip() or "unknown"
    valid = {"supporting", "moderate", "strong", "very_strong"}
    crits = []
    for it in obj.get("criteria", []):
        if not isinstance(it, dict):
            continue
        code = str(it.get("code", "")).strip()
        if not code:
            continue
        strength = str(it.get("strength", "supporting")).strip().lower()
        if strength not in valid:
            strength = "supporting"
        crits.append({
            "code": code,
            "strength": strength,
            "direction": "pathogenic",
            "met": bool(it.get("met", True)),
            "evidence": str(it.get("evidence", ""))[:280],
        })
    return source, crits


def _norm_source(s):
    import re
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def assess_literature_criteria_multi(variant_str, papers, backend="ollama",
                                     model=None, api_key=None, dedup=True):
    """Read several papers, extract criteria from each, de-duplicate overlapping
    cohorts, and aggregate into one criteria list.

    papers: list of (name, text).
    Returns (aggregated_criteria, breakdown) where breakdown is a per-paper list of
    {name, source, criteria, dup, dup_of} for display.
    """
    try:
        from .dspy_modules import extract_literature_criteria
    except ImportError:
        return [], [
            {"name": n, "source": "unknown", "criteria": [], "dup": False,
             "dup_of": None, "note": "DSPy not installed"}
            for n, _ in papers
        ]

    breakdown = []
    for name, text in papers:
        text = (text or "").strip()
        if not text:
            breakdown.append({"name": name, "source": "unknown", "criteria": [],
                              "dup": False, "dup_of": None, "note": "empty file"})
            continue
        try:
            source, crits, _raw = extract_literature_criteria(
                variant_str, text, backend=backend, model=model, api_key=api_key
            )
        except Exception as e:
            breakdown.append({"name": name, "source": "unknown", "criteria": [],
                              "dup": False, "dup_of": None, "note": f"error: {e}"})
            continue
        breakdown.append({"name": name, "source": source or "unknown", "criteria": crits,
                          "dup": False, "dup_of": None, "note": None})

    # De-duplicate overlapping cohorts (fig. S4): same normalized source
    if dedup:
        seen = {}
        for pap in breakdown:
            key_s = _norm_source(pap["source"])
            if key_s and key_s != "unknown" and key_s in seen:
                pap["dup"] = True
                pap["dup_of"] = seen[key_s]
            elif key_s and key_s != "unknown":
                seen[key_s] = pap["name"]

    # Aggregate: strongest strength per criterion; count-based rules ignore duplicates
    best = {}
    for pap in breakdown:
        for c in pap["criteria"]:
            code = c["code"]
            if pap["dup"] and code in _COUNT_RULES:
                continue  # avoid double-counting the same cohort
            rank = _STRENGTH_RANK.get(c["strength"], 1)
            if code not in best or rank > best[code]["rank"]:
                best[code] = {"rank": rank, "crit": dict(c), "papers": [pap["name"]]}
            else:
                best[code]["papers"].append(pap["name"])

    aggregated = []
    for code, info in best.items():
        c = info["crit"]
        n = len(info["papers"])
        c["evidence"] = f"[literature/LLM · {n} paper(s)] " + c["evidence"]
        aggregated.append(c)
    return aggregated, breakdown


def assess_pvs1_rna(variant_str, api_key=None):
    """PVS1(RNA): verbatim AI-CURA prompt for splicing evidence from literature."""
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not key or not _ANTHROPIC_AVAILABLE:
        return "Set ANTHROPIC_API_KEY and use --llm to run PVS1(RNA) assessment."
    try:
        return _call_claude(_PVS1_RNA.format(variant=variant_str), key)
    except Exception as e:
        return f"PVS1(RNA) failed: {e}"


def assess_ps3(variant_str, api_key=None):
    """PS3: verbatim AI-CURA prompt for functional experimental evidence."""
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not key or not _ANTHROPIC_AVAILABLE:
        return "Set ANTHROPIC_API_KEY and use --llm to run PS3 assessment."
    try:
        return _call_claude(_PS3.format(variant=variant_str), key)
    except Exception as e:
        return f"PS3 failed: {e}"


def assess_pp1(variant_str, api_key=None):
    """PP1: AI-CURA prompt with PP1 knowledgebase (Table S9) injected."""
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not key or not _ANTHROPIC_AVAILABLE:
        return "Set ANTHROPIC_API_KEY and use --llm to run PP1 assessment."
    try:
        return _call_claude(_PP1.format(variant=variant_str, pp1_kb=_pp1_kb_text()), key)
    except Exception as e:
        return f"PP1 failed: {e}"


def _fallback(result):
    met = [c for c in result.criteria if c.met]
    lines = [
        f"Classification: {result.classification}",
        f"(Literature-independent only: P={result.path_score}, B={result.benign_score})",
        "",
        "Criteria met:" if met else "No criteria met - classified as VUS.",
    ]
    for c in met:
        lines.append(f"  - {c.code}: {c.evidence}")
    lines += [
        "",
        "Literature-dependent criteria (PS2/PM6, PS3, PS4, PM3, PP1, PP4, PVS1_RNA) not assessed.",
        "Set ANTHROPIC_API_KEY and use --llm for full AI-CURA interpretation.",
    ]
    return "\n".join(lines)
