"""
ACMG/AMP criteria engine.
Thresholds from Ma et al. AI-CURA (2026) Supplementary Methods.

Literature-independent criteria:
  PVS1, PS1, PM2_Supporting, PM4, PP2, PP3/PP3_Moderate/PP3_Strong,
  PP5, BA1, BS1, BP4/BP4_Moderate/BP4_Strong/BP4_VeryStrong, BP6, BP7

Literature-dependent criteria (PS2/PM6, PS3, PS4, PM3, PP1, PP4, PVS1_RNA)
are handled by llm.py.

Key sources:
  Richards et al. Genet Med 17:405 (2015) - ACMG/AMP framework
  Pejaver et al. Am J Hum Genet 109:1806 (2022) - REVEL thresholds
  Ma et al. Sci Transl Med (2026) - AI-CURA MAF thresholds
"""

from dataclasses import dataclass, field
from typing import Optional

_LOF = {"stop_gained","frameshift_variant","splice_acceptor_variant",
        "splice_donor_variant","start_lost","transcript_ablation","exon_loss_variant"}
_INFRAME = {"inframe_insertion","inframe_deletion","protein_altering_variant"}
_SYN = {"synonymous_variant","stop_retained_variant"}
_PATH_KW   = {"pathogenic","likely pathogenic"}
_BENIGN_KW = {"benign","likely benign"}

# REVEL thresholds (Pejaver 2022, adopted by AI-CURA)
PP3_STRONG   = 0.932
PP3_MODERATE = 0.773
PP3_MIN      = 0.644
BP4_MAX      = 0.290
BP4_MODERATE = 0.183
BP4_STRONG   = 0.016
BP4_VS       = 0.003

# gnomAD MAF thresholds (Ma et al. 2026)
BA1_AF = 0.05
BS1_AF = 0.01
PM2_AF = 0.005   # PM2_Supporting: MAF <= 0.5%
PP2_Z  = 3.09    # gnomAD v2 missense constraint Z-score


@dataclass
class CriterionResult:
    code: str
    strength: str   # very_strong | strong | moderate | supporting | stand_alone
    direction: str  # pathogenic | benign
    met: bool
    evidence: str


@dataclass
class ACMGResult:
    criteria: list = field(default_factory=list)
    classification: str = "Variant of Uncertain Significance"
    path_score: int = 0
    benign_score: int = 0
    summary: str = ""


def _ctc(vep):
    """Return canonical transcript consequence dict, or {}."""
    for tc in vep.get("transcript_consequences", []):
        if tc.get("canonical") == 1:
            return tc
    return {}


def _cons(vep):
    tc = _ctc(vep)
    c = set(tc.get("consequence_terms", []))
    c.add(vep.get("most_severe_consequence", ""))
    return c


def _parse_exon(tc):
    """Return (exon_number, total_exons) from VEP's 'exon' field like '5/23', or (None, None)."""
    raw = tc.get("exon") or tc.get("intron") or ""
    if "/" in str(raw):
        try:
            n, total = str(raw).split("/")
            return int(n), int(total)
        except (ValueError, TypeError):
            pass
    return None, None


def _check_pvs1(vep):
    """Graded PVS1, approximating ClinGen SVI (Abou Tayoun et al. 2018) / autoPVS1.

    Strength is downgraded when the predicted null variant likely escapes
    nonsense-mediated decay (NMD) — i.e. sits in the last exon or the last
    ~50 nt of the penultimate exon — since those may leave a partly functional
    protein. Full autoPVS1 needs exact transcript coordinates; this uses the
    exon position VEP provides.
    """
    cons = _cons(vep)
    tc = _ctc(vep)
    exon_n, exon_total = _parse_exon(tc)

    # Canonical ±1,2 splice sites — strongest LoF signal
    if cons & {"splice_acceptor_variant", "splice_donor_variant"}:
        return CriterionResult("PVS1", "very_strong", "pathogenic", True,
                               "Canonical ±1/2 splice site. (Full autoPVS1 checks reading-frame/reinitiation.)")

    # Nonsense / frameshift — grade by NMD escape
    if cons & {"stop_gained", "frameshift_variant"}:
        if exon_n and exon_total and exon_n == exon_total:  # last exon → likely escapes NMD
            return CriterionResult("PVS1_Strong", "strong", "pathogenic", True,
                                   f"Truncating in last exon ({exon_n}/{exon_total}) — likely escapes NMD; downgraded to Strong.")
        if exon_n and exon_total and exon_n == exon_total - 1:  # penultimate exon (approx last-50nt rule)
            return CriterionResult("PVS1_Strong", "strong", "pathogenic", True,
                                   f"Truncating in penultimate exon ({exon_n}/{exon_total}) — may escape NMD; downgraded to Strong.")
        loc = f" (exon {exon_n}/{exon_total})" if exon_n else ""
        return CriterionResult("PVS1", "very_strong", "pathogenic", True,
                               f"Truncating variant predicted to trigger NMD{loc}.")

    # Start-loss — ClinGen SVI caps initiation-codon variants at Moderate
    if "start_lost" in cons:
        return CriterionResult("PVS1_Moderate", "moderate", "pathogenic", True,
                               "Initiation-codon loss (start_lost) — capped at Moderate per ClinGen SVI.")

    # Whole-transcript ablation / exon loss
    if cons & {"transcript_ablation", "exon_loss_variant"}:
        return CriterionResult("PVS1", "very_strong", "pathogenic", True,
                               f"{', '.join(cons & {'transcript_ablation','exon_loss_variant'})}.")

    return CriterionResult("PVS1", "very_strong", "pathogenic", False,
                           f"No predicted LoF ({vep.get('most_severe_consequence','unknown')}).")


