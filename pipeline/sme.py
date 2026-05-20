"""
sme.py — SME Validation Workflow (spec section 8, steps 10-12)
───────────────────────────────────────────────────────────────
Implements the post-generation SME (Subject Matter Expert) review workflow:

  Step 10: SME validation  — export review Excel for human approval
  Step 11: Inject approved — read back approved alt text, inject into XML
  Step 12: Final validation — verify all <fig> elements have <alt-text>

Usage:
    # Step 10: export review sheet (after pipeline run)
    from pipeline.sme import export_sme_review
    export_sme_review(figures, "outputs/review.xlsx")

    # Step 11: inject approved (after SME fills in the review sheet)
    from pipeline.sme import inject_approved_alt_text
    inject_approved_alt_text("outputs/review.xlsx", "article.xml", "outputs/")

    # Step 12: validate final XML
    from pipeline.sme import validate_final_xml
    report = validate_final_xml("outputs/article_final.xml")
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from collections import Counter
from dataclasses import dataclass
from typing import Optional

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


# ── Colours ────────────────────────────────────────────────────────────────────
HEADER_FILL    = "2D5FA0"
HEADER_FONT    = "FFFFFF"
APPROVED_FILL  = "E8F5E9"   # green  — SME approved
REJECTED_FILL  = "FFEBEE"   # red    — SME rejected
EDITED_FILL    = "FFF9C4"   # yellow — SME edited
PENDING_FILL   = "F5F8FF"   # blue   — awaiting review


# ── Step 10: Export SME review sheet ──────────────────────────────────────────

SME_COLUMNS = [
    ("fig_id",      28, "Figure ID"),
    ("xml_label",   12, "Label"),
    ("fig_type",    16, "Type"),
    ("caption",     48, "Caption"),
    ("existing_alt",36, "Existing Alt"),
    ("alt_text",    60, "Generated Alt (review this)"),
    ("final_flag",  22, "Flag"),
    ("confidence",  12, "Confidence"),
    ("qc_flag",     20, "QC Flag"),
    ("qc_notes",    40, "QC Notes"),
    # SME fills these in:
    ("",            12, "SME Decision"),      # APPROVED / REJECTED / EDITED
    ("",            60, "SME Alt Text"),      # filled if EDITED
    ("",            30, "SME Comments"),      # optional notes
]


def export_sme_review(figures: list, output_path: str) -> str:
    """
    Step 10: Export a review-ready Excel sheet for SME validation.
    SME fills in 'SME Decision', 'SME Alt Text', 'SME Comments' columns.
    Only includes figures that were generated (status == 'done').
    """
    if not HAS_OPENPYXL:
        print("[SKIP] openpyxl not available")
        return ""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SME Review"
    ws.freeze_panes = "A2"

    # Headers
    for col_idx, (_, width, label) in enumerate(SME_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.fill = PatternFill("solid", fgColor=HEADER_FILL)
        cell.font = Font(bold=True, color=HEADER_FONT, size=10)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[1].height = 22

    # Add data validation dropdown for SME Decision column (col 11)
    try:
        from openpyxl.worksheet.datavalidation import DataValidation
        dv = DataValidation(
            type="list",
            formula1='"APPROVED,REJECTED,EDITED"',
            allow_blank=True,
            showDropDown=False,
        )
        ws.add_data_validation(dv)
        dv.sqref = f"K2:K10000"
    except Exception:
        pass  # data validation is optional

    eligible = [f for f in figures if getattr(f, "status", "") == "done"]
    print(f"  📋 Exporting {len(eligible)} generated figures for SME review")

    for row_idx, fig in enumerate(eligible, start=2):
        def g(attr): return getattr(fig, attr, "") or ""

        values = [
            g("fig_id"), g("xml_label"), g("fig_type"), g("caption"),
            g("existing_alt"), g("alt_text"), g("final_flag"),
            g("confidence"), g("qc_flag"), g("qc_notes"),
            "",  # SME Decision — blank for SME to fill
            "",  # SME Alt Text — blank for SME to fill
            "",  # SME Comments — blank for SME to fill
        ]

        fill = PatternFill("solid", fgColor=PENDING_FILL)
        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.fill = fill
            cell.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
            cell.font = Font(size=10)
        ws.row_dimensions[row_idx].height = 50

    # Instructions sheet
    ws_inst = wb.create_sheet("Instructions")
    instructions = [
        ("FigCaption — SME Review Sheet", ""),
        ("", ""),
        ("HOW TO USE THIS SHEET:", ""),
        ("1.", "Review each row in the 'SME Review' tab"),
        ("2.", "For 'SME Decision' column, choose one of:"),
        ("", "   APPROVED  — the generated alt text is correct, use as-is"),
        ("", "   REJECTED  — the alt text is wrong, leave 'SME Alt Text' blank to skip"),
        ("", "   EDITED    — modify the alt text in the 'SME Alt Text' column"),
        ("3.", "If EDITED, paste your improved alt text in 'SME Alt Text' column"),
        ("4.", "Add any notes in 'SME Comments' column"),
        ("5.", "Save the file and send back for injection"),
        ("", ""),
        ("AFTER REVIEW:", ""),
        ("", "Run: python main.py --inject-approved <this_file> --xml <original.xml>"),
    ]
    for row_idx, (a, b) in enumerate(instructions, start=1):
        ws_inst.cell(row=row_idx, column=1, value=a).font = Font(bold=(b == ""), size=11 if b == "" else 10)
        ws_inst.cell(row=row_idx, column=2, value=b).font = Font(size=10)
    ws_inst.column_dimensions["A"].width = 6
    ws_inst.column_dimensions["B"].width = 80

    wb.save(str(output_path))
    print(f"  ✅ SME review sheet saved: {output_path}")
    return str(output_path)


# ── Step 11: Inject approved alt text ─────────────────────────────────────────

@dataclass
class SMEDecision:
    fig_id:      str
    decision:    str   # APPROVED / REJECTED / EDITED
    alt_text:    str   # final alt text to inject
    comments:    str


def read_sme_decisions(review_path: str) -> list[SMEDecision]:
    """Read SME decisions back from the filled-in review Excel."""
    if not HAS_OPENPYXL:
        raise RuntimeError("openpyxl required to read SME review sheet")

    wb   = openpyxl.load_workbook(review_path)
    ws   = wb["SME Review"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))

    decisions = []
    for row in rows:
        if not row or not row[0]:
            continue
        fig_id   = str(row[0] or "").strip()
        gen_alt  = str(row[5] or "").strip()   # Generated Alt
        decision = str(row[10] or "").strip().upper()
        sme_alt  = str(row[11] or "").strip()
        comments = str(row[12] or "").strip()

        if not decision:
            continue

        # Determine final alt text
        if decision == "APPROVED":
            final_alt = gen_alt
        elif decision == "EDITED" and sme_alt:
            final_alt = sme_alt
        else:
            final_alt = ""  # REJECTED or EDITED without text

        decisions.append(SMEDecision(
            fig_id=fig_id, decision=decision,
            alt_text=final_alt, comments=comments,
        ))

    approved = sum(1 for d in decisions if d.decision == "APPROVED")
    edited   = sum(1 for d in decisions if d.decision == "EDITED")
    rejected = sum(1 for d in decisions if d.decision == "REJECTED")
    print(f"  📖 SME decisions read: {approved} approved, {edited} edited, {rejected} rejected")
    return decisions


def inject_approved_alt_text(
    review_path: str,
    xml_path:    str,
    output_dir:  str,
) -> str:
    """
    Step 11: Read SME-approved alt text and inject into the original XML.
    Produces <stem>_final.xml in output_dir.
    """
    import re

    decisions   = read_sme_decisions(review_path)
    decision_map = {d.fig_id: d for d in decisions}

    xml_path   = Path(xml_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_xml = xml_path.read_text(encoding="utf-8")
    # Register namespaces to preserve them
    for match in re.finditer(r'xmlns(?::(\w+))?=["\']([^"\']+)["\']', raw_xml):
        prefix = match.group(1) or ""
        uri    = match.group(2)
        ET.register_namespace(prefix, uri)

    tree = ET.parse(str(xml_path))
    root = tree.getroot()

    ns      = root.tag[1:root.tag.index("}")] if root.tag.startswith("{") else ""
    alt_tag = f"{{{ns}}}alt-text" if ns else "alt-text"

    def strip_ns(tag):
        return tag.split("}")[-1] if "}" in tag else tag

    fig_elements = [el for el in root.iter() if strip_ns(el.tag) == "fig"]

    injected = 0
    skipped  = 0
    for fig_el in fig_elements:
        fig_id_attr = fig_el.get("id", "")
        # Try matching by XML id attr or by fig_id in decision map
        decision = decision_map.get(fig_id_attr)
        if not decision:
            # Try matching by position-based fig_id
            for d_key, d_val in decision_map.items():
                if fig_id_attr and fig_id_attr in d_key:
                    decision = d_val
                    break

        if not decision or not decision.alt_text:
            skipped += 1
            continue

        # Remove existing alt-text everywhere inside this fig
        for child in list(fig_el):
            if strip_ns(child.tag) == "alt-text":
                fig_el.remove(child)
            if strip_ns(child.tag) in ("graphic", "inline-graphic"):
                for grandchild in list(child):
                    if strip_ns(grandchild.tag) == "alt-text":
                        child.remove(grandchild)

        # Inject approved alt text
        alt_el      = ET.SubElement(fig_el, alt_tag)
        alt_el.text = decision.alt_text
        injected   += 1

    print(f"  💉 Injected approved alt text into {injected}/{len(fig_elements)} figures "
          f"({skipped} skipped)")

    # Write final XML
    ET.indent(tree, space="  ")
    stem       = xml_path.stem
    final_path = output_dir / f"{stem}_final.xml"
    tree.write(str(final_path), encoding="utf-8", xml_declaration=True)
    print(f"  📄 Final XML saved: {final_path}")
    return str(final_path)


# ── Step 12: Final XML validation ─────────────────────────────────────────────

@dataclass
class ValidationReport:
    total_figs:        int
    with_alt_text:     int
    without_alt_text:  int
    empty_alt_text:    int
    missing_fig_ids:   list[str]
    coverage_pct:      float
    passed:            bool


def validate_final_xml(xml_path: str) -> ValidationReport:
    """
    Step 12: Validate that all <fig> elements in the final XML have
    non-empty <alt-text>. Returns a ValidationReport.
    """
    xml_path = Path(xml_path)
    tree     = ET.parse(str(xml_path))
    root     = tree.getroot()

    def strip_ns(tag):
        return tag.split("}")[-1] if "}" in tag else tag

    fig_elements = [el for el in root.iter() if strip_ns(el.tag) == "fig"]
    total        = len(fig_elements)

    with_alt    = 0
    without_alt = 0
    empty_alt   = 0
    missing_ids = []

    for fig_el in fig_elements:
        fig_id = fig_el.get("id", "[no id]")
        alt_texts = [
            "".join(el.itertext()).strip()
            for el in fig_el.iter()
            if strip_ns(el.tag) == "alt-text"
        ]

        if not alt_texts:
            without_alt += 1
            missing_ids.append(fig_id)
        elif all(not t for t in alt_texts):
            empty_alt += 1
            missing_ids.append(f"{fig_id} [empty]")
        else:
            with_alt += 1

    coverage = (with_alt / total * 100) if total else 0
    passed   = without_alt == 0 and empty_alt == 0

    report = ValidationReport(
        total_figs=total,
        with_alt_text=with_alt,
        without_alt_text=without_alt,
        empty_alt_text=empty_alt,
        missing_fig_ids=missing_ids,
        coverage_pct=coverage,
        passed=passed,
    )

    _print_validation_report(report, xml_path.name)
    return report


def _print_validation_report(report: ValidationReport, filename: str):
    status = "✅ PASSED" if report.passed else "❌ FAILED"
    print(f"""
---------- FINAL XML VALIDATION: {filename} ----------
Status            : {status}
Total <fig>       : {report.total_figs}
With <alt-text>   : {report.with_alt_text}
Without <alt-text>: {report.without_alt_text}
Empty <alt-text>  : {report.empty_alt_text}
Coverage          : {report.coverage_pct:.1f}%""")

    if report.missing_fig_ids:
        print("Missing alt text in:")
        for fig_id in report.missing_fig_ids[:10]:
            print(f"  - {fig_id}")
        if len(report.missing_fig_ids) > 10:
            print(f"  ... and {len(report.missing_fig_ids) - 10} more")
    print("─" * 55)
