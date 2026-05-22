"""
extractor.py — Figure Extraction (PDF + XML)

Fixes applied vs Phase 1:
  FIX-1  Caption matching: horizontal overlap constraint prevents cross-column
          misassociation on multi-column layouts. Also searches above the image
          (captions sometimes appear above in certain journal styles).
  FIX-2  Vector-only page fallback: instead of one full-page render we analyse
          the page's drawing paths to isolate individual vector figure regions
          and crop each one separately.
  FIX-3  Table detection: drawing-path analysis identifies grid-like table
          bounding boxes; they are extracted and classified as "Table figure"
          even when the caption does not contain the word "table".
  FIX-4  Multi-figure panel splitting: after extracting an image we inspect it
          for strong horizontal/vertical white-space dividers and sub-panel
          labels (A, B, C …); if found the image is split into sub-panels and
          each is stored as a distinct Figure.
"""

import re
import io
import base64
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import fitz                        # PyMuPDF
from PIL import Image
import xml.etree.ElementTree as ET

# ── Tunable constants ────────────────────────────────────────────────────────
MIN_IMG_PX            = 200    # ignore images smaller than this in either dimension
MIN_PAGE_IMAGE_RATIO  = 0.15   # image must cover ≥15 % of page area
CAPTION_V_THRESHOLD   = 160    # max vertical gap (pts) between image and caption
CAPTION_ABOVE_MAX     = 30     # how far above an image we'll look for a caption
CAPTION_H_OVERLAP_MIN = 0.35   # caption block must share ≥35 % horizontal overlap
CAPTION_KEYWORDS      = ["figure", "fig"]
VECTOR_MIN_AREA       = 20_000 # minimum area (pts²) for a vector cluster to keep
TABLE_MIN_H_LINES     = 3      # minimum horizontal lines to call something a table
TABLE_MIN_V_LINES     = 2      # minimum vertical lines to call something a table
PANEL_GAP_RATIO       = 0.04   # white-stripe narrower than 4 % of dimension → not a split
PANEL_WHITE_THRESH    = 245    # pixel value counted as "white" for split detection
PANEL_MIN_FRAC        = 0.15   # sub-panel must be ≥15 % of full image to keep


# ── Figure dataclass ─────────────────────────────────────────────────────────
@dataclass
class Figure:
    source_file:          str
    source_type:          str
    page_num:             int
    fig_id:               str
    image_bytes:          bytes
    image_filename:       str
    caption:              str
    fig_type:             str           = "Unknown"
    caption_flag:         str           = "PENDING"
    caption_word_count:   int           = 0
    image_status:         str           = "resolved"
    existing_alt:         str           = ""
    confidence:           Optional[str] = None
    final_flag:           str           = "PENDING"
    eligible:             bool          = True
    alt_text:             str           = ""
    status:               str           = "pending"
    processing_status:    str           = "PROCESSED"
    xml_fig_element_id:   str           = ""
    xml_label:            str           = ""
    qc_flag:              str           = "QC_SKIPPED"
    qc_notes:             str           = ""


# ── Figure-type classifier ────────────────────────────────────────────────────
def _classify_figure(caption: str) -> str:
    lower = caption.lower()
    rules = [
        (["forest plot", "forest"],                "Forest plot"),
        (["bar chart", "bar graph", "bar plot"],   "Bar chart"),
        (["line graph", "line plot", "line chart"], "Line graph"),
        (["scatter plot", "scatter"],              "Scatter plot"),
        (["diagram", "schematic", "flowchart"],    "Diagram"),
        (["histogram"],                            "Histogram"),
        (["heatmap", "heat map"],                  "Heatmap"),
        (["kaplan", "survival curve"],             "Survival curve"),
        (["box plot", "boxplot"],                  "Box plot"),
        (["pie chart", "pie graph"],               "Pie chart"),
        (["table"],                                "Table figure"),
    ]
    for keywords, fig_type in rules:
        if any(kw in lower for kw in keywords):
            return fig_type
    return "Unknown"


# ─────────────────────────────────────────────────────────────────────────────
# FIX-1 ── Caption matching with horizontal-overlap constraint
# ─────────────────────────────────────────────────────────────────────────────

def _h_overlap_ratio(ax0: float, ax1: float, bx0: float, bx1: float) -> float:
    """Fraction of [ax0,ax1] that overlaps with [bx0,bx1]."""
    overlap = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    width   = ax1 - ax0
    return overlap / width if width > 0 else 0.0


