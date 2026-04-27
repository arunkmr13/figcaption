"""
extractor.py — Figure Extraction (PDF + XML)
─────────────────────────────────────────────
Stages 1 & 2: extract figures + associate captions from PDF or XML input.
Phase 2: Figure dataclass now carries full validation fields.
"""

import fitz
import io
import base64
from pathlib import Path
from PIL import Image
from dataclasses import dataclass, field
from typing import Optional
import xml.etree.ElementTree as ET

from .validator import validate_figure

# ── Extraction constants ─────────────────────────────────────────────────────
MIN_IMG_PX             = 200
CAPTION_V_THRESHOLD    = 150
CAPTION_KEYWORDS       = ["figure", "fig"]
MIN_PAGE_IMAGE_RATIO   = 0.15


# ── Figure dataclass (Phase 2 expanded) ─────────────────────────────────────
@dataclass
class Figure:
    # Source
    source_file: str          # original PDF or XML filename
    source_type: str          # "pdf" or "xml"
    page_num: int             # page number (PDF) or element index (XML)
    fig_id: str               # unique identifier

    # Content
    image_bytes: bytes        # raw PNG bytes
    image_filename: str       # saved filename in figures/
    caption: str              # matched caption text

    # Classification
    fig_type: str = "Unknown"

    # Phase 2 — Validation
    caption_flag: str = "PENDING"
    caption_word_count: int = 0
    image_status: str = "resolved"
    existing_alt: str = ""
    confidence: Optional[str] = None
    final_flag: str = "PENDING"
    eligible: bool = True

    # Output
    alt_text: str = ""
    status: str = "pending"       # pending | done | skipped | error
    processing_status: str = "PROCESSED"  # PROCESSED | ERROR

    # XML-specific (only populated for XML input)
    xml_fig_element_id: str = ""  # original id attr from <fig> element
    xml_label: str = ""           # <label> text from XML


# ── Figure type classifier ───────────────────────────────────────────────────
def _classify_figure(caption: str) -> str:
    lower = caption.lower()
    rules = [
        (["forest plot", "forest"], "Forest plot"),
        (["bar chart", "bar graph", "bar plot"], "Bar chart"),
        (["line graph", "line plot", "line chart"], "Line graph"),
        (["scatter plot", "scatter"], "Scatter plot"),
        (["diagram", "schematic", "flowchart"], "Diagram"),
        (["histogram"], "Histogram"),
        (["heatmap", "heat map"], "Heatmap"),
        (["kaplan", "survival curve"], "Survival curve"),
        (["box plot", "boxplot"], "Box plot"),
        (["pie chart", "pie graph"], "Pie chart"),
        (["table"], "Table figure"),
    ]
    for keywords, fig_type in rules:
        if any(kw in lower for kw in keywords):
            return fig_type
    return "Unknown"


# ── PDF helpers ──────────────────────────────────────────────────────────────
def _is_background_image(w, h, page_w, page_h):
    return w > page_w * 0.9 and h > page_h * 0.9


def _page_has_significant_text(page):
    return len(page.get_text().strip()) > 1500


def _extract_embedded_images(page, page_w, page_h):
    results = []
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        try:
            base_image = page.parent.extract_image(xref)
            w, h = base_image["width"], base_image["height"]
            if w < MIN_IMG_PX or h < MIN_IMG_PX:
                continue
            if _is_background_image(w, h, page_w, page_h):
                continue
            if (w * h) / (page_w * page_h) < MIN_PAGE_IMAGE_RATIO:
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


def _find_caption_pdf(page, img_rect):
    candidates = []
    for block in page.get_text("blocks"):
        bx0, by0, bx1, by1, text = block[0], block[1], block[2], block[3], block[4]
        text = text.strip()
        if not text:
            continue
        if not any(kw in text.lower() for kw in CAPTION_KEYWORDS):
            continue
        if img_rect is None:
            return text
        if by0 < img_rect.y1:
            continue
        dist = by0 - img_rect.y1
        if dist > CAPTION_V_THRESHOLD:
            continue
        candidates.append((dist, text))
    if candidates:
        return sorted(candidates)[0][1].replace("\n", " ").strip()
    return ""


