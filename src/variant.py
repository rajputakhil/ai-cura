"""Variant data model and input parser."""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class VariantType(Enum):
    SNV = "SNV"
    INDEL = "INDEL"
    CNV = "CNV"


class Classification(Enum):
    PATHOGENIC = "Pathogenic"
    LIKELY_PATHOGENIC = "Likely Pathogenic"
    VUS = "Variant of Uncertain Significance"
    LIKELY_BENIGN = "Likely Benign"
    BENIGN = "Benign"


@dataclass
class Variant:
    raw_input: str
    variant_type: Optional[VariantType] = None

    # Genomic coordinates
    chromosome: Optional[str] = None
    position: Optional[int] = None
    ref: Optional[str] = None
    alt: Optional[str] = None

    # Annotation
    gene: Optional[str] = None
    transcript: Optional[str] = None
    hgvs_c: Optional[str] = None
    hgvs_p: Optional[str] = None
    consequence: Optional[str] = None  # e.g. stop_gained, frameshift_variant
    rsid: Optional[str] = None

    # CNV fields
    start: Optional[int] = None
    end: Optional[int] = None
    cnv_type: Optional[str] = None  # DEL or DUP


def parse_variant(raw: str) -> Variant:
    """
    Parse a variant string into a Variant object.

    Supported formats:
      HGVS transcript:  NM_007294.4:c.5266dupC
      VCF-like:         chr17-41234470-G-A  or  17:41234470:G:A
      rsID:             rs80357906
      CNV:              chr17-1000000-5000000-DEL
    """
    raw = raw.strip()
    v = Variant(raw_input=raw)

    # rsID
    if re.match(r'^rs\d+$', raw, re.IGNORECASE):
        v.rsid = raw.lower()
        return v

    # HGVS (transcript-based): NM_xxxxx.x:c.xxxxx
    if re.match(r'^NM_\d+(\.\d+)?:c\.', raw, re.IGNORECASE):
        parts = raw.split(':', 1)
        v.transcript = parts[0]
        v.hgvs_c = raw
        # Classify as SNV/INDEL based on notation
        c_part = parts[1]
        if any(x in c_part for x in ['dup', 'del', 'ins', 'inv']):
            v.variant_type = VariantType.INDEL
        else:
            v.variant_type = VariantType.SNV
        return v

    # CNV: chr17-1000000-5000000-DEL or chr17-1000000-5000000-DUP
    cnv_match = re.match(
        r'^(chr)?(\w+)-(\d+)-(\d+)-(DEL|DUP)$', raw, re.IGNORECASE
    )
    if cnv_match:
        v.chromosome = cnv_match.group(2)
        v.start = int(cnv_match.group(3))
        v.end = int(cnv_match.group(4))
        v.cnv_type = cnv_match.group(5).upper()
        v.variant_type = VariantType.CNV
        return v

    # VCF-like: chr17-41234470-G-A  or  17:41234470:G:A
    vcf_match = re.match(
        r'^(chr)?(\w+)[-:](\d+)[-:]([ACGT]+)[-:]([ACGT]+)$', raw, re.IGNORECASE
    )
    if vcf_match:
        v.chromosome = vcf_match.group(2)
        v.position = int(vcf_match.group(3))
        v.ref = vcf_match.group(4).upper()
        v.alt = vcf_match.group(5).upper()
        v.variant_type = VariantType.SNV if len(v.ref) == 1 and len(v.alt) == 1 else VariantType.INDEL
        return v

    raise ValueError(
        f"Could not parse variant: '{raw}'\n"
        "Supported formats:\n"
        "  HGVS:  NM_007294.4:c.5266dupC\n"
        "  VCF:   chr17-41234470-G-A\n"
        "  rsID:  rs80357906\n"
        "  CNV:   chr17-1000000-5000000-DEL"
    )