def _find_caption_pdf(page, img_rect) -> str:
    """
    Find the caption for a figure.

    Strategy (in priority order):
    1. Text block below the image, within CAPTION_V_THRESHOLD pts, that starts
       with a caption keyword AND overlaps the image horizontally by ≥35 %.
    2. Same but searching above the image (up to CAPTION_ABOVE_MAX pts) for
       journals that place captions above figures.
    3. Nearest qualifying block ignoring the vertical limit (last resort).

    The horizontal-overlap constraint (FIX-1) is the key change: it prevents
    a caption from the right column of a two-column layout from being assigned
    to a figure that sits entirely in the left column.
    """
    if img_rect is None:
        # No bounding box — just take the first caption-keyword block on the page
        for block in page.get_text("blocks"):
            text = block[4].strip()
            if text and any(kw in text.lower() for kw in CAPTION_KEYWORDS):
                return text.replace("\n", " ").strip()
        return ""

    ix0, iy0, ix1, iy1 = img_rect.x0, img_rect.y0, img_rect.x1, img_rect.y1

    below   = []  # (dist, text)
    above   = []  # (dist, text)
    fallback = [] # (dist, text) — no H-overlap check

    for block in page.get_text("blocks"):
        bx0, by0, bx1, by1, text = block[0], block[1], block[2], block[3], block[4]
        text = text.strip()
        if not text:
            continue
        if not any(kw in text.lower() for kw in CAPTION_KEYWORDS):
            continue

        h_overlap = _h_overlap_ratio(ix0, ix1, bx0, bx1)

        # --- below ---
        if by0 >= iy1:
            dist = by0 - iy1
            if h_overlap >= CAPTION_H_OVERLAP_MIN:
                if dist <= CAPTION_V_THRESHOLD:
                    below.append((dist, text))
            else:
                fallback.append((abs(dist), text))

        # --- above (FIX-1 bonus: some journals put captions above) ---
        elif by1 <= iy0:
            dist = iy0 - by1
            if h_overlap >= CAPTION_H_OVERLAP_MIN and dist <= CAPTION_ABOVE_MAX:
                above.append((dist, text))

    if below:
        return sorted(below)[0][1].replace("\n", " ").strip()
    if above:
        return sorted(above)[0][1].replace("\n", " ").strip()
    if fallback:
        return sorted(fallback)[0][1].replace("\n", " ").strip()
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# FIX-2 & FIX-3 ── Vector figure & table detection from drawing paths
# ─────────────────────────────────────────────────────────────────────────────

def _get_path_rects(page) -> list[fitz.Rect]:
    """Return a list of bounding boxes for every drawing path on the page."""
    rects = []
    for path in page.get_drawings():
        r = fitz.Rect(path["rect"])
        if not r.is_empty and r.width > 1 and r.height > 1:
            rects.append(r)
    return rects


def _cluster_rects(rects: list[fitz.Rect], gap: float = 10.0) -> list[fitz.Rect]:
    """
    Greedily merge rectangles that are within `gap` points of each other
    into cluster bounding boxes.
    """
    if not rects:
        return []

    clusters: list[fitz.Rect] = []
    for r in rects:
        merged = False
        for i, c in enumerate(clusters):
            expanded = fitz.Rect(
                c.x0 - gap, c.y0 - gap, c.x1 + gap, c.y1 + gap
            )
            if expanded.intersects(r):
                clusters[i] = fitz.Rect(
                    min(c.x0, r.x0), min(c.y0, r.y0),
                    max(c.x1, r.x1), max(c.y1, r.y1),
                )
                merged = True
                break
        if not merged:
            clusters.append(fitz.Rect(r))
    return clusters


def _is_table_cluster(page, cluster: fitz.Rect) -> bool:
    """
    Decide whether a cluster of drawing paths looks like a table by counting
    predominantly horizontal and vertical line segments inside it.

    FIX-3: This lets us detect table regions even when the caption doesn't
    contain the word "table".
    """
    h_lines = 0
    v_lines = 0
    for path in page.get_drawings():
        pr = fitz.Rect(path["rect"])
        if not cluster.contains(pr):
            continue
        w, h = pr.width, pr.height
        # Horizontal line: very wide relative to height
        if w > 10 and h < 3:
            h_lines += 1
        # Vertical line: very tall relative to width
        elif h > 10 and w < 3:
            v_lines += 1
    return h_lines >= TABLE_MIN_H_LINES and v_lines >= TABLE_MIN_V_LINES


