import fitz
import io
from pathlib import Path
from PIL import Image
from dataclasses import dataclass
from typing import Optional

MIN_IMG_PX = 200
CAPTION_V_THRESHOLD = 150
CAPTION_KEYWORDS = ["figure", "fig"]
MIN_PAGE_IMAGE_RATIO = 0.15

@dataclass
class Figure:
    pdf_name: str
    page_num: int
    fig_id: str
    image_bytes: bytes
    image_filename: str
    caption: str
    fig_type: str = "unknown"
    alt_text: str = ""
    status: str = "pending"

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
            if w < MIN_IMG_PX or h < MIN_IMG_PX: continue
            if _is_background_image(w, h, page_w, page_h): continue
            if (w * h) / (page_w * page_h) < MIN_PAGE_IMAGE_RATIO: continue
            img = Image.open(io.BytesIO(base_image["image"]))
            if img.mode == "CMYK": img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            bbox = None
            for item in page.get_image_info(hashes=False):
                if item.get("xref") == xref:
                    bbox = fitz.Rect(item["bbox"]); break
            results.append((buf.getvalue(), bbox))
        except Exception as e:
            print(f"  [WARN] xref={xref}: {e}")
    return results

def _find_caption(page, img_rect):
    candidates = []
    for block in page.get_text("blocks"):
        bx0, by0, bx1, by1, text = block[0], block[1], block[2], block[3], block[4]
        text = text.strip()
        if not text: continue
        if not any(kw in text.lower() for kw in CAPTION_KEYWORDS): continue
        if img_rect is None: return text
        if by0 < img_rect.y1: continue
        dist = by0 - img_rect.y1
        if dist > CAPTION_V_THRESHOLD: continue
        candidates.append((dist, text))
    if candidates:
        return sorted(candidates)[0][1].replace("\n", " ").strip()
    return "No caption available"

def _classify_figure(caption):
    lower = caption.lower()
    rules = [
        (["forest"], "Forest plot"), (["bar chart","bar graph","bar plot"], "Bar chart"),
        (["line graph","line plot","line chart"], "Line graph"), (["scatter"], "Scatter plot"),
        (["diagram","schematic","flowchart"], "Diagram"), (["table"], "Table figure"),
        (["histogram"], "Histogram"), (["heatmap","heat map"], "Heatmap"),
        (["kaplan","survival curve"], "Survival curve"),
    ]
    for keywords, fig_type in rules:
        if any(kw in lower for kw in keywords): return fig_type
    return "Unknown"

def extract_figures(pdf_path, output_dir="figures"):
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_name = pdf_path.stem
    doc = fitz.open(str(pdf_path))
    figures = []
    fig_counter = 1
    print(f"\n📄 Processing: {pdf_path.name} ({len(doc)} pages)")
    for page_num, page in enumerate(doc, start=1):
        page_rect = page.rect
        page_w, page_h = page_rect.width, page_rect.height
        print(f"  Page {page_num}...", end=" ")
        images = _extract_embedded_images(page, page_w, page_h)
        if not images:
            if _page_has_significant_text(page):
                print("(text page, skipped)"); continue
            print("(fallback render)", end=" ")
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)
            images = [(pix.tobytes("png"), page.rect)]
        print(f"{len(images)} figure(s) found")
        for png_bytes, bbox in images:
            fig_id = f"{pdf_name}_p{page_num:03d}_f{fig_counter:03d}"
            img_filename = f"{fig_id}.png"
            try: (output_dir / img_filename).write_bytes(png_bytes)
            except Exception as e: print(f"  [WARN] {e}")
            caption = _find_caption(page, bbox)
            figures.append(Figure(
                pdf_name=pdf_path.name, page_num=page_num, fig_id=fig_id,
                image_bytes=png_bytes, image_filename=img_filename,
                caption=caption, fig_type=_classify_figure(caption),
            ))
            fig_counter += 1
    doc.close()
    print(f"\n✅ Extracted {len(figures)} figure(s) from {pdf_path.name}")
    return figures
