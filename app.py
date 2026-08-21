"""
AI-CURA Streamlit Dashboard
Run with: streamlit run app.py
"""

import os
import sys
import streamlit as st
import pandas as pd
from pathlib import Path

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.classifier import VariantClassifier
from src.variant import parse_variant, VariantType

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="AI-CURA | Variant Classification",
    page_icon="🧬",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
CLASSIFICATION_COLORS = {
    "Pathogenic":                      "#c0392b",
    "Likely Pathogenic":               "#e67e22",
    "Variant of Uncertain Significance": "#f1c40f",
    "Likely Benign":                   "#27ae60",
    "Benign":                          "#1a8a4a",
    "Requires Manual Review (CNV)":    "#8e44ad",
}

EXAMPLE_VARIANTS = {
    "BRCA1 frameshift (Pathogenic)":     "NM_007294.4:c.5266dupC",
    "BRCA1 via rsID":                    "rs80357906",
    "VCF format":                        "chr17-41234470-G-A",
    "CNV deletion — RAI1 (Pathogenic)":  "chr17-17584000-19000000-DEL",
}

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    _dna_icon_path = Path(__file__).parent / "assets" / "dna_icon.svg"
    if _dna_icon_path.exists():
        st.markdown(
            f"<div style='width:64px;'>{_dna_icon_path.read_text()}</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown("<div style='font-size:44px; line-height:1;'>🧬</div>", unsafe_allow_html=True)
    st.title("AI-CURA")
    st.caption("Automated LLM workflow for genetic variant classification")
    st.divider()

    st.subheader("Settings")
    genome = st.selectbox("Reference genome", ["GRCh38", "GRCh37"])
    use_llm = st.toggle("Enable LLM synthesis", value=False,
                        help="Off = rule-based only. On = call one or more LLMs.")

    LOCAL_MODELS = [
        "deepseek-r1:1.5b", "llama3.2:3b", "qwen2.5:3b",
        "gemma2:2b", "phi3.5", "deepseek-r1:7b", "llama3.1:8b",
    ]

    backend_choice = st.radio(
        "LLM backend",
        ["Single local model", "Compare two local models", "Claude (API)"],
        index=0,
        disabled=not use_llm,
        help="Run one local model, compare two side-by-side, or use the Claude API.",
    )

    ollama_model = None
    model_a = model_b = None

    if backend_choice == "Single local model":
        ollama_model = st.selectbox(
            "Local model", LOCAL_MODELS, index=0, disabled=not use_llm,
            help="Model tag as installed via `ollama pull <name>`.",
        )
    elif backend_choice == "Compare two local models":
        model_a = st.selectbox("Model A", LOCAL_MODELS, index=0, disabled=not use_llm)
        model_b = st.selectbox("Model B", LOCAL_MODELS, index=1, disabled=not use_llm)
        ollama_model = model_a

    if use_llm and backend_choice == "Claude (API)" \
            and (not os.getenv("ANTHROPIC_API_KEY")
                 or os.getenv("ANTHROPIC_API_KEY", "").startswith("your_")):
        st.warning("ANTHROPIC_API_KEY not set — Claude will fall back to rule-based.")

    if use_llm and "local" in backend_choice:
        st.caption("Local models require Ollama running and the model pulled "
                   "(`ollama pull <name>`). R1 models are slower (chain-of-thought).")

    st.divider()
    st.subheader("Quick examples")
    for label, variant in EXAMPLE_VARIANTS.items():
        if st.button(label, use_container_width=True):
            st.session_state["variant_input"] = variant

    st.divider()
    st.caption(
        "Based on: Ma et al. *AI-CURA*, "
        "Science Translational Medicine (2026)  \n"
        "ACMG/AMP guidelines: Richards et al. (2015)  \n"
        "REVEL thresholds: Pejaver et al. (2022)"
    )

# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------
st.title("🧬 AI-CURA Variant Classifier")
st.markdown(
    "Enter a variant below to classify it using **ACMG/AMP guidelines** "
    "with evidence from gnomAD, ClinVar, and Ensembl VEP."
)

col_input, col_btn = st.columns([4, 1])
with col_input:
    variant_input = st.text_input(
        "Variant",
        value=st.session_state.get("variant_input", ""),
        placeholder="e.g. NM_007294.4:c.5266dupC  |  rs80357906  |  chr17-41234470-G-A  |  chr17-1000000-5000000-DEL",
        label_visibility="collapsed",
    )
with col_btn:
    run = st.button("Classify", type="primary", use_container_width=True)

# Optional paper upload → lets the LLM score literature-dependent criteria.
# Multiple papers are supported (as in AI-CURA): each is summarized separately,
# overlapping cohorts are de-duplicated, then the evidence is aggregated.
paper_files = st.file_uploader(
    "Optional: upload one or more primary papers (PDF or .txt) so the LLM can score "
    "literature-dependent criteria (PS3, PS4, PM3, PP1, PP4, PVS1_RNA) and fold them "
    "into the classification.",
    type=["pdf", "txt"],
    accept_multiple_files=True,
    disabled=not use_llm,
    help="Requires LLM synthesis enabled. Each paper is read separately, duplicate "
         "cohorts are removed, then evidence is aggregated. Without any paper, only "
         "the automated (literature-independent) criteria are scored.",
)
dedup_on = st.checkbox(
    "De-duplicate overlapping patient cohorts (recommended for multiple papers)",
    value=True, disabled=not use_llm,
    help="Mirrors AI-CURA fig. S4: papers sharing an author/institution are treated as "
         "the same cohort so count-based rules (PS4, PM3) aren't double-counted.",
)


def _extract_paper_text(uploaded) -> str:
    if uploaded is None:
        return ""
    if uploaded.name.lower().endswith(".txt"):
        return uploaded.getvalue().decode("utf-8", errors="ignore")
    try:
        from pypdf import PdfReader
        reader = PdfReader(uploaded)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:
        st.warning(f"Could not read {uploaded.name} ({e}). Try a .txt instead.")
        return ""

if not run and "last_result" not in st.session_state:
    st.info("Enter a variant above and click **Classify** to begin.")
    st.stop()

# ---------------------------------------------------------------------------
# Run classification
# ---------------------------------------------------------------------------
if run and variant_input:
    st.session_state.pop("last_result", None)
    st.session_state.pop("compare_reports", None)
    with st.spinner(f"Classifying {variant_input} …  (local models can take 1–2 min on CPU)"):
        try:
            if use_llm and backend_choice == "Compare two local models":
                # Run automated pipeline once, then call each model for interpretation.
                base = VariantClassifier(genome=genome, use_llm=False).classify(variant_input)
                from src.llm import synthesize
                af_val = base.gnomad_data.get("af")
                out_a = synthesize(
                    variant_str=base.variant.raw_input, acmg_result=base.acmg_result,
                    clinvar_hits=base.clinvar_hits, gnomad_af=af_val,
                    backend="ollama", model=model_a,
                )
                out_b = synthesize(
                    variant_str=base.variant.raw_input, acmg_result=base.acmg_result,
                    clinvar_hits=base.clinvar_hits, gnomad_af=af_val,
                    backend="ollama", model=model_b,
                )
                st.session_state["last_result"] = base
                st.session_state["compare_reports"] = {model_a: out_a, model_b: out_b}
            else:
                backend = "ollama" if backend_choice == "Single local model" else "claude"
                classifier = VariantClassifier(
                    genome=genome, use_llm=use_llm,
                    backend=backend, model=ollama_model,
                )
                report = classifier.classify(variant_input)

                # Fold literature-dependent criteria into the score if papers were uploaded
                st.session_state.pop("lit_added", None)
                st.session_state.pop("lit_before", None)
                st.session_state.pop("lit_breakdown", None)
                if use_llm and paper_files:
                    papers = [(f.name, _extract_paper_text(f)) for f in paper_files]
                    papers = [(n, t) for n, t in papers if t.strip()]
                    if papers:
                        from src.llm import assess_literature_criteria_multi
                        from src.acmg import add_literature_criteria
                        before = report.acmg_result.classification
                        extra, breakdown = assess_literature_criteria_multi(
                            variant_str=report.variant.raw_input,
                            papers=papers,
                            backend=backend,
                            model=ollama_model,
                            dedup=dedup_on,
                        )
                        st.session_state["lit_breakdown"] = breakdown
                        if extra:
                            _, added = add_literature_criteria(report.acmg_result, extra)
                            st.session_state["lit_added"] = [
                                (c.code, c.strength, c.met, c.evidence) for c in added
                            ]
                            st.session_state["lit_before"] = before

                st.session_state["last_result"] = report
        except ValueError as e:
            st.error(f"Could not parse variant: {e}")
            st.stop()
        except Exception as e:
            st.error(f"Classification error: {e}")
            st.stop()

report = st.session_state.get("last_result")
if not report:
    st.stop()

acmg = report.acmg_result
cls  = acmg.classification
color = CLASSIFICATION_COLORS.get(cls, "#555")

# ---------------------------------------------------------------------------
# Classification banner
# ---------------------------------------------------------------------------
st.markdown(f"""
<div style="background:{color}18; border-left:6px solid {color};
            padding:16px 20px; border-radius:6px; margin:12px 0;">
  <span style="font-size:0.85rem; color:{color}; font-weight:600; text-transform:uppercase; letter-spacing:.05em;">
    Classification
  </span><br>
  <span style="font-size:1.8rem; font-weight:700; color:{color};">{cls}</span>
  <span style="float:right; font-size:0.9rem; color:#666; margin-top:10px;">
    Pathogenic score: <b>{acmg.path_score}</b> &nbsp;|&nbsp; Benign score: <b>{acmg.benign_score}</b>
  </span>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Literature-dependent evidence panel (if a paper was scored)
# ---------------------------------------------------------------------------
lit_added = st.session_state.get("lit_added")
if lit_added:
    before = st.session_state.get("lit_before", "")
    changed = before and before != cls
    header = (f"📄 Literature evidence changed the classification: "
              f"**{before} → {cls}**" if changed
              else "📄 Literature evidence applied (classification unchanged)")
    with st.container(border=True):
        st.markdown(header)
        for code, strength, met, evidence in lit_added:
            icon = "✅" if met else "⬜"
            st.markdown(f"{icon} **{code}** ({strength.replace('_',' ')}) — {evidence}")
        st.caption("These literature-dependent criteria were extracted by the LLM from your "
                   "uploaded paper(s) and folded into the ACMG score. Verify before any real use.")

# Per-paper breakdown (shown whenever papers were processed, even if no criteria met)
lit_breakdown = st.session_state.get("lit_breakdown")
if lit_breakdown:
    dups = [b for b in lit_breakdown if b.get("dup")]
    with st.expander(f"Per-paper evidence  ({len(lit_breakdown)} paper(s)"
                     + (f", {len(dups)} duplicate cohort(s) excluded)" if dups else ")")):
        if dups:
            st.warning("Duplicate cohorts detected — count-based rules (PS4, PM3) were not "
                       "double-counted for: " + ", ".join(f"**{b['name']}**" for b in dups))
        for b in lit_breakdown:
            tag = " · ⚠️ duplicate cohort" if b.get("dup") else ""
            src = b.get("source", "unknown")
            st.markdown(f"**{b['name']}**  —  source: _{src}_{tag}")
            if b.get("note"):
                st.caption(f"  ({b['note']})")
            if b["criteria"]:
                for c in b["criteria"]:
                    st.markdown(f"&nbsp;&nbsp;• **{c['code']}** ({c['strength'].replace('_',' ')}) — {c['evidence']}")
            else:
                st.caption("  No literature-dependent criteria found in this paper.")

# ---------------------------------------------------------------------------
# Variant info + gnomAD row
# ---------------------------------------------------------------------------
v = report.variant
col1, col2, col3, col4 = st.columns(4)
col1.metric("Gene",      v.gene or "—")
col2.metric("Consequence", v.consequence or "—")
col3.metric("HGVS p.",   v.hgvs_p or "—")
af = report.gnomad_data.get("af")
col4.metric("gnomAD AF", f"{af:.2e}" if af is not None else "absent")

st.divider()

# ---------------------------------------------------------------------------
# Two-column layout: criteria + evidence
# ---------------------------------------------------------------------------
left, right = st.columns([3, 2])

with left:
    st.subheader("ACMG/AMP Criteria")

    rows = []
    for c in acmg.criteria:
        if c.code == "CNV_NOTE":
            continue
        rows.append({
            "Code":      c.code,
            "Met":       "✓" if c.met else "✗",
            "Strength":  c.strength.replace("_", " "),
            "Direction": c.direction,
            "Evidence":  c.evidence,
        })

    df = pd.DataFrame(rows)

    def style_row(row):
        if row["Met"] == "✓" and row["Direction"] == "pathogenic":
            return ["background-color:#fdecea"] * len(row)
        if row["Met"] == "✓" and row["Direction"] == "benign":
            return ["background-color:#e8f5e9"] * len(row)
        return ["color:#aaa"] * len(row)

    styled = df.style.apply(style_row, axis=1).hide(axis="index")
    st.dataframe(styled, use_container_width=True, height=420)

with right:
    st.subheader("ClinVar Evidence")
    hits = report.clinvar_hits
    if not hits or hits[0].get("_error"):
        st.info("No ClinVar entries found.")
    else:
        for h in hits[:5]:
            sig = h.get("clinical_significance", "unknown")
            sig_color = ("#c0392b" if "pathogenic" in sig.lower()
                         else "#27ae60" if "benign" in sig.lower()
                         else "#888")
            st.markdown(
                f'<div style="border-left:4px solid {sig_color}; padding:8px 12px; '
                f'margin-bottom:8px; background:#fafafa; border-radius:4px;">'
                f'<b style="color:{sig_color}">{sig}</b><br>'
                f'<span style="font-size:0.8rem; color:#666">{h.get("review_status","")}</span><br>'
                f'<span style="font-size:0.82rem">{h.get("title","")[:100]}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    from src.variant import VariantType
    is_cnv = getattr(report.variant, "variant_type", None) == VariantType.CNV

    st.subheader("Dosage / Frequency" if is_cnv else "gnomAD Frequency")
    gd = report.gnomad_data
    if is_cnv:
        st.info("CNV scored with the ClinGen dosage framework (Section 2 — overlap with "
                "established HI/TS genes). SNV frequency criteria (PM2/BA1/BS1) do not apply. "
                "Full curation: ClinGen CNV calculator.")
    elif gd.get("af") is not None:
        af_val = gd["af"]
        # Gauge: log scale from 1e-6 to 1
        import math
        pct = max(0.0, min(1.0, (math.log10(af_val + 1e-8) + 8) / 8))
        bar_color = "#c0392b" if af_val > 0.05 else "#e67e22" if af_val > 0.005 else "#27ae60"
        st.markdown(
            f'<div style="background:#eee; border-radius:4px; height:12px; margin:4px 0;">'
            f'<div style="background:{bar_color}; width:{pct*100:.1f}%; height:12px; border-radius:4px;"></div>'
            f'</div>'
            f'<span style="font-size:0.85rem">AF = {af_val:.2e} &nbsp; '
            f'(source: {gd.get("source","gnomAD")})</span>',
            unsafe_allow_html=True,
        )
        thresholds = [
            ("BA1 (Benign stand-alone)", 0.05,  af_val > 0.05),
            ("BS1 (Benign strong)",      0.01,  af_val > 0.01),
            ("PM2_Supporting",           0.005, af_val <= 0.005),
        ]
        for label, thr, applies in thresholds:
            icon = "🟢" if applies else "⚪"
            st.markdown(f"{icon} {label} (threshold: {thr})")
    else:
        st.success("Variant absent from gnomAD → **PM2_Supporting** applies")

st.divider()

# ---------------------------------------------------------------------------
# Interpretation panel
# ---------------------------------------------------------------------------
st.subheader("Clinical Interpretation")
compare = st.session_state.get("compare_reports")
if compare:
    st.caption("Side-by-side interpretation from both LLMs using the identical AI-CURA synthesis prompt.")
    cmp_cols = st.columns(len(compare))
    for col, (name, text) in zip(cmp_cols, compare.items()):
        with col:
            st.markdown(f"**{name}**")
            st.markdown(
                f'<div style="background:#f8f9fa; border:1px solid #dee2e6; padding:14px; '
                f'border-radius:6px; white-space:pre-wrap; font-size:0.85rem; line-height:1.55; '
                f'max-height:520px; overflow:auto;">{text}</div>',
                unsafe_allow_html=True,
            )
elif use_llm:
    st.markdown(f"*AI-generated using the AI-CURA synthesis prompt ({backend_choice})*")
    st.markdown(
        f'<div style="background:#f8f9fa; border:1px solid #dee2e6; padding:16px; '
        f'border-radius:6px; white-space:pre-wrap; font-size:0.9rem; line-height:1.6;">'
        f'{report.interpretation}</div>',
        unsafe_allow_html=True,
    )
else:
    st.caption("Rule-based summary. Enable **LLM synthesis** in the sidebar for an AI-generated interpretation.")
    st.markdown(
        f'<div style="background:#f8f9fa; border:1px solid #dee2e6; padding:16px; '
        f'border-radius:6px; white-space:pre-wrap; font-size:0.9rem; line-height:1.6;">'
        f'{report.interpretation}</div>',
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------
with st.expander("Export raw JSON"):
    import json
    st.code(json.dumps(report.to_dict(), indent=2, default=str), language="json")

st.divider()
st.caption(
    "⚠️ For research and educational use only. "
    "Not validated for clinical decision-making. "
    "Literature-dependent criteria (PS3, PS4, PM3, PP1, PP4, PVS1_RNA) require manual review."
)