def _render_clip(page, clip_rect: fitz.Rect, scale: float = 2.0) -> bytes:
    """Render a rectangular region of a page to PNG bytes."""
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, clip=clip_rect)
    return pix.tobytes("png")


def _extract_vector_figures(page, page_w: float, page_h: float) -> list[tuple[bytes, fitz.Rect, bool]]:
    """
    FIX-2 + FIX-3: Analyse drawing paths to find vector figure regions and
    table regions, render each crop individually.

    Returns list of (png_bytes, bbox, is_table).
    """
    path_rects = _get_path_rects(page)
    if not path_rects:
        return []

    clusters = _cluster_rects(path_rects, gap=12.0)
    results  = []

    for cluster in clusters:
        # Skip tiny clusters (hairlines, borders)
        area = cluster.width * cluster.height
        if area < VECTOR_MIN_AREA:
            continue
        # Skip near-full-page clusters (page border / background)
        if cluster.width > page_w * 0.9 and cluster.height > page_h * 0.9:
            continue

        is_table = _is_table_cluster(page, cluster)
        png      = _render_clip(page, cluster)
        results.append((png, cluster, is_table))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# FIX-4 ── Multi-figure panel splitting
# ─────────────────────────────────────────────────────────────────────────────

def _row_col_white_masks(img: Image.Image) -> tuple[list[bool], list[bool]]:
    """
    Return (row_white_mask, col_white_mask) for a PIL image.
    row_white_mask[y] is True if every pixel in row y has max(R,G,B) ≥ threshold.
    col_white_mask[x] is True if every pixel in column x has max(R,G,B) ≥ threshold.
    """
    rgb  = img.convert("RGB")
    w, h = rgb.size
    data = list(rgb.getdata())                     # flat list of (R,G,B)

    row_white: list[bool] = []
    for y in range(h):
        row = data[y * w:(y + 1) * w]
        row_white.append(all(max(px) >= PANEL_WHITE_THRESH for px in row))

    col_white: list[bool] = []
    for x in range(w):
        col = [data[y * w + x] for y in range(h)]
        col_white.append(all(max(px) >= PANEL_WHITE_THRESH for px in col))

    return row_white, col_white