# ── PDF extractor ─────────────────────────────────────────────────────────────
def extract_figures_from_pdf(pdf_path: str, output_dir: str = "figures") -> list:
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_name = pdf_path.stem
    doc = fitz.open(str(pdf_path))
    figures = []
    fig_counter = 1

    print(f"\n📄 Processing PDF: {pdf_path.name} ({len(doc)} pages)")

    for page_num, page in enumerate(doc, start=1):
        page_rect = page.rect
        page_w, page_h = page_rect.width, page_rect.height
        print(f"  Page {page_num}...", end=" ")

        images = _extract_embedded_images(page, page_w, page_h)
        if not images:
            if _page_has_significant_text(page):
                print("(text page, skipped)")
                continue
            print("(fallback render)", end=" ")
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)
            images = [(pix.tobytes("png"), page.rect)]

        print(f"{len(images)} figure(s) found")

        for png_bytes, bbox in images:
            fig_id = f"{pdf_name}_p{page_num:03d}_f{fig_counter:03d}"
            img_filename = f"{fig_id}.png"
            try:
                (output_dir / img_filename).write_bytes(png_bytes)
                image_resolved = True
            except Exception as e:
                print(f"  [WARN] Could not save {img_filename}: {e}")
                image_resolved = False

            caption = _find_caption_pdf(page, bbox) or ""
            fig_type = _classify_figure(caption)

            # Phase 2 validation
            v = validate_figure(
                caption=caption,
                image_bytes=png_bytes,
                image_resolved=image_resolved,
            )

            figures.append(Figure(
                source_file=pdf_path.name,
                source_type="pdf",
                page_num=page_num,
                fig_id=fig_id,
                image_bytes=png_bytes,
                image_filename=img_filename,
                caption=caption or "No caption available",
                fig_type=fig_type,
                caption_flag=v["caption_flag"],
                caption_word_count=v["caption_word_count"],
                image_status=v["image_status"],
                confidence=v["confidence"],
                final_flag=v["final_flag"],
                eligible=v["eligible"],
            ))
            fig_counter += 1

    doc.close()
    print(f"\n✅ Extracted {len(figures)} figure(s) from {pdf_path.name}")
    return figures


# ── XML helpers ───────────────────────────────────────────────────────────────
# Supports JATS/NLM XML (used by APS and most STM publishers)
# Namespace-agnostic: strips {ns} prefixes before matching tags.

def _strip_ns(tag: str) -> str:
    """Strip XML namespace from tag: {http://...}fig → fig"""
    return tag.split("}")[-1] if "}" in tag else tag


def _find_all_ns(element, tag: str):
    """Find all descendants matching tag, ignoring namespace."""
    return [el for el in element.iter() if _strip_ns(el.tag) == tag]


def _get_text_ns(element, tag: str) -> str:
    """Get text content of first child with given tag (ns-agnostic)."""
    for child in element:
        if _strip_ns(child.tag) == tag:
            return "".join(child.itertext()).strip()
    return ""


def _decode_image_from_xml(graphic_el) -> Optional[bytes]:
    """
    Try to decode inline base64 image from <inline-graphic> or <graphic>.
    Supports: base64-encoded content in element text, or xlink:href data URIs.
    """
    # Try xlink:href for data URI
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

    # Try element text (some formats embed base64 directly)
    text = (graphic_el.text or "").strip()
    if text:
        try:
            return base64.b64decode(text)
        except Exception:
            pass

    return None


def _make_placeholder_png() -> bytes:
    """1x1 transparent PNG for XML figures where image is referenced externally."""
    img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── XML extractor ─────────────────────────────────────────────────────────────
