"""
main.py — APS Alt Text Generation Pipeline (Phase 1)
─────────────────────────────────────────────────────
Usage:
    python main.py path/to/paper.pdf
    python main.py path/to/papers/          # process all PDFs in folder
    python main.py paper.pdf --no-json      # skip JSON output
    python main.py paper.pdf --dry-run      # extract only, skip LLM
"""

import argparse
import sys
import time
from pathlib import Path

# ── allow running from project root ─────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from pipeline.extractor import extract_figures
from pipeline.llm import generate_alt_text, DELAY_S
from pipeline.output import write_excel, write_json


def process_pdf(
    pdf_path: str,
    output_dir: str = "output",
    figures_dir: str = "figures",
    dry_run: bool = False,
    write_json_output: bool = True,
    api_key: str = "",
) -> list:
    """
    Full pipeline for a single PDF.
    Returns the list of processed Figure objects.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Stage 1 & 2: Extract figures + associate captions ───────────────────
    figures = extract_figures(pdf_path, output_dir=figures_dir)

    if not figures:
        print("  No figures found.")
        return []

    if dry_run:
        print(f"\n[DRY RUN] Skipping LLM generation for {len(figures)} figure(s).")
    else:
        # ── Stage 3: Generate alt text via Gemini ───────────────────────────
        print(f"\n🤖 Generating alt text for {len(figures)} figure(s)...")
        for i, fig in enumerate(figures, start=1):
            print(f"  [{i}/{len(figures)}] {fig.fig_id} ({fig.fig_type})")

            alt_text, status = generate_alt_text(
                image_bytes=fig.image_bytes,
                fig_type=fig.fig_type,
                caption=fig.caption,
                api_key=api_key,
            )

            fig.alt_text = alt_text
            fig.status = status
            print(f"    → {alt_text[:90]}{'...' if len(alt_text) > 90 else ''}")

            # Throttle between API calls
            if i < len(figures):
                time.sleep(DELAY_S)

    # ── Stage 4: Write outputs ───────────────────────────────────────────────
    stem = Path(pdf_path).stem
    excel_path = output_dir / f"{stem}_alt_text.xlsx"
    write_excel(figures, str(excel_path))

    if write_json_output:
        json_path = output_dir / f"{stem}_alt_text.json"
        write_json(figures, str(json_path))

    return figures


def main():
    parser = argparse.ArgumentParser(
        description="APS Alt Text Generation Pipeline — Phase 1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", help="PDF file or folder containing PDFs")
    parser.add_argument("--output-dir", default="output", help="Output directory (default: output/)")
    parser.add_argument("--figures-dir", default="figures", help="Figures image directory (default: figures/)")
    parser.add_argument("--api-key", default="", help="Gemini API key (or set GEMINI_API_KEY env var)")
    parser.add_argument("--dry-run", action="store_true", help="Extract only, skip LLM generation")
    parser.add_argument("--no-json", action="store_true", help="Skip JSON output")
    args = parser.parse_args()

    input_path = Path(args.input)

    if input_path.is_dir():
        pdf_files = sorted(input_path.glob("*.pdf"))
        if not pdf_files:
            print(f"❌ No PDFs found in: {input_path}")
            sys.exit(1)
        print(f"📂 Found {len(pdf_files)} PDF(s) in {input_path}\n")
    elif input_path.is_file() and input_path.suffix.lower() == ".pdf":
        pdf_files = [input_path]
    else:
        print(f"❌ Not a valid PDF or folder: {args.input}")
        sys.exit(1)

    all_figures = []
    for pdf_path in pdf_files:
        figures = process_pdf(
            pdf_path=str(pdf_path),
            output_dir=args.output_dir,
            figures_dir=args.figures_dir,
            dry_run=args.dry_run,
            write_json_output=not args.no_json,
            api_key=args.api_key,
        )
        all_figures.extend(figures)

    # ── Final summary ────────────────────────────────────────────────────────
    total = len(all_figures)
    done = sum(1 for f in all_figures if f.status == "done")
    skipped = sum(1 for f in all_figures if f.status == "skipped")
    errors = sum(1 for f in all_figures if "error" in f.status.lower())

    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Pipeline complete
  Total figures : {total}
  Alt text done : {done}
  Skipped       : {skipped}
  Errors        : {errors}
  Outputs in    : {args.output_dir}/
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")


if __name__ == "__main__":
    main()