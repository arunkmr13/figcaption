"""
output.py — Structured Output Generation (Phase 2)
Writes Excel (.xlsx), JSON, and processing log (.log).
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from collections import Counter

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False
    print("[WARN] openpyxl not installed")

# ── Colour palette ─────────────────────────────────────────────────────────────
HEADER_FILL      = "2D5FA0"
HEADER_FONT      = "FFFFFF"
FLAG_OK_FILL     = "E8F5E9"
FLAG_INSUFF_FILL = "FFF9C4"
FLAG_MISS_FILL   = "FFE0B2"
FLAG_ERROR_FILL  = "FFEBEE"
FLAG_SKIP_FILL   = "F5F5F5"
ALT_ROW_FILL     = "F5F8FF"
QC_FAIL_FILL     = "FFF3CD"

# ── Excel columns (spec section 5 + Phase 2 additions) ────────────────────────
COLUMNS = [
    ("source_file",        18, "File"),
    ("xml_label",          12, "Label"),
    ("page_num",            6, "Page"),
    ("fig_id",             28, "Figure ID"),
    ("fig_type",           16, "Type"),
    ("image_filename",     30, "Image Path"),
    ("caption",            48, "Caption"),
    ("caption_word_count", 10, "Caption Words"),
    ("caption_flag",       22, "Caption Flag"),
    ("image_status",       18, "Image Status"),
    ("existing_alt",       36, "Existing Alt"),
    ("confidence",         12, "Confidence"),
    ("final_flag",         24, "Final Flag"),
    ("alt_text",           56, "Generated Alt"),
    ("status",             14, "Status"),
    ("processing_status",  18, "Processing Status"),
    ("qc_flag",            20, "QC Flag"),
    ("qc_notes",           40, "QC Notes"),
]


def _get(fig, key, default=""):
    return getattr(fig, key, default) or default


def _header_style(cell):
    cell.fill = PatternFill("solid", fgColor=HEADER_FILL)
    cell.font = Font(bold=True, color=HEADER_FONT, size=10)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _flag_fill(final_flag: str) -> str:
    if final_flag == "OK":                                      return FLAG_OK_FILL
    if "INSUFFICIENT" in final_flag:                           return FLAG_INSUFF_FILL
    if any(x in final_flag for x in ("MISSING","NOT_RESOLVED")): return FLAG_MISS_FILL
    if "ERROR" in final_flag:                                   return FLAG_ERROR_FILL
    if any(x in final_flag for x in ("SKIP","PRESENT")):       return FLAG_SKIP_FILL
    return "FFFFFF"


# ── Excel ──────────────────────────────────────────────────────────────────────

def write_excel(figures: list, output_path: str) -> str:
    if not HAS_OPENPYXL:
        print("[SKIP] openpyxl not available")
        return ""
    output_path = Path(output_path)
    try:
        wb = openpyxl.Workbook()
        _write_alt_text_sheet(wb, figures)
        _write_summary_sheet(wb, figures)
        wb.save(str(output_path))
        print(f"📊 Excel saved: {output_path}")
        return str(output_path)
    except PermissionError:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = output_path.with_name(f"{output_path.stem}_{ts}{output_path.suffix}")
        wb.save(str(fallback))
        print(f"[WARN] File locked — saved as: {fallback.name}")
        return str(fallback)


def _write_alt_text_sheet(wb, figures: list):
    ws = wb.active
    ws.title = "Alt Text"
    ws.freeze_panes = "A2"

    for col_idx, (key, width, label) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        _header_style(cell)
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[1].height = 22

    for row_idx, fig in enumerate(figures, start=2):
        final_flag = _get(fig, "final_flag")
        fill_hex   = _flag_fill(final_flag)
        qc_flag    = _get(fig, "qc_flag")
        if qc_flag and qc_flag not in ("QC_PASS", "QC_SKIPPED", ""):
            fill_hex = QC_FAIL_FILL

        fill   = PatternFill("solid", fgColor=fill_hex)
        values = [_get(fig, key) for key, _, _ in COLUMNS]

        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.fill = fill
            cell.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
            cell.font = Font(size=10)
        ws.row_dimensions[row_idx].height = 40


def _write_summary_sheet(wb, figures: list):
    ws = wb.create_sheet("Summary")
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 12
    current_row = 1

    def _section(title, counts):
        nonlocal current_row
        h1 = ws.cell(row=current_row, column=1, value=title)
        h2 = ws.cell(row=current_row, column=2, value="Count")
        _header_style(h1); _header_style(h2)
        current_row += 1
        for label, count in sorted(counts.items(), key=lambda x: -x[1]):
            ws.cell(row=current_row, column=1, value=label).font = Font(size=10)
            ws.cell(row=current_row, column=2, value=count).font  = Font(size=10)
            current_row += 1
        total = ws.cell(row=current_row, column=1, value="TOTAL")
        total.font = Font(bold=True, size=10)
        ws.cell(row=current_row, column=2, value=sum(counts.values())).font = Font(bold=True, size=10)
        current_row += 3

    _section("Flag Distribution",   Counter(_get(f, "final_flag", "unknown") for f in figures))
    _section("Caption Flag",        Counter(_get(f, "caption_flag", "unknown") for f in figures))
    _section("Confidence",          Counter(str(_get(f, "confidence", "N/A")) for f in figures))
    _section("QC Flag",             Counter(_get(f, "qc_flag", "QC_SKIPPED") for f in figures))
    _section("Figure Type",         Counter(_get(f, "fig_type", "Unknown") for f in figures))
    _section("Generation Status",   Counter(_get(f, "status", "unknown") for f in figures))
    _section("Processing Status",   Counter(_get(f, "processing_status", "PROCESSED") for f in figures))
    _section("Source Type",         Counter(_get(f, "source_type", "unknown") for f in figures))


# ── JSON ───────────────────────────────────────────────────────────────────────

def write_json(figures: list, output_path: str) -> str:
    output_path = Path(output_path)
    records = []
    for fig in figures:
        records.append({
            "source_file":        _get(fig, "source_file"),
            "source_type":        _get(fig, "source_type"),
            "xml_label":          _get(fig, "xml_label"),
            "page":               getattr(fig, "page_num", ""),
            "fig_id":             _get(fig, "fig_id"),
            "fig_type":           _get(fig, "fig_type"),
            "image":              _get(fig, "image_filename"),
            "caption":            _get(fig, "caption"),
            "caption_word_count": getattr(fig, "caption_word_count", None),
            "caption_flag":       _get(fig, "caption_flag"),
            "image_status":       _get(fig, "image_status"),
            "existing_alt":       _get(fig, "existing_alt"),
            "confidence":         _get(fig, "confidence"),
            "final_flag":         _get(fig, "final_flag"),
            "alt_text":           _get(fig, "alt_text"),
            "status":             _get(fig, "status"),
            "processing_status":  _get(fig, "processing_status", "PROCESSED"),
            "qc_flag":            _get(fig, "qc_flag"),
            "qc_notes":           _get(fig, "qc_notes"),
            "xml_fig_element_id": _get(fig, "xml_fig_element_id"),
        })
    output_path.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    print(f"📄 JSON saved: {output_path}")
    return str(output_path)


# ── Log file (spec section 6) ──────────────────────────────────────────────────

def write_log(figures: list, output_path: str, source_name: str = "") -> str:
    """
    Write a structured processing log to disk.
    Matches the sample output format from spec section 6.
    """
    output_path = Path(output_path)
    metrics     = _build_metrics(figures, source_name)
    log_text    = _format_metrics(metrics)

    output_path.write_text(log_text, encoding="utf-8")
    print(f"📋 Log saved: {output_path}")
    return str(output_path)


def _build_metrics(figures: list, source_name: str = "") -> dict:
    flag_counts = Counter(_get(f, "final_flag", "unknown") for f in figures)
    conf_counts = Counter(str(_get(f, "confidence", "N/A")) for f in figures)
    qc_counts   = Counter(_get(f, "qc_flag", "QC_SKIPPED") for f in figures)

    generated   = sum(1 for f in figures if _get(f, "status") == "done")
    already     = sum(1 for f in figures if _get(f, "status") == "skipped_existing_alt")
    skipped     = sum(1 for f in figures if _get(f, "status") == "skipped")
    errors      = sum(1 for f in figures if "error" in _get(f, "status").lower())
    passed_qc   = qc_counts.get("QC_PASS", 0)
    pass_rate   = f"{(passed_qc / generated * 100):.1f}%" if generated else "N/A"

    return {
        "source_name":  source_name,
        "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total":        len(figures),
        "flag_counts":  flag_counts,
        "conf_counts":  conf_counts,
        "qc_counts":    qc_counts,
        "generated":    generated,
        "already":      already,
        "skipped":      skipped,
        "errors":       errors,
        "passed_qc":    passed_qc,
        "pass_rate":    pass_rate,
    }


def _format_metrics(m: dict) -> str:
    label = f" — {m['source_name']}" if m["source_name"] else ""
    fc    = m["flag_counts"]
    cc    = m["conf_counts"]
    qc    = m["qc_counts"]

    return f"""========== PROCESSING SUMMARY{label} ==========
