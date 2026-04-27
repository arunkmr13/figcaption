"""
xml_writer.py — XML Alt Text Injection
────────────────────────────────────────
Takes the original XML + processed figures and produces:
  1. alt_text.xml   — original XML with <alt-text> injected into every <fig>
  2. embedded.xml   — same as above but with base64 PNG images embedded inline
"""

import base64
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List


def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _find_all_ns(element, tag: str):
    return [el for el in element.iter() if _strip_ns(el.tag) == tag]


def _get_namespace(root) -> str:
    """Extract the default namespace from root tag if present."""
    tag = root.tag
    if tag.startswith("{"):
        return tag[1:tag.index("}")]
    return ""


def _register_namespaces(tree_str: str):
    """
    Pre-register all namespaces found in the XML so ElementTree
    doesn't rewrite them as ns0, ns1, etc.
    """
    import re
    ns_map = {}
    for match in re.finditer(r'xmlns(?::(\w+))?=["\']([^"\']+)["\']', tree_str):
        prefix = match.group(1) or ""
        uri = match.group(2)
        ns_map[prefix] = uri
        ET.register_namespace(prefix, uri)
    return ns_map


def inject_alt_text(
    xml_path: str,
    figures: list,
    output_dir: str,
    embed_images: bool = False,
) -> tuple[str, str]:
    """
    Inject generated alt text back into the original XML.

    Args:
        xml_path:     path to original .xml file
        figures:      processed Figure objects (source_type == "xml")
        output_dir:   where to write output XML files
        embed_images: if True, also write an embedded version with base64 images

    Returns:
        (alt_text_xml_path, embedded_xml_path) — embedded path is "" if not requested
    """
    xml_path = Path(xml_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Read raw text first so we can preserve namespaces
    raw_xml = xml_path.read_text(encoding="utf-8")
    _register_namespaces(raw_xml)

    tree = ET.parse(str(xml_path))
    root = tree.getroot()

    # Build lookup: xml_fig_element_id → figure
    fig_lookup = {}
    for fig in figures:
        if fig.source_type == "xml":
            if fig.xml_fig_element_id:
                fig_lookup[fig.xml_fig_element_id] = fig
            # Also index by position (fig_counter order)
            fig_lookup[fig.fig_id] = fig

    xml_figs = _find_all_ns(root, "fig")

    injected = 0
    for idx, fig_el in enumerate(xml_figs):
        fig_id_attr = fig_el.get("id", "")
        # Match by XML id attr first, then by position
        fig_obj = fig_lookup.get(fig_id_attr)
        if not fig_obj:
            # Try position-based match
            for f in figures:
                if f.source_type == "xml" and f.page_num == idx + 1:
                    fig_obj = f
                    break

        if not fig_obj:
            continue

        # Inject <alt-text> if we have generated alt text
        alt_text = fig_obj.alt_text
        if not alt_text or alt_text.startswith("[SKIPPED") or alt_text.startswith("[ERROR"):
            continue

        # Check if <alt-text> already exists
        existing_alt_el = None
        for child in fig_el:
            if _strip_ns(child.tag) == "alt-text":
                existing_alt_el = child
                break

        ns = _get_namespace(root)
        alt_tag = f"{{{ns}}}alt-text" if ns else "alt-text"

        if existing_alt_el is not None:
            existing_alt_el.text = alt_text
        else:
            alt_el = ET.SubElement(fig_el, alt_tag)
            alt_el.text = alt_text

        injected += 1

    print(f"  💉 Injected alt text into {injected}/{len(xml_figs)} <fig> elements")

    # Write alt_text.xml (no embedded images)
    stem = xml_path.stem
    alt_xml_path = output_dir / f"{stem}_alt_text.xml"
    _write_xml(tree, alt_xml_path)
    print(f"  📄 Alt text XML saved: {alt_xml_path}")

    # Write embedded.xml (with base64 images)
    embedded_xml_path = ""
    if embed_images:
        embedded_xml_path = str(output_dir / f"{stem}_embedded.xml")
        _embed_images_in_tree(tree, figures, root)
        _write_xml(tree, Path(embedded_xml_path))
        print(f"  📦 Embedded XML saved: {embedded_xml_path}")

    return str(alt_xml_path), embedded_xml_path


def _embed_images_in_tree(tree, figures: list, root):
    """
    For each figure that has image bytes, find its <graphic> element
    and inject a base64 data URI into xlink:href.
    """
    xml_figs = _find_all_ns(root, "fig")
    ns = _get_namespace(root)
    xlink_ns = "http://www.w3.org/1999/xlink"

    fig_by_pos = {f.page_num: f for f in figures if f.source_type == "xml"}
    fig_by_id  = {f.xml_fig_element_id: f for f in figures if f.source_type == "xml" and f.xml_fig_element_id}

    for idx, fig_el in enumerate(xml_figs):
        fig_id_attr = fig_el.get("id", "")
        fig_obj = fig_by_id.get(fig_id_attr) or fig_by_pos.get(idx + 1)

        if not fig_obj or not fig_obj.image_bytes or len(fig_obj.image_bytes) < 100:
            continue

        # Find <graphic> child
        for child in fig_el:
            if _strip_ns(child.tag) in ("graphic", "inline-graphic"):
                b64 = base64.b64encode(fig_obj.image_bytes).decode("ascii")
                data_uri = f"data:image/png;base64,{b64}"
                # Try to set xlink:href
                href_key = f"{{{xlink_ns}}}href"
                child.set(href_key, data_uri)
                break


def _write_xml(tree, path: Path):
    """Write ElementTree to file with XML declaration."""
    ET.indent(tree, space="  ")
    tree.write(
        str(path),
        encoding="utf-8",
        xml_declaration=True,
    )


def write_xml_outputs(
    xml_path: str,
    figures: list,
    output_dir: str,
) -> dict:
    """
    Convenience wrapper — writes both alt_text.xml and embedded.xml.
    Returns dict with paths.
    """
    alt_xml, embedded_xml = inject_alt_text(
        xml_path=xml_path,
        figures=figures,
        output_dir=output_dir,
        embed_images=True,
    )
    return {
        "alt_text_xml": alt_xml,
        "embedded_xml": embedded_xml,
    }