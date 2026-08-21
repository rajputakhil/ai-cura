"""
Main classification orchestrator.
Pulls together VEP, gnomAD, ClinVar, ACMG logic, and LLM synthesis.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from .variant import Variant, VariantType, parse_variant
from .apis import vep_annotate, vep_annotate_vcf, gnomad_frequency, clinvar_search_hgvs, clinvar_search_rsid
from .acmg import apply_criteria, ACMGResult
from .llm import synthesize


@dataclass
class ClassificationReport:
    variant: Variant
    vep_data: dict
    gnomad_data: dict
    clinvar_hits: list[dict]
    acmg_result: ACMGResult
    interpretation: str

    def to_dict(self) -> dict:
        return {
            "variant": self.variant.raw_input,
            "gene": self.variant.gene,
            "hgvs_c": self.variant.hgvs_c,
            "hgvs_p": self.variant.hgvs_p,
            "consequence": self.variant.consequence,
            "gnomad_af": self.gnomad_data.get("af"),
            "classification": self.acmg_result.classification,
            "criteria": [
                {
                    "code": c.code,
                    "met": c.met,
                    "strength": c.strength,
                    "direction": c.direction,
                    "evidence": c.evidence,
                }
                for c in self.acmg_result.criteria
            ],
            "interpretation": self.interpretation,
        }


class VariantClassifier:
    def __init__(self, genome: str = "GRCh38", use_llm: bool = False,
                 backend: str = "claude", model: str | None = None):
        self.genome = genome
        self.use_llm = use_llm
        self.backend = backend
        self.model = model

    def classify(self, raw_input: str) -> ClassificationReport:
        variant = parse_variant(raw_input)

        if variant.variant_type == VariantType.CNV:
            return self._classify_cnv(variant)
        else:
            return self._classify_snv_indel(variant)

    # ------------------------------------------------------------------
    # SNV / INDEL
    # ------------------------------------------------------------------

    def _classify_snv_indel(self, variant: Variant) -> ClassificationReport:
        # 1. VEP annotation
        vep = self._run_vep(variant)
        self._enrich_variant_from_vep(variant, vep)

        # 2. gnomAD frequency
        gnomad = {}
        if variant.chromosome and variant.position and variant.ref and variant.alt:
            gnomad = gnomad_frequency(
                variant.chromosome, variant.position,
                variant.ref, variant.alt, self.genome
            )

        # 3. ClinVar
        clinvar_hits = self._run_clinvar(variant)

        # 4. ACMG criteria
        acmg = apply_criteria(vep, gnomad, clinvar_hits, variant.hgvs_p)

        # 5. LLM synthesis
        interp = synthesize(
            variant_str=variant.raw_input,
            acmg_result=acmg,
            clinvar_hits=clinvar_hits,
            gnomad_af=gnomad.get("af"),
            backend=self.backend,
            model=self.model,
        ) if self.use_llm else _rule_based_summary(acmg)

        return ClassificationReport(
            variant=variant,
            vep_data=vep,
            gnomad_data=gnomad,
            clinvar_hits=clinvar_hits,
            acmg_result=acmg,
            interpretation=interp,
        )

    def _run_vep(self, variant: Variant) -> dict:
        if variant.hgvs_c:
            return vep_annotate(variant.hgvs_c, self.genome)
        if variant.rsid:
            return vep_annotate(variant.rsid, self.genome)
        if variant.chromosome and variant.position and variant.ref and variant.alt:
            return vep_annotate_vcf(
                variant.chromosome, variant.position,
                variant.ref, variant.alt, self.genome
            )
        return {}

    def _enrich_variant_from_vep(self, variant: Variant, vep: dict) -> None:
        """Populate variant fields from VEP response."""
        if not vep or vep.get("_error"):
            return

        variant.most_severe_consequence = vep.get("most_severe_consequence")
        variant.consequence = vep.get("most_severe_consequence")

        # Pull from canonical transcript
        for tc in vep.get("transcript_consequences", []):
            if tc.get("canonical") == 1:
                variant.gene = tc.get("gene_symbol") or variant.gene
                variant.transcript = tc.get("transcript_id") or variant.transcript
                variant.hgvs_c = tc.get("hgvs_c") or variant.hgvs_c
                variant.hgvs_p = tc.get("hgvs_p")
                break

        # Genomic coordinates from VEP if not already set
        if not variant.chromosome:
            seq_name = vep.get("seq_region_name", "")
            variant.chromosome = seq_name
        if not variant.position:
            variant.position = vep.get("start")

    def _run_clinvar(self, variant: Variant) -> list[dict]:
        if variant.rsid:
            return clinvar_search_rsid(variant.rsid)
        if variant.hgvs_c:
            return clinvar_search_hgvs(variant.hgvs_c, variant.gene)
        return []

    # ------------------------------------------------------------------
    # CNV (simplified — ClinVar lookup + basic summary)
    # ------------------------------------------------------------------

    def _classify_cnv(self, variant: Variant) -> ClassificationReport:
        from .cnv import score_cnv
        from .acmg import ACMGResult, CriterionResult

        # ClinGen dosage scoring (Section 2)
        cnv = score_cnv(variant.chromosome, variant.start, variant.end, variant.cnv_type)

        # Populate variant fields for the dashboard metrics
        variant.consequence = "copy-number loss" if cnv["is_loss"] else "copy-number gain"
        if cnv["overlaps"]:
            variant.gene = cnv["overlaps"][0]["name"]

        # Build criteria from the dosage overlaps
        dkey = "HI" if cnv["is_loss"] else "TS"
        criteria = []
        for o in cnv["overlaps"]:
            direction = "pathogenic" if o["points"] > 0 else "benign"
            met = o["points"] != 0.0
            criteria.append(CriterionResult(
                code=f"Section2 ({o['name']})",
                strength="stand_alone" if o["points"] >= 0.99 else "supporting",
                direction=direction,
                met=met,
                evidence=(f"{dkey}={o['dosage_score']} "
                          f"({'complete' if o['complete'] else 'partial'} overlap; "
                          f"{o['points']:+.2f} pts) — {o['condition']}"),
            ))
        if not criteria:
            criteria.append(CriterionResult(
                code="Section2", strength="supporting", direction="pathogenic", met=False,
                evidence="No overlap with an established dosage-sensitive gene/region.",
            ))

        acmg = ACMGResult(
            criteria=criteria,
            classification=cnv["classification"],
            path_score=round(max(cnv["score"], 0.0) * 10),
            benign_score=round(max(-cnv["score"], 0.0) * 10),
            summary=cnv["summary"],
        )

        # Supplementary ClinVar region lookup (kept for extra evidence)
        from .apis import clinvar_search
        query = (
            f'"{variant.cnv_type}"[All Fields] AND '
            f'"chr{variant.chromosome}"[All Fields] AND '
            f'"copy number"[All Fields]'
        )
        try:
            clinvar_hits = clinvar_search(query)[:3]
        except Exception as e:
            clinvar_hits = [{"_error": str(e)}]

        return ClassificationReport(
            variant=variant,
            vep_data={},
            gnomad_data={},
            clinvar_hits=clinvar_hits,
            acmg_result=acmg,
            interpretation=cnv["summary"],
        )


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _rule_based_summary(result: ACMGResult) -> str:
    met = [c for c in result.criteria if c.met]
    lines = [f"Classification: {result.classification}", ""]
    if met:
        lines.append("Evidence (criteria met):")
        for c in met:
            lines.append(f"  • {c.code}: {c.evidence}")
    else:
        lines.append("No ACMG criteria were met — classified as VUS.")
    lines.append(
        "\nTip: run with --llm for an AI-generated clinical interpretation."
    )
    return "\n".join(lines)