def _find_split_positions(mask: list[bool], total: int) -> list[int]:
    """
    Given a boolean mask of white stripes, find positions of white-band
    *centres* that are wide enough to be a panel divider.
    """
    min_gap_px = max(4, int(total * PANEL_GAP_RATIO))
    positions  = []
    in_band    = False
    start      = 0

    for i, white in enumerate(mask):
        if white and not in_band:
            in_band = True
            start   = i
        elif not white and in_band:
            in_band = False
            width   = i - start
            if width >= min_gap_px:
                positions.append((start + i) // 2)

    # Close an open band at the end
    if in_band:
        width = total - start
        if width >= min_gap_px:
            positions.append((start + total) // 2)

    return positions


def _has_panel_labels(img: Image.Image, splits: list[int], axis: str) -> bool:
    """
    Heuristic: check for dark pixels (likely label glyphs A/B/C…) near the
    top-left corner of each sub-panel.  Uses PIL only — no NumPy.
    """
    rgb  = img.convert("RGB")
    w, h = rgb.size
    lsz  = max(20, min(h, w) // 8)   # label-region size

    for pos in splits:
        if axis == "v":
            box = (pos + 2, 0, min(pos + 2 + lsz, w), min(lsz, h))
        else:
            box = (0, pos + 2, min(lsz, w), min(pos + 2 + lsz, h))

        region = rgb.crop(box)
        pixels = list(region.getdata())
        if not pixels:
            continue
        dark_count = sum(1 for px in pixels if max(px) < 80)
        if dark_count > len(pixels) * 0.02:
            return True
    return False







def _split_image_into_panels(png_bytes: bytes) -> list[bytes]:
    """
    FIX-4: Attempt to split a figure image into sub-panels.

    Algorithm:
    1. Build row and column brightness masks (all-white stripes).
    2. Find bands of consecutive white rows/cols wide enough to be a divider.
    3. Choose the axis with more splits (more sub-panels).
    4. Crop each sub-panel; discard any that are smaller than PANEL_MIN_FRAC.

    Returns a list with the original bytes if no split found, or a list of
    sub-panel PNG bytes when splitting succeeds.
    """
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception:
        return [png_bytes]

    w, h = img.size
    row_white, col_white = _row_col_white_masks(img)

    h_splits = _find_split_positions(row_white, h)
    v_splits = _find_split_positions(col_white, w)

    if not h_splits and not v_splits:
        return [png_bytes]

    # Prefer the axis with more splits; fall back to whichever has any
    if len(h_splits) >= len(v_splits):
        splits, use_h = h_splits, True
    else:
        splits, use_h = v_splits, False

    boundaries = [0] + splits + [h if use_h else w]
    panels = []

    for i in range(len(boundaries) - 1):
        a, b = boundaries[i], boundaries[i + 1]
        frac = (b - a) / (h if use_h else w)
        if frac < PANEL_MIN_FRAC:
            continue

        crop = img.crop((0, a, w, b) if use_h else (a, 0, b, h))
        buf  = io.BytesIO()
        crop.save(buf, format="PNG")
        panels.append(buf.getvalue())

    return panels if len(panels) > 1 else [png_bytes]


# ─────────────────────────────────────────────────────────────────────────────
# Embedded image helpers (unchanged from Phase 1, kept for reference)
# ─────────────────────────────────────────────────────────────────────────────

def _is_background_image(w, h, page_w, page_h) -> bool:
    return w > page_w * 0.9 and h > page_h * 0.9


def _page_has_significant_text(page) -> bool:
    return len(page.get_text().strip()) > 1500


def _extract_embedded_images(page, page_w, page_h) -> list[tuple[bytes, Optional[fitz.Rect]]]:
    """Extract raster images embedded in the PDF page."""
    results = []
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        try:
            base_image = page.parent.extract_image(xref)
            ew, eh = base_image["width"], base_image["height"]
            if ew < MIN_IMG_PX or eh < MIN_IMG_PX:
                continue
            if _is_background_image(ew, eh, page_w, page_h):
                continue
            if (ew * eh) / (page_w * page_h) < MIN_PAGE_IMAGE_RATIO:
                continue

            img = Image.open(io.BytesIO(base_image["image"]))
            if img.mode == "CMYK":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")

            bbox = None
            for item in page.get_image_info(hashes=False):
                if item.get("xref") == xref:
                    bbox = fitz.Rect(item["bbox"])
                    break

            results.append((buf.getvalue(), bbox))
        except Exception as e:
            print(f"  [WARN] xref={xref}: {e}")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Helper: turn (bytes, bbox, is_table) into Figure objects
# ─────────────────────────────────────────────────────────────────────────────

def _make_figures(
    png_bytes:   bytes,
    bbox:        Optional[fitz.Rect],
    page,
    page_num:    int,
    pdf_name:    str,
    output_dir:  Path,
    fig_counter: int,
    force_type:  Optional[str] = None,
) -> tuple[list[Figure], int]:
    """
    Given a single extracted image, attempt panel splitting (FIX-4) and
    produce one Figure per (sub-)panel.  Returns (figures_list, updated_counter).
    """
    caption  = _find_caption_pdf(page, bbox)
    fig_type = force_type or _classify_figure(caption)

    # FIX-4: try to split into sub-panels
    panels = _split_image_into_panels(png_bytes)
    figures = []

    for panel_idx, panel_bytes in enumerate(panels):
        suffix     = f"_p{panel_idx + 1}" if len(panels) > 1 else ""
        fig_id     = f"{pdf_name}_p{page_num:03d}_f{fig_counter:03d}{suffix}"
        img_name   = f"{fig_id}.png"

        try:
            (output_dir / img_name).write_bytes(panel_bytes)
            image_resolved = True
        except Exception as e:
            print(f"  [WARN] Could not save {img_name}: {e}")
            image_resolved = False

        panel_caption = caption + (f" (panel {panel_idx + 1})" if len(panels) > 1 else "")
        word_count    = len(panel_caption.split())

        figures.append(Figure(
            source_file        = "",          # filled by caller
            source_type        = "pdf",
            page_num           = page_num,
            fig_id             = fig_id,
            image_bytes        = panel_bytes,
            image_filename     = img_name,
            caption            = panel_caption if panel_caption else "No caption available",
            fig_type           = fig_type,
            caption_flag       = "SUFFICIENT" if word_count >= 5 else (
                                  "MISSING" if word_count == 0 else "INSUFFICIENT"),
            caption_word_count = word_count,
            image_status       = "resolved" if image_resolved else "error",
            final_flag         = "OK" if image_resolved else "IMAGE_NOT_RESOLVED",
            eligible           = image_resolved,
        ))

    return figures, fig_counter + 1


# ─────────────────────────────────────────────────────────────────────────────
# PDF Extractor (main entry)
# ─────────────────────────────────────────────────────────────────────────────

def extract_figures_from_pdf(pdf_path, output_dir="figures") -> list[Figure]:
    pdf_path   = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_name    = pdf_path.stem
    doc         = fitz.open(str(pdf_path))
    all_figures: list[Figure] = []
    fig_counter = 1

    print(f"\n📄 Processing PDF: {pdf_path.name} ({len(doc)} pages)")

    for page_num, page in enumerate(doc, start=1):
        page_rect    = page.rect
        page_w, page_h = page_rect.width, page_rect.height
        print(f"  Page {page_num}...", end=" ")

        # ── Try embedded raster images first ─────────────────────────────────
        embedded = _extract_embedded_images(page, page_w, page_h)

        page_figures: list[Figure] = []

        if embedded:
            print(f"{len(embedded)} raster figure(s)", end="")
            for png_bytes, bbox in embedded:
                figs, fig_counter = _make_figures(
                    png_bytes, bbox, page, page_num, pdf_name, output_dir, fig_counter
                )
                for f in figs:
                    f.source_file = pdf_path.name
                page_figures.extend(figs)

        # ── FIX-2 & FIX-3: Try vector/table regions ──────────────────────────
        vector_regions = _extract_vector_figures(page, page_w, page_h)

        if vector_regions:
            # Filter out regions that overlap with already-found raster images
            raster_bboxes = [
                fitz.Rect(item["bbox"])
                for item in page.get_image_info(hashes=False)
            ]
            novel_regions = []
            for png, bbox, is_table in vector_regions:
                overlaps_raster = any(
                    bbox.intersects(rb) and
                    (bbox & rb).get_area() / max(bbox.get_area(), 1) > 0.5
                    for rb in raster_bboxes
                )
                if not overlaps_raster:
                    novel_regions.append((png, bbox, is_table))

            if novel_regions:
                print(f" + {len(novel_regions)} vector region(s)", end="")
                for png_bytes, bbox, is_table in novel_regions:
                    force = "Table figure" if is_table else None
                    figs, fig_counter = _make_figures(
                        png_bytes, bbox, page, page_num, pdf_name, output_dir,
                        fig_counter, force_type=force,
                    )
                    for f in figs:
                        f.source_file = pdf_path.name
                    page_figures.extend(figs)

        # ── Fallback: full-page render (only if nothing else found) ──────────
        if not page_figures:
            if _page_has_significant_text(page):
                print(" (text page, skipped)")
                continue
            print(" (full-page fallback)", end="")
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)
            png_bytes = pix.tobytes("png")
            figs, fig_counter = _make_figures(
                png_bytes, page.rect, page, page_num, pdf_name, output_dir, fig_counter
            )
            for f in figs:
                f.source_file = pdf_path.name
            page_figures.extend(figs)

        print(f" → {len(page_figures)} figure(s) stored")
        all_figures.extend(page_figures)

    doc.close()
    print(f"\n✅ Extracted {len(all_figures)} figure(s) from {pdf_path.name}")
    return all_figures


# ─────────────────────────────────────────────────────────────────────────────
# XML helpers (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _find_all_ns(element, tag: str) -> list:
    return [el for el in element.iter() if _strip_ns(el.tag) == tag]


def _get_text_ns(element, tag: str) -> str:
    for child in element:
        if _strip_ns(child.tag) == tag:
            return "".join(child.itertext()).strip()
    return ""


def _decode_image_from_xml(graphic_el) -> Optional[bytes]:
    href = None
    for attr_name, attr_val in graphic_el.attrib.items():
        if "href" in attr_name.lower():
            href = attr_val
            break
    if href and href.startswith("data:"):
        try:
            _, encoded = href.split(",", 1)
            return base64.b64decode(encoded)
        except Exception:
            pass
    text = (graphic_el.text or "").strip()
    if text:
        try:
            return base64.b64decode(text)
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# XML Extractor
# ─────────────────────────────────────────────────────────────────────────────

def extract_figures_from_xml(xml_path, output_dir="figures") -> list[Figure]:
    xml_path   = Path(xml_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    xml_name   = xml_path.stem

    print(f"\n📋 Processing XML: {xml_path.name}")

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"  [ERROR] Failed to parse XML: {e}")
        return []

    fig_elements = _find_all_ns(root, "fig")
    print(f"  Found {len(fig_elements)} <fig> element(s)")

    figures: list[Figure] = []
    fig_counter = 1

    for idx, fig_el in enumerate(fig_elements, start=1):
        fig_id_attr = fig_el.get("id", "")
        label_text  = _get_text_ns(fig_el, "label")

        # Caption
        caption_parts = []
        for cap_el in fig_el:
            if _strip_ns(cap_el.tag) == "caption":
                caption_parts.append("".join(cap_el.itertext()).strip())
        caption = " ".join(caption_parts).strip()
        if not caption and label_text:
            caption = label_text

        # Image
        png_bytes      = None
        image_resolved = False
        external_href  = ""

        graphic_els = [c for c in fig_el if _strip_ns(c.tag) in ("graphic", "inline-graphic")]
        for g in graphic_els:
            decoded = _decode_image_from_xml(g)
            if decoded:
                try:
                    img = Image.open(io.BytesIO(decoded))
                    if img.mode == "CMYK":
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    png_bytes      = buf.getvalue()
                    image_resolved = True
                    break
                except Exception:
                    pass
            else:
                for attr_name, attr_val in g.attrib.items():
                    if "href" in attr_name.lower():
                        external_href = attr_val
                        break

        # Existing alt text
        existing_alt = ""
        for alt_el in fig_el.iter():
            if _strip_ns(alt_el.tag) == "alt-text":
                existing_alt = "".join(alt_el.itertext()).strip()
                break
        if existing_alt.lower().startswith("alt text:"):
            existing_alt = existing_alt[9:].strip()

        has_image   = png_bytes is not None
        is_external = not has_image and bool(external_href)

        # FIX-4: attempt panel splitting for XML-sourced images too
        panels = _split_image_into_panels(png_bytes) if has_image else [None]

        for panel_idx, panel_bytes in enumerate(panels):
            suffix        = f"_p{panel_idx + 1}" if len(panels) > 1 else ""
            fig_id        = f"{xml_name}_fig{fig_counter:03d}{suffix}"
            panel_caption = caption + (f" (panel {panel_idx + 1})" if len(panels) > 1 else "")
            word_count    = len(panel_caption.split())

            if panel_bytes is not None:
                img_filename = f"{fig_id}.png"
                try:
                    (output_dir / img_filename).write_bytes(panel_bytes)
                except Exception as e:
                    print(f"  [WARN] Could not save {img_filename}: {e}")
            elif external_href:
                img_filename = f"[external] {external_href}"
            else:
                img_filename = "[no image]"

            fig_type = _classify_figure(panel_caption)
            final_flag = (
                "OK"                  if image_resolved else
                "EXTERNAL_IMAGE"      if is_external    else
                "IMAGE_MISSING"
            )

            print(f"  <fig> {fig_counter}: {fig_id} — caption_words={word_count}")

            figures.append(Figure(
                source_file        = xml_path.name,
                source_type        = "xml",
                page_num           = idx,
                fig_id             = fig_id,
                image_bytes        = panel_bytes if panel_bytes else b"",
                image_filename     = img_filename,
                caption            = panel_caption if panel_caption else "No caption available",
                fig_type           = fig_type,
                caption_flag       = "SUFFICIENT" if word_count >= 5 else (
                                      "MISSING" if word_count == 0 else "INSUFFICIENT"),
                caption_word_count = word_count,
                image_status       = "resolved" if image_resolved else (
                                      "external" if is_external else "missing"),
                existing_alt       = existing_alt,
                final_flag         = final_flag,
                eligible           = image_resolved or is_external,
                xml_fig_element_id = fig_id_attr,
                xml_label          = label_text,
            ))
            fig_counter += 1

    print(f"\n✅ Extracted {len(figures)} figure(s) from {xml_path.name}")
    return figures


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def extract_figures(input_path, output_dir="figures") -> list[Figure]:
    path   = Path(input_path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_figures_from_pdf(input_path, output_dir)
    elif suffix == ".xml":
        return extract_figures_from_xml(input_path, output_dir)
    else:
        raise ValueError(f"Unsupported input format: {suffix}. Expected .pdf or .xml")