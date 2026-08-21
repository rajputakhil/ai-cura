"""
Public API clients for variant evidence gathering.

APIs used (all free, no auth required except optional NCBI key):
  - Ensembl VEP REST  https://rest.ensembl.org
  - gnomAD GraphQL    https://gnomad.broadinstitute.org/api
  - NCBI ClinVar      https://eutils.ncbi.nlm.nih.gov
"""

import os
import re
import time
import requests
from typing import Optional

_SESSION = requests.Session()
_SESSION.headers.update({"Content-Type": "application/json"})

NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")


# ---------------------------------------------------------------------------
# Ensembl VEP
# ---------------------------------------------------------------------------

def vep_annotate(hgvs: str, genome: str = "GRCh38") -> dict:
    """
    Annotate a variant using Ensembl VEP REST API.
    Returns the first hit from the response or an empty dict on failure.
    """
    server = "https://rest.ensembl.org"
    endpoint = f"/vep/human/hgvs/{requests.utils.quote(hgvs)}"
    params = {
        "content-type": "application/json",
        "CADD": 1,
        "LoF": 1,
        "canonical": 1,
    }
    if genome == "GRCh37":
        server = "https://grch37.rest.ensembl.org"

    try:
        resp = _SESSION.get(server + endpoint, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else {}
    except Exception as e:
        return {"_error": str(e)}


def vep_annotate_vcf(chrom: str, pos: int, ref: str, alt: str, genome: str = "GRCh38") -> dict:
    """Annotate a VCF-format variant via VEP."""
    hgvs = f"{chrom}:g.{pos}{ref}>{alt}"
    return vep_annotate(hgvs, genome)


# ---------------------------------------------------------------------------
# gnomAD
# ---------------------------------------------------------------------------

_GNOMAD_URL = "https://gnomad.broadinstitute.org/api"

_GNOMAD_QUERY = """
query VariantFrequency($variantId: String!, $datasetId: DatasetId!) {
  variant(variantId: $variantId, dataset: $datasetId) {
    variantId
    exome {
      ac
      an
      af
      populations {
        id
        ac
        an
        af
      }
    }
    genome {
      ac
      an
      af
      populations {
        id
        ac
        an
        af
      }
    }
    clinvar {
      clinicalSignificance
      goldStars
    }
  }
}
"""

def gnomad_frequency(chrom: str, pos: int, ref: str, alt: str, genome: str = "GRCh38") -> dict:
    """
    Fetch allele frequency from gnomAD.
    Returns a dict with keys: af, ac, an, source ('exome'/'genome'), clinvar.
    """
    dataset = "gnomad_r4" if genome == "GRCh38" else "gnomad_r2_1"
    variant_id = f"{chrom}-{pos}-{ref}-{alt}"

    try:
        resp = _SESSION.post(
            _GNOMAD_URL,
            json={"query": _GNOMAD_QUERY, "variables": {"variantId": variant_id, "datasetId": dataset}},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        variant = data.get("data", {}).get("variant")
        if not variant:
            return {"af": None, "ac": None, "an": None, "source": None}

        # Prefer exome data, fall back to genome
        for source in ("exome", "genome"):
            src_data = variant.get(source)
            if src_data and src_data.get("an", 0) > 0:
                return {
                    "af": src_data["af"],
                    "ac": src_data["ac"],
                    "an": src_data["an"],
                    "source": source,
                    "clinvar": variant.get("clinvar"),
                }
        return {"af": None, "ac": None, "an": None, "source": None}
    except Exception as e:
        return {"af": None, "_error": str(e)}


# ---------------------------------------------------------------------------
# ClinVar (NCBI E-utilities)
# ---------------------------------------------------------------------------

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

def _ncbi_params(extra: dict) -> dict:
    params = {"retmode": "json", **extra}
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    return params


def clinvar_search(query: str) -> list[dict]:
    """
    Search ClinVar and return a list of variant summaries.
    Each summary contains: title, clinical_significance, review_status, conditions.
    """
    try:
        # Step 1: esearch
        search_resp = _SESSION.get(
            f"{_EUTILS}/esearch.fcgi",
            params=_ncbi_params({"db": "clinvar", "term": query, "retmax": 5}),
            timeout=15,
        )
        search_resp.raise_for_status()
        ids = search_resp.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []

        time.sleep(0.35)  # Be polite to NCBI

        # Step 2: esummary
        summary_resp = _SESSION.get(
            f"{_EUTILS}/esummary.fcgi",
            params=_ncbi_params({"db": "clinvar", "id": ",".join(ids)}),
            timeout=15,
        )
        summary_resp.raise_for_status()
        result = summary_resp.json().get("result", {})

        summaries = []
        for uid in ids:
            entry = result.get(uid, {})
            if not entry:
                continue
            germline = entry.get("germline_classification", {})
            summaries.append({
                "uid": uid,
                "title": entry.get("title", ""),
                "clinical_significance": germline.get("description", ""),
                "review_status": germline.get("review_status", ""),
                "last_evaluated": germline.get("last_evaluated", ""),
                "conditions": [
                    c.get("name", "") for c in entry.get("trait_set", [])
                ],
                "variation_type": entry.get("obj_type", ""),
            })
        return summaries

    except Exception as e:
        return [{"_error": str(e)}]


def clinvar_search_hgvs(hgvs_c: str, gene: Optional[str] = None) -> list[dict]:
    """Search ClinVar by HGVS notation, optionally filtered by gene."""
    query = f'"{hgvs_c}"[All Fields]'
    if gene:
        query += f' AND "{gene}"[Gene Name]'
    return clinvar_search(query)


def clinvar_search_rsid(rsid: str) -> list[dict]:
    """Search ClinVar by rsID."""
    return clinvar_search(f'"{rsid}"[RS# (All)]')
