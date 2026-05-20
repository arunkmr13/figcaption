"""
main.py — APS Alt Text Generation Pipeline (Phase 2)
──────────────────────────────────────────────────────
Usage:
    python main.py paper.pdf
    python main.py papers/                    # all PDFs in folder
    python main.py article.xml
    python main.py articles/                  # all XMLs in folder
    python main.py inputs/                    # mixed folder

    python main.py paper.pdf --dry-run        # extract + validate only, skip LLM
    python main.py paper.pdf --no-json        # skip JSON output
    python main.py article.xml --no-embed     # skip embedded XML output
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pipeline.extractor import extract_figures
from pipeline.llm import generate_alt_text          # FIX 3: no longer import DELAY_S
from pipeline.output import write_excel, write_json, print_metrics, write_log
from pipeline.sme    import export_sme_review, inject_approved_alt_text, validate_final_xml
from pipeline.xml_writer import write_xml_outputs
from pipeline.qc import run_qc_on_figures, print_qc_metrics


def process_file(
    input_path:          str,
    output_dir:          str  = "outputs",
    figures_dir:         str  = "figures",
    dry_run:             bool = False,
    write_json_output:   bool = True,
    write_embedded_xml:  bool = True,
    api_key:             str  = "",
) -> list:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem        = input_path.stem
    source_type = input_path.suffix.lower().lstrip(".")

    # ── Stages 1 & 2: Extract + Validate ────────────────────────────────────
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
                # FIX 2: store clean empty string, not noisy sentinel
                fig.alt_text = ""
                fig.status   = "skipped"
                print(f"{label} → skipped")
                continue

            print(label)
            alt_text, status = generate_alt_text(
                image_bytes  = fig.image_bytes,
                fig_type     = fig.fig_type,
                caption      = fig.caption,
                final_flag   = fig.final_flag,
                existing_alt = fig.existing_alt,
                confidence   = fig.confidence,      # FIX 1: now passed through
                api_key      = api_key,
            )
            fig.alt_text = alt_text
            fig.status   = status

            if alt_text:
                print(f"    → {alt_text[:90]}{'...' if len(alt_text) > 90 else ''}")

            # FIX 3: removed time.sleep(DELAY_S) — delay now lives in llm.py

    # ── Stage 3b: QC on generated alt text ──────────────────────────────────
    if not dry_run:
        qc_failures = run_qc_on_figures(figures)
        if qc_failures:
            print(f"\n⚠️  QC flagged {len(qc_failures)} figure(s) for review")


    # ── Stage 4: Write outputs ───────────────────────────────────────────────
    excel_path = output_dir / f"{stem}_alt_text.xlsx"
    write_excel(figures, str(excel_path))

    if write_json_output:
        write_json(figures, str(output_dir / f"{stem}_alt_text.json"))

    # Step 8: Write log file (spec section 6)
    write_log(figures, str(output_dir / f"{stem}_processing.log"), source_name=input_path.name)

    if source_type == "xml" and not dry_run:
        write_xml_outputs(
            xml_path   = str(input_path),
            figures    = figures,
            output_dir = str(output_dir),
        )

    # ── Stage 5: Metrics ─────────────────────────────────────────────────────
    print_metrics(figures, source_name=input_path.name)
    if not dry_run:
        print_qc_metrics(figures)
    return figures


def main():
    parser = argparse.ArgumentParser(
        description="APS Alt Text Generation Pipeline — Phase 2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input",          help="PDF/XML file or folder")
    parser.add_argument("--output-dir",   default="outputs", help="Output directory")
    parser.add_argument("--figures-dir",  default="figures", help="Figures image directory")
    parser.add_argument("--api-key",      default="",        help="Gemini API key")
    parser.add_argument("--dry-run",      action="store_true", help="Extract + validate only")
    parser.add_argument("--no-json",      action="store_true", help="Skip JSON output")
    parser.add_argument("--no-embed",     action="store_true", help="Skip embedded XML output")
    args = parser.parse_args()

    input_path = Path(args.input)

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
            input_path         = str(file_path),
            output_dir         = args.output_dir,
            figures_dir        = args.figures_dir,
            dry_run            = args.dry_run,
            write_json_output  = not args.no_json,
            write_embedded_xml = not args.no_embed,
            api_key            = args.api_key,
        )
        all_figures.extend(figures)

    # Step 10: SME review export
    if args.sme_review and all_figures:
        stem = Path(args.input).stem if Path(args.input).is_file() else "batch"
        export_sme_review(all_figures, f"{args.output_dir}/{stem}_sme_review.xlsx")

    # Step 11: Inject approved alt text
    if args.inject_approved:
        xml_files = [f for f in input_files if str(f).endswith(".xml")]
        if xml_files:
            inject_approved_alt_text(args.inject_approved, str(xml_files[0]), args.output_dir)

    # Step 12: Final XML validation
    if args.validate_xml:
        validate_final_xml(args.validate_xml)

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