def _check_pm4(vep):
    hits = _cons(vep) & _INFRAME
    met = bool(hits)
    return CriterionResult(
        "PM4", "moderate", "pathogenic", met,
        f"In-frame change: {', '.join(hits)}" if met else "No in-frame protein-length change."
    )


def _check_pm2_supporting(gnomad):
    af = gnomad.get("af")
    if af is None:
        return CriterionResult("PM2_Supporting","supporting","pathogenic",True,
                               "Variant absent from gnomAD.")
    met = af <= PM2_AF
    return CriterionResult(
        "PM2_Supporting","supporting","pathogenic", met,
        f"gnomAD AF = {af:.4f} ({'<=' if met else '>'} {PM2_AF})"
    )


def _check_ba1(gnomad):
    af = gnomad.get("af")
    met = af is not None and af > BA1_AF
    return CriterionResult("BA1","stand_alone","benign", met,
                           f"gnomAD AF = {af:.4f}" if af is not None else "Not in gnomAD.")


def _check_bs1(gnomad):
    af = gnomad.get("af")
    met = af is not None and af > BS1_AF
    return CriterionResult("BS1","strong","benign", met,
                           f"gnomAD AF = {af:.4f}" if af is not None else "Not in gnomAD.")


def _check_revel(vep):
    """PP3/BP4 via REVEL (graduated). Falls back to SIFT/PolyPhen."""
    tc = _ctc(vep)
    revel = tc.get("revel_score") or tc.get("revel") or vep.get("revel_score")
    sift  = tc.get("sift_prediction")
    poly  = tc.get("polyphen_prediction")

    if revel is not None:
        try:
            r = float(revel)
            if r >= PP3_STRONG:
                pp3 = CriterionResult("PP3_Strong","strong","pathogenic",True,f"REVEL={r:.3f}>={PP3_STRONG}")
            elif r >= PP3_MODERATE:
                pp3 = CriterionResult("PP3_Moderate","moderate","pathogenic",True,f"REVEL={r:.3f}>={PP3_MODERATE}")
            elif r >= PP3_MIN:
                pp3 = CriterionResult("PP3","supporting","pathogenic",True,f"REVEL={r:.3f}>={PP3_MIN}")
            else:
                pp3 = CriterionResult("PP3","supporting","pathogenic",False,f"REVEL={r:.3f}<{PP3_MIN}")

            if r <= BP4_VS:
                bp4 = CriterionResult("BP4_VeryStrong","very_strong","benign",True,f"REVEL={r:.3f}<={BP4_VS}")
            elif r <= BP4_STRONG:
                bp4 = CriterionResult("BP4_Strong","strong","benign",True,f"REVEL={r:.3f}<={BP4_STRONG}")
            elif r <= BP4_MODERATE:
                bp4 = CriterionResult("BP4_Moderate","moderate","benign",True,f"REVEL={r:.3f}<={BP4_MODERATE}")
            elif r <= BP4_MAX:
                bp4 = CriterionResult("BP4","supporting","benign",True,f"REVEL={r:.3f}<={BP4_MAX}")
            else:
                bp4 = CriterionResult("BP4","supporting","benign",False,f"REVEL={r:.3f}>{BP4_MAX}")
            return pp3, bp4
        except (TypeError, ValueError):
            pass

    # Fallback
    note = f"(REVEL N/A; SIFT={sift or 'N/A'}, PolyPhen={poly or 'N/A'})"
    dam = sum([sift=="deleterious", poly in ("probably_damaging","possibly_damaging")])
    ben = sum([sift=="tolerated",   poly=="benign"])
    pp3 = CriterionResult("PP3","supporting","pathogenic",dam>=2,
                          f"Both predict damaging {note}" if dam>=2 else f"Insufficient {note}")
    bp4 = CriterionResult("BP4","supporting","benign",ben>=2,
                          f"Both predict benign {note}" if ben>=2 else f"Insufficient {note}")
    return pp3, bp4


def _check_pp2(vep):
    """PP2: Missense in high-constraint gene (gnomAD Z >= 3.09)."""
    tc = _ctc(vep)
    if "missense_variant" not in tc.get("consequence_terms", []):
        return CriterionResult("PP2","supporting","pathogenic",False,
                               "Not a missense variant - PP2 N/A.")
    mis_z = tc.get("gene_pheno") or tc.get("mis_z")
    if mis_z is not None:
        try:
            z = float(mis_z)
            met = z >= PP2_Z
            return CriterionResult("PP2","supporting","pathogenic",met,
                                   f"Missense constraint Z={z:.2f} ({'meets' if met else 'below'} {PP2_Z})")
        except (TypeError, ValueError):
            pass
    return CriterionResult("PP2","supporting","pathogenic",False,
                           f"Missense but Z-score unavailable. Check gnomAD browser (Z>={PP2_Z}).")


