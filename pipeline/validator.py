"""
validator.py — Phase 2 Validation & Flagging Engine
─────────────────────────────────────────────────────
Implements the expanded flag taxonomy, caption quality heuristics,
confidence tagging, and unified decision engine from the APS Phase 2 spec.
"""

from dataclasses import dataclass, field
from typing import Optional

# ── Flag taxonomy ────────────────────────────────────────────────────────────
# Structural flags
FLAG_OK                      = "OK"
FLAG_IMAGE_MISSING           = "IMAGE_MISSING"
FLAG_IMAGE_NOT_RESOLVED      = "IMAGE_NOT_RESOLVED"
FLAG_CAPTION_MISSING         = "CAPTION_MISSING"
FLAG_IMAGE_AND_CAPTION_MISSING = "IMAGE_AND_CAPTION_MISSING"

# Quality flags
FLAG_CAPTION_INSUFFICIENT    = "CAPTION_INSUFFICIENT"
FLAG_LOW_CONFIDENCE          = "LOW_CONFIDENCE"
FLAG_ALT_ALREADY_PRESENT     = "ALT_ALREADY_PRESENT"
FLAG_ALT_EMPTY               = "ALT_EMPTY"

# Advanced flags
FLAG_NON_INFORMATIVE         = "NON_INFORMATIVE_IMAGE"

# Confidence levels
CONF_HIGH   = "HIGH"
CONF_MEDIUM = "MEDIUM"
CONF_LOW    = "LOW"

# Processing status
STATUS_PROCESSED = "PROCESSED"
STATUS_ERROR     = "ERROR"

# Caption quality thresholds
MIN_CAPTION_WORDS = 5
DOMAIN_KEYWORDS = [
    "analysis", "trend", "comparison", "distribution", "correlation",
    "significant", "increase", "decrease", "effect", "result", "show",
    "indicate", "measure", "rate", "ratio", "percent", "data", "model",
    "error", "mean", "median", "value", "group", "patient", "sample",
    "figure", "plot", "graph", "chart", "diagram", "curve", "panel",
]


def check_caption_quality(caption: Optional[str]) -> str:
    """
    Deterministic caption quality check.
    Returns one of: CAPTION_MISSING, CAPTION_INSUFFICIENT, OK
    """
    if not caption or caption.strip() in ("", "No caption available"):
        return FLAG_CAPTION_MISSING

    words = caption.strip().split()

    if len(words) < MIN_CAPTION_WORDS:
        return FLAG_CAPTION_INSUFFICIENT

    lower = caption.lower()
    has_domain = any(kw in lower for kw in DOMAIN_KEYWORDS)
    if not has_domain:
        return FLAG_CAPTION_INSUFFICIENT

    return FLAG_OK


def assign_confidence(has_image: bool, caption_flag: str) -> Optional[str]:
    """
    Rule-based confidence proxy (no LLM scores).
    Returns HIGH, MEDIUM, LOW, or None if no image.
    """
    if not has_image:
        return None
    if caption_flag == FLAG_OK:
        return CONF_HIGH
    if caption_flag == FLAG_CAPTION_INSUFFICIENT:
        return CONF_MEDIUM
    return CONF_LOW


def classify_figure_flag(
    has_image: bool,
    image_resolved: bool,
    caption_flag: str,
    existing_alt: Optional[str] = None,
) -> str:
    """
    Unified decision engine — returns the final flag for a figure.
    Every figure gets exactly one flag.
    """
    if not has_image and caption_flag == FLAG_CAPTION_MISSING:
        return FLAG_IMAGE_AND_CAPTION_MISSING
    if not has_image:
        return FLAG_IMAGE_MISSING
    if has_image and not image_resolved:
        return FLAG_IMAGE_NOT_RESOLVED
    if caption_flag == FLAG_CAPTION_MISSING:
        return FLAG_CAPTION_MISSING
    if caption_flag == FLAG_CAPTION_INSUFFICIENT:
        return FLAG_CAPTION_INSUFFICIENT
    if existing_alt and existing_alt.strip():
        return FLAG_ALT_ALREADY_PRESENT
    return FLAG_OK


def is_eligible_for_generation(final_flag: str) -> bool:
    """Only generate alt text for figures that pass validation."""
    return final_flag in (FLAG_OK, FLAG_CAPTION_INSUFFICIENT)


def validate_figure(
    caption: Optional[str],
    image_bytes: Optional[bytes],
    image_resolved: bool = True,
    existing_alt: Optional[str] = None,
) -> dict:
    """
    Run full validation on a figure and return a dict of all flags/scores.
    Used by both PDF and XML extractors.
    """
    has_image = image_bytes is not None and len(image_bytes) > 0
    caption_flag = check_caption_quality(caption)
    final_flag = classify_figure_flag(has_image, image_resolved, caption_flag, existing_alt)
    confidence = assign_confidence(has_image, caption_flag)
    eligible = is_eligible_for_generation(final_flag)

    caption_word_count = len(caption.strip().split()) if caption and caption.strip() not in ("", "No caption available") else 0

    return {
        "caption_flag": caption_flag,
        "caption_word_count": caption_word_count,
        "image_status": "resolved" if (has_image and image_resolved) else ("missing" if not has_image else "not_resolved"),
        "confidence": confidence,
        "final_flag": final_flag,
        "eligible": eligible,
    }