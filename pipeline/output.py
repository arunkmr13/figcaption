import json
from pathlib import Path
from datetime import datetime
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from extractor import Figure

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False
    print("[WARN] openpyxl not installed — Excel output disabled")

HEADER_FILL   = "2D5FA0"
DONE_FILL     = "E8F5E9"
SKIP_FILL     = "FFF9C4"
ERROR_FILL    = "FFEBEE"
HEADER_FONT   = "FFFFFF"
ALT_ROW_FILL  = "F5F8FF"

COLUMNS = [
    ("pdf",      18, "Source PDF"),
    ("page",      6, "Page"),
    ("fig_id",   30, "Figure ID"),
    ("fig_type", 18, "Type"),
    ("image",    32, "Image File"),
    ("caption",  50, "Caption"),
    ("alt_text", 60, "Alt Text"),
    ("status",   12, "Status"),
]


def _header_style(cell, fg_hex=HEADER_FILL, font_hex=HEADER_FONT):
    cell.fill = PatternFill("solid", fgColor=fg_hex)
    cell.font = Font(bold=True, color=font_hex, size=10)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _row_fill(status: str) -> str:
    if status == "done": return DONE_FILL
    if status == "skipped": return SKIP_FILL
    if "error" in status.lower(): return ERROR_FILL
    return "FFFFFF"


def write_excel(figures: list, output_path: str) -> str:
    if not HAS_OPENPYXL:
        print("[SKIP] openpyxl not available, skipping Excel output")
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
        print(f"[WARN] {output_path.name} is locked — saved as: {fallback.name}")
        return str(fallback)


def _write_alt_text_sheet(wb, figures):
    ws = wb.active
    ws.title = "Alt Text"
    ws.freeze_panes = "A2"
    for col_idx, (key, width, label) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        _header_style(cell)
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[1].height = 22
    for row_idx, fig in enumerate(figures, start=2):
        fill_hex = _row_fill(fig.status) if row_idx % 2 == 0 else (
            ALT_ROW_FILL if fig.status == "done" else _row_fill(fig.status)
        )
        fill = PatternFill("solid", fgColor=fill_hex)
        values = [fig.pdf_name, fig.page_num, fig.fig_id, fig.fig_type,
                  fig.image_filename, fig.caption, fig.alt_text, fig.status]
        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.fill = fill
            cell.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
            cell.font = Font(size=10)
        ws.row_dimensions[row_idx].height = 40


def _write_summary_sheet(wb, figures):
    ws = wb.create_sheet("Summary")
    type_counts = Counter(f.fig_type for f in figures)
    status_counts = Counter(f.status for f in figures)
    current_row = 1
    for section_name, counts in [("Figure Type", type_counts), ("Status", status_counts)]:
        h1 = ws.cell(row=current_row, column=1, value=section_name)
        h2 = ws.cell(row=current_row, column=2, value="Count")
        _header_style(h1); _header_style(h2)
        ws.column_dimensions["A"].width = 24
        ws.column_dimensions["B"].width = 12
        current_row += 1
        for label, count in sorted(counts.items(), key=lambda x: -x[1]):
            ws.cell(row=current_row, column=1, value=label).font = Font(size=10)
            ws.cell(row=current_row, column=2, value=count).font = Font(size=10)
            current_row += 1
        total_cell = ws.cell(row=current_row, column=1, value="TOTAL")
        total_cell.font = Font(bold=True, size=10)
        ws.cell(row=current_row, column=2, value=sum(counts.values())).font = Font(bold=True, size=10)
        current_row += 3


def write_json(figures: list, output_path: str) -> str:
    output_path = Path(output_path)
    records = [{"pdf": f.pdf_name, "page": f.page_num, "fig_id": f.fig_id,
                "fig_type": f.fig_type, "image": f.image_filename,
                "caption": f.caption, "alt_text": f.alt_text, "status": f.status}
               for f in figures]
    output_path.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    print(f"📄 JSON saved: {output_path}")
    return str(output_path)