def _check_bp7(vep):
    """BP7: Synonymous + SpliceAI < 0.2."""
    tc = _ctc(vep)
    if not (set(tc.get("consequence_terms",[])) & _SYN):
        return CriterionResult("BP7","supporting","benign",False,
                               "Not synonymous - BP7 N/A.")
    spliceai = tc.get("spliceai_score") or tc.get("spliceai_pred")
    if spliceai is not None:
        try:
            score = max(float(x) for x in str(spliceai).split(",") if x.strip())
            met = score < 0.2
            return CriterionResult("BP7","supporting","benign",met,
                                   f"Synonymous, SpliceAI={score:.3f} ({'<' if met else '>='} 0.2)")
        except (TypeError, ValueError):
            pass
    return CriterionResult("BP7","supporting","benign",False,
                           "Synonymous but SpliceAI unavailable. May apply if <0.2.")


def _check_pp5_bp6(hits):
    sigs = [h.get("clinical_significance","").lower() for h in hits if not h.get("_error")]
    revs = [h.get("review_status","") for h in hits if not h.get("_error")]
    best = revs[0] if revs else "not found"
    path_found  = any(any(k in s for k in _PATH_KW)   for s in sigs)
    benign_found = any(any(k in s for k in _BENIGN_KW) for s in sigs)
    pp5 = CriterionResult("PP5","supporting","pathogenic",path_found,
                          f"ClinVar: {'pathogenic entry' if path_found else 'no pathogenic'} (review: {best})")
    bp6 = CriterionResult("BP6","supporting","benign",benign_found,
                          f"ClinVar: {'benign entry' if benign_found else 'no benign'} (review: {best})")
    return pp5, bp6


def _check_ps1(hits, hgvs_p):
    if not hgvs_p:
        return CriterionResult("PS1","strong","pathogenic",False,
                               "No protein change for comparison.")
    met = any(any(k in h.get("clinical_significance","").lower() for k in _PATH_KW)
              for h in hits if not h.get("_error"))
    return CriterionResult("PS1","strong","pathogenic",met,
                           f"ClinVar {'has' if met else 'lacks'} pathogenic entry for {hgvs_p}.")


_SCORES = {"very_strong":8,"strong":4,"moderate":2,"supporting":1,"stand_alone":99}


def _classify(criteria):
    path_met  = [c for c in criteria if c.met and c.direction=="pathogenic"]
    benign_met = [c for c in criteria if c.met and c.direction=="benign"]
    p = sum(_SCORES.get(c.strength,1) for c in path_met)
    b = sum(_SCORES.get(c.strength,1) for c in benign_met)

    if any(c.code=="BA1" for c in benign_met):
        return "Benign", p, b
    if p >= 10:  label = "Pathogenic"
    elif p >= 6: label = "Likely Pathogenic"
    elif b >= 8: label = "Benign"
    elif b >= 4: label = "Likely Benign"
    else:        label = "Variant of Uncertain Significance"
    return label, p, b


def add_literature_criteria(result, extra):
    """Fold LLM-assessed literature-dependent criteria into an existing result
    and re-run classification. `extra` is a list of dicts from
    llm.assess_literature_criteria(). Returns the updated ACMGResult in place."""
    added = []
    for e in extra:
        crit = CriterionResult(
            code=e["code"],
            strength=e.get("strength", "supporting"),
            direction=e.get("direction", "pathogenic"),
            met=e.get("met", True),
            evidence=e.get("evidence", ""),
        )
        result.criteria.append(crit)
        added.append(crit)
    cls, p, b = _classify(result.criteria)
    result.classification = cls
    result.path_score = p
    result.benign_score = b
    met = [c.code for c in result.criteria if c.met]
    result.summary = (f"Classification: {cls}. Met: {', '.join(met) if met else 'none'}. "
                      f"P={p}, B={b}.")
    return result, added


def apply_criteria(vep, gnomad, clinvar_hits, hgvs_p=None):
    """Run all literature-independent ACMG criteria."""
    criteria = [
        _check_pvs1(vep),
        _check_ps1(clinvar_hits, hgvs_p),
        _check_pm2_supporting(gnomad),
        _check_pm4(vep),
        _check_pp2(vep),
    ]
    pp3, bp4 = _check_revel(vep)
    criteria += [pp3, bp4]
    pp5, bp6 = _check_pp5_bp6(clinvar_hits)
    criteria += [pp5, bp6, _check_ba1(gnomad), _check_bs1(gnomad), _check_bp7(vep)]

    cls, p, b = _classify(criteria)
    met = [c.code for c in criteria if c.met]
    return ACMGResult(
        criteria=criteria, classification=cls, path_score=p, benign_score=b,
        summary=f"Classification: {cls}. Met: {', '.join(met) if met else 'none'}. P={p}, B={b}."
    )