Timestamp     : {m['timestamp']}
Total Figures : {m['total']}

---------- VALIDATION ----------
OK                        : {fc.get('OK', 0)}
EXTERNAL_IMAGE            : {fc.get('EXTERNAL_IMAGE', 0)}
IMAGE_MISSING             : {fc.get('IMAGE_MISSING', 0)}
IMAGE_NOT_RESOLVED        : {fc.get('IMAGE_NOT_RESOLVED', 0)}
CAPTION_MISSING           : {fc.get('CAPTION_MISSING', 0)}
CAPTION_INSUFFICIENT      : {fc.get('CAPTION_INSUFFICIENT', 0)}
IMAGE_AND_CAPTION_MISSING : {fc.get('IMAGE_AND_CAPTION_MISSING', 0)}
ALT_ALREADY_PRESENT       : {fc.get('ALT_ALREADY_PRESENT', 0)}

---------- GENERATION ----------
Alt Text Generated        : {m['generated']}
Already Present (kept)    : {m['already']}
Skipped                   : {m['skipped']}
Errors                    : {m['errors']}

---------- CONFIDENCE ----------
HIGH                      : {cc.get('HIGH', 0)}
MEDIUM                    : {cc.get('MEDIUM', 0)}
LOW                       : {cc.get('LOW', 0)}

---------- QC ----------
QC_PASS                   : {m['passed_qc']}  ({m['pass_rate']} pass rate)
QC_TOO_SHORT              : {qc.get('QC_TOO_SHORT', 0)}
QC_TOO_LONG               : {qc.get('QC_TOO_LONG', 0)}
QC_FILLER_PHRASE          : {qc.get('QC_FILLER_PHRASE', 0)}
QC_MISSING_INSIGHT        : {qc.get('QC_MISSING_INSIGHT', 0)}
QC_MULTIPLE               : {qc.get('QC_MULTIPLE', 0)}
QC_SKIPPED                : {qc.get('QC_SKIPPED', 0)}

---------- SKIPPED ----------
Total Skipped             : {m['skipped']}

==========================================
"""


def print_metrics(figures: list, source_name: str = ""):
    """Print metrics to terminal (same format as log file)."""
    metrics  = _build_metrics(figures, source_name)
    log_text = _format_metrics(metrics)
    print(log_text)