def extract_figures_from_xml(xml_path: str, output_dir: str = "figures") -> list:
    """
    Extract figures from a JATS/NLM XML file.
    Handles both inline base64 images and external image references.
    """
    xml_path = Path(xml_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    xml_name = xml_path.stem

    print(f"\n📋 Processing XML: {xml_path.name}")

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"  [ERROR] Failed to parse XML: {e}")
        return []

    fig_elements = _find_all_ns(root, "fig")
    print(f"  Found {len(fig_elements)} <fig> element(s)")

    figures = []
    fig_counter = 1

    for idx, fig_el in enumerate(fig_elements, start=1):
        fig_id_attr = fig_el.get("id", "")
        label_text = _get_text_ns(fig_el, "label")

        # Build caption from <caption><p>...</p></caption>
        caption_parts = []
        for cap_el in fig_el:
            if _strip_ns(cap_el.tag) == "caption":
                caption_parts.append("".join(cap_el.itertext()).strip())
        caption = " ".join(caption_parts).strip()
        if not caption and label_text:
            caption = label_text

        # Try to get inline image
        png_bytes = None
        image_resolved = False
        external_href = ""

        graphic_els = [
            child for child in fig_el
            if _strip_ns(child.tag) in ("graphic", "inline-graphic")
        ]

        for g in graphic_els:
            decoded = _decode_image_from_xml(g)
            if decoded:
                # Convert to PNG if needed
                try:
                    img = Image.open(io.BytesIO(decoded))
                    if img.mode == "CMYK":
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    png_bytes = buf.getvalue()
                    image_resolved = True
                    break
                except Exception:
                    pass
            else:
                # Record external href for reporting
                for attr_name, attr_val in g.attrib.items():
                    if "href" in attr_name.lower():
                        external_href = attr_val
                        break

        # Check for existing alt text in XML
        existing_alt = ""
        for alt_el in fig_el.iter():
            if _strip_ns(alt_el.tag) == "alt-text":
                existing_alt = "".join(alt_el.itertext()).strip()
                break

        # If no inline image, use placeholder (external reference)
        has_image = png_bytes is not None
        if not has_image:
            png_bytes = _make_placeholder_png()

        # Phase 2 validation
        v = validate_figure(
            caption=caption,
            image_bytes=png_bytes if has_image else None,
            image_resolved=image_resolved,
            existing_alt=existing_alt,
        )

        fig_id = f"{xml_name}_fig{fig_counter:03d}"
        img_filename = f"{fig_id}.png"

        # Only save real images (not placeholders)
        if has_image:
            try:
                (output_dir / img_filename).write_bytes(png_bytes)
            except Exception as e:
                print(f"  [WARN] Could not save {img_filename}: {e}")
        else:
            img_filename = f"[external] {external_href}" if external_href else "[no image]"

        fig_type = _classify_figure(caption)

        print(f"  <fig> {fig_counter}: {fig_id} — flag={v['final_flag']} caption_words={v['caption_word_count']}")

        figures.append(Figure(
            source_file=xml_path.name,
            source_type="xml",
            page_num=idx,
            fig_id=fig_id,
            image_bytes=png_bytes if has_image else b"",
            image_filename=img_filename,
            caption=caption or "No caption available",
            fig_type=fig_type,
            caption_flag=v["caption_flag"],
            caption_word_count=v["caption_word_count"],
            image_status=v["image_status"],
            existing_alt=existing_alt,
            confidence=v["confidence"],
            final_flag=v["final_flag"],
            eligible=v["eligible"],
            xml_fig_element_id=fig_id_attr,
            xml_label=label_text,
        ))
        fig_counter += 1

    print(f"\n✅ Extracted {len(figures)} figure(s) from {xml_path.name}")
    return figures


# ── Unified entry point ───────────────────────────────────────────────────────
def extract_figures(input_path: str, output_dir: str = "figures") -> list:
    """
    Auto-detect input type (PDF or XML) and dispatch to the right extractor.
    """
    path = Path(input_path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return extract_figures_from_pdf(input_path, output_dir)
    elif suffix == ".xml":
        return extract_figures_from_xml(input_path, output_dir)
    else:
        raise ValueError(f"Unsupported input format: {suffix}. Expected .pdf or .xml")