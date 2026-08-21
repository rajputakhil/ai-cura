#!/usr/bin/env python3
"""
AI-CURA — Automated LLM workflow for genetic variant classification.

Prototype implementation of the AI-CURA framework
(Ma et al., Science Translational Medicine, 2025).
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from src.classifier import VariantClassifier

console = Console()

# Classification → color mapping
_COLORS = {
    "Pathogenic": "red",
    "Likely Pathogenic": "orange3",
    "Variant of Uncertain Significance": "yellow",
    "Likely Benign": "cyan",
    "Benign": "green",
    "Requires Manual Review (CNV)": "magenta",
}


def parse_args():
    parser = argparse.ArgumentParser(
        prog="ai-cura",
        description="AI-CURA: Automated genetic variant classification (ACMG/AMP guidelines)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples
--------
  # BRCA1 frameshift — known pathogenic
  python main.py --variant "NM_007294.4:c.5266dupC"

  # Via rsID
  python main.py --variant "rs80357906"

  # VCF format  (chr-pos-ref-alt)
  python main.py --variant "chr17-41234470-G-A"

  # CNV deletion
  python main.py --variant "chr17-1000000-5000000-DEL"

  # Enable LLM synthesis (requires ANTHROPIC_API_KEY)
  python main.py --variant "NM_007294.4:c.5266dupC" --llm

  # JSON output (for downstream tools)
  python main.py --variant "rs80357906" --output json
        """,
    )
    parser.add_argument(
        "--variant", "-v", required=True,
        help="Variant: HGVS (NM_xxxxx:c.xxxxx), rsID, VCF (chr-pos-ref-alt), or CNV (chr-start-end-DEL/DUP)",
    )
    parser.add_argument(
        "--genome", default="GRCh38", choices=["GRCh37", "GRCh38"],
        help="Reference genome build (default: GRCh38)",
    )
    parser.add_argument(
        "--llm", action="store_true",
        help="Enable Claude LLM synthesis (requires ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--output", "-o", choices=["text", "json"], default="text",
        help="Output format (default: text)",
    )
    return parser.parse_args()


def display_report(report) -> None:
    cls = report.acmg_result.classification
    color = _COLORS.get(cls, "white")

    # Header
    console.print()
    console.print(Panel(
        f"[bold {color}]{cls}[/bold {color}]",
        title="[bold]AI-CURA Classification Result[/bold]",
        subtitle=f"Variant: {report.variant.raw_input}",
        border_style=color,
        expand=False,
    ))

    # Variant info
    info = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    info.add_column("Field", style="dim")
    info.add_column("Value")

    fields = [
        ("Gene", report.variant.gene),
        ("Transcript", report.variant.transcript),
        ("HGVS c.", report.variant.hgvs_c),
        ("HGVS p.", report.variant.hgvs_p),
        ("Consequence", report.variant.consequence),
        ("gnomAD AF", f"{report.gnomad_data.get('af'):.2e}"
                       if report.gnomad_data.get("af") is not None
                       else "not found"),
        ("Genome build", report.variant.raw_input and "GRCh38"),
    ]
    for label, value in fields:
        if value:
            info.add_row(label, str(value))

    console.print(info)

    # ACMG criteria table
    table = Table(
        title="ACMG/AMP Criteria",
        box=box.ROUNDED,
        show_lines=True,
        title_style="bold",
    )
    table.add_column("Code", style="bold", width=8)
    table.add_column("Status", width=10)
    table.add_column("Strength", width=12)
    table.add_column("Direction", width=12)
    table.add_column("Evidence")

    for c in report.acmg_result.criteria:
        if c.code == "CNV_NOTE":
            continue
        status_text = Text("✓ MET", style="green bold") if c.met else Text("✗", style="dim")
        direction_style = "red" if c.direction == "pathogenic" else "cyan"
        table.add_row(
            c.code,
            status_text,
            c.strength.replace("_", " "),
            Text(c.direction, style=direction_style),
            c.evidence,
        )

    console.print(table)

    # ClinVar hits
    if report.clinvar_hits and not report.clinvar_hits[0].get("_error"):
        console.print("\n[bold]ClinVar Evidence[/bold]")
        for h in report.clinvar_hits[:3]:
            sig = h.get("clinical_significance", "unknown")
            sig_color = "red" if "pathogenic" in sig.lower() else "cyan" if "benign" in sig.lower() else "yellow"
            console.print(
                f"  [{sig_color}]{sig}[/{sig_color}] — {h.get('review_status', '')} | "
                f"{h.get('title', '')[:80]}"
            )

    # Interpretation
    console.print()
    console.print(Panel(
        report.interpretation,
        title="[bold]Interpretation[/bold]",
        border_style="dim",
    ))
    console.print()


def main():
    args = parse_args()

    if args.llm and not os.getenv("ANTHROPIC_API_KEY"):
        console.print(
            "[yellow]Warning:[/yellow] --llm requires ANTHROPIC_API_KEY. "
            "Falling back to rule-based summary."
        )

    classifier = VariantClassifier(genome=args.genome, use_llm=args.llm)

    with console.status(f"[bold green]Classifying {args.variant} ...", spinner="dots"):
        try:
            report = classifier.classify(args.variant)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

    if args.output == "json":
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        display_report(report)


if __name__ == "__main__":
    main()
