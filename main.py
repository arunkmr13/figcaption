"""
main.py — APS Alt Text Generation Pipeline (Phase 2)
──────────────────────────────────────────────────────
Usage:
    # PDF input
    python main.py paper.pdf
    python main.py papers/                    # all PDFs in folder

    # XML input
    python main.py article.xml
    python main.py articles/                  # all XMLs in folder

    # Mixed folder (PDFs + XMLs)
    python main.py inputs/

    # Options
    python main.py paper.pdf --dry-run        # extract + validate only, skip LLM
    python main.py paper.pdf --no-json        # skip JSON output
    python main.py article.xml --no-embed     # skip embedded XML output
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pipeline.extractor import extract_figures
from pipeline.llm import generate_alt_text, DELAY_S
from pipeline.output import write_excel, write_json, print_metrics
from pipeline.xml_writer import write_xml_outputs


def process_file(
    input_path: str,
    output_dir: str = "outputs",
    figures_dir: str = "figures",
    dry_run: bool = False,
    write_json_output: bool = True,
    write_embedded_xml: bool = True,
    api_key: str = "",
) -> list:
    """Full Phase 2 pipeline for a single PDF or XML file."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = input_path.stem
    source_type = input_path.suffix.lower().lstrip(".")

    # ── Stages 1 & 2: Extract + validate ────────────────────────────────────
    figures = extract_figures(str(input_path), output_dir=figures_dir)

    if not figures:
        print("  No figures found.")
        return []

    eligible   = [f for f in figures if f.eligible]
    ineligible = [f for f in figures if not f.eligible]

    print(f"\n  Validation: {len(eligible)} eligible, {len(ineligible)} skipped by flag")

    if dry_run:
        print(f"\n[DRY RUN] Skipping LLM generation for {len(figures)} figure(s).")
        for f in figures:
            f.status = "dry_run"
    else:
        # ── Stage 3: Generate alt text (eligible only) ───────────────────────
        print(f"\n🤖 Generating alt text for {len(eligible)}/{len(figures)} eligible figure(s)...")

        for i, fig in enumerate(figures, start=1):
            label = f"  [{i}/{len(figures)}] {fig.fig_id} ({fig.fig_type}) [{fig.final_flag}]"

            if not fig.eligible:
                fig.alt_text = f"[SKIPPED — {fig.final_flag}]"
                fig.status = "skipped"
                print(f"{label} → skipped")
                continue

            print(label)
            alt_text, status = generate_alt_text(
                image_bytes=fig.image_bytes,
                fig_type=fig.fig_type,
                caption=fig.caption,
                final_flag=fig.final_flag,
                existing_alt=fig.existing_alt,
                api_key=api_key,
            )
            fig.alt_text = alt_text
            fig.status = status
            print(f"    → {alt_text[:90]}{'...' if len(alt_text) > 90 else ''}")

            if i < len(figures):
                time.sleep(DELAY_S)

    # ── Stage 4: Write outputs ───────────────────────────────────────────────
    excel_path = output_dir / f"{stem}_alt_text.xlsx"
    write_excel(figures, str(excel_path))

    if write_json_output:
        write_json(figures, str(output_dir / f"{stem}_alt_text.json"))

    # XML outputs (only for XML source files)
    if source_type == "xml" and not dry_run:
        write_xml_outputs(
            xml_path=str(input_path),
            figures=figures,
            output_dir=str(output_dir),
        )

    # ── Stage 5: Metrics ─────────────────────────────────────────────────────
    print_metrics(figures, source_name=input_path.name)

    return figures


def main():
    parser = argparse.ArgumentParser(
        description="APS Alt Text Generation Pipeline — Phase 2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", help="PDF/XML file or folder containing PDF/XML files")
    parser.add_argument("--output-dir",  default="outputs", help="Output directory (default: outputs/)")
    parser.add_argument("--figures-dir", default="figures", help="Figures image directory (default: figures/)")
    parser.add_argument("--api-key",     default="",        help="Gemini API key (or set GEMINI_API_KEY env var)")
    parser.add_argument("--dry-run",     action="store_true", help="Extract + validate only, skip LLM")
    parser.add_argument("--no-json",     action="store_true", help="Skip JSON output")
    parser.add_argument("--no-embed",    action="store_true", help="Skip embedded XML output")
    args = parser.parse_args()

    input_path = Path(args.input)

    # Collect input files
    if input_path.is_dir():
        input_files = sorted(
            list(input_path.glob("*.pdf")) + list(input_path.glob("*.xml"))
        )
        if not input_files:
            print(f"❌ No PDF or XML files found in: {input_path}")
            sys.exit(1)
        print(f"📂 Found {len(input_files)} file(s) in {input_path}\n")
    elif input_path.is_file() and input_path.suffix.lower() in (".pdf", ".xml"):
        input_files = [input_path]
    else:
        print(f"❌ Not a valid PDF, XML, or folder: {args.input}")
        sys.exit(1)

    all_figures = []
    for file_path in input_files:
        figures = process_file(
            input_path=str(file_path),
            output_dir=args.output_dir,
            figures_dir=args.figures_dir,
            dry_run=args.dry_run,
            write_json_output=not args.no_json,
            write_embedded_xml=not args.no_embed,
            api_key=args.api_key,
        )
        all_figures.extend(figures)

    if len(input_files) > 1:
        print("\n\n📁 COMBINED SUMMARY (all files)")
        print_metrics(all_figures)

    total  = len(all_figures)
    done   = sum(1 for f in all_figures if f.status == "done")
    skip   = sum(1 for f in all_figures if "skip" in f.status)
    errors = sum(1 for f in all_figures if "error" in f.status.lower())

    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Pipeline complete
  Total figures : {total}
  Alt text done : {done}
  Skipped       : {skip}
  Errors        : {errors}
  Outputs in    : {args.output_dir}/
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")


if __name__ == "__main__":
    main()