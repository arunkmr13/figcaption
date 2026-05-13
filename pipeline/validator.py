from typing import Optional

# ── Structural Flags ────────────────────────────────────────────────────────
FLAG_OK                        = "OK"
FLAG_IMAGE_MISSING             = "IMAGE_MISSING"
FLAG_IMAGE_NOT_RESOLVED        = "IMAGE_NOT_RESOLVED"
FLAG_EXTERNAL_IMAGE            = "EXTERNAL_IMAGE"
FLAG_CAPTION_MISSING           = "CAPTION_MISSING"
FLAG_IMAGE_AND_CAPTION_MISSING = "IMAGE_AND_CAPTION_MISSING"

# ── Quality Flags ────────────────────────────────────────────────────────────
FLAG_CAPTION_INSUFFICIENT      = "CAPTION_INSUFFICIENT"
FLAG_LOW_CONFIDENCE            = "LOW_CONFIDENCE"
FLAG_ALT_ALREADY_PRESENT       = "ALT_ALREADY_PRESENT"
FLAG_ALT_EMPTY                 = "ALT_EMPTY"
FLAG_NON_INFORMATIVE           = "NON_INFORMATIVE_IMAGE"

# ── Confidence Levels ────────────────────────────────────────────────────────
CONF_HIGH   = "HIGH"
CONF_MEDIUM = "MEDIUM"
CONF_LOW    = "LOW"

# ── Processing Status ────────────────────────────────────────────────────────
STATUS_PROCESSED = "PROCESSED"
STATUS_ERROR     = "ERROR"

# ── Caption Quality Config ───────────────────────────────────────────────────
MIN_CAPTION_WORDS = 5
DOMAIN_KEYWORDS = [
    "analysis", "trend", "comparison", "distribution", "correlation",
    "significant", "increase", "decrease", "effect", "result", "show",
    "indicate", "measure", "rate", "ratio", "percent", "data", "model",
    "error", "mean", "median", "value", "group", "patient", "sample",
    "figure", "plot", "graph", "chart", "diagram", "curve", "panel",
    "weighted", "hazard", "odds", "kaplan", "confidence", "interval",
    "cohort", "treatment", "outcome", "risk", "survival",
]

EMPTY_CAPTION_SENTINELS = {"", "no caption available", "none", "n/a"}


# ── 1. Caption Quality ───────────────────────────────────────────────────────
def check_caption_quality(caption: Optional[str]) -> str:
    if not caption or caption.strip().lower() in EMPTY_CAPTION_SENTINELS:
        return FLAG_CAPTION_MISSING

    words = caption.strip().split()

    if len(words) < MIN_CAPTION_WORDS:
        return FLAG_CAPTION_INSUFFICIENT

    lower = caption.lower()
    if not any(kw in lower for kw in DOMAIN_KEYWORDS):
        return FLAG_CAPTION_INSUFFICIENT

    return FLAG_OK


# ── 2. Confidence Scoring ────────────────────────────────────────────────────
def assign_confidence(
    has_image: bool,
    external_image: bool,
    caption_flag: str,
) -> Optional[str]:
    # No image of any kind — confidence not applicable
    if not has_image and not external_image:
        return None

    if external_image:
        return CONF_MEDIUM if caption_flag == FLAG_OK else CONF_LOW

    if caption_flag == FLAG_OK:
        return CONF_HIGH
    if caption_flag == FLAG_CAPTION_INSUFFICIENT:
        return CONF_MEDIUM

    return CONF_LOW


# ── 3. Unified Decision Engine ───────────────────────────────────────────────
def classify_figure_flag(
    has_image: bool,
    image_resolved: bool,
    caption_flag: str,
    existing_alt: Optional[str] = None,
    external_image: bool = False,
    non_informative: bool = False,
) -> str:

    # ── Structural checks first ──────────────────────────────────────────────
    if not has_image and not external_image:
        if caption_flag == FLAG_CAPTION_MISSING:
            return FLAG_IMAGE_AND_CAPTION_MISSING
        return FLAG_IMAGE_MISSING

    if has_image and not image_resolved:
        return FLAG_IMAGE_NOT_RESOLVED

    # ── Caption checks ───────────────────────────────────────────────────────
    if caption_flag == FLAG_CAPTION_MISSING:
        return FLAG_CAPTION_MISSING

    if caption_flag == FLAG_CAPTION_INSUFFICIENT:
        return FLAG_CAPTION_INSUFFICIENT

    # ── Content checks ───────────────────────────────────────────────────────
    if non_informative:
        return FLAG_NON_INFORMATIVE

    # ── Existing alt text checks (after structure is confirmed OK) ───────────
    if existing_alt is not None:
        if existing_alt.strip():
            return FLAG_ALT_ALREADY_PRESENT
        else:
            return FLAG_ALT_EMPTY  # tag exists but is blank

    # ── External image with good caption ────────────────────────────────────
    if external_image:
        return FLAG_EXTERNAL_IMAGE

    return FLAG_OK


# ── 4. Eligibility Gate ──────────────────────────────────────────────────────
def is_eligible_for_generation(final_flag: str) -> bool:
    return final_flag in (FLAG_OK, FLAG_CAPTION_INSUFFICIENT, FLAG_EXTERNAL_IMAGE)


# ── 5. Main Entry Point ──────────────────────────────────────────────────────
def validate_figure(
    caption: Optional[str],
    image_bytes: Optional[bytes],
    image_resolved: bool = True,
    existing_alt: Optional[str] = None,
    external_image: bool = False,
    external_href: str = "",
    non_informative: bool = False,
) -> dict:
    try:
        has_image    = isinstance(image_bytes, (bytes, bytearray)) and len(image_bytes) > 100
        caption_flag = check_caption_quality(caption)
        final_flag   = classify_figure_flag(
            has_image, image_resolved, caption_flag,
            existing_alt, external_image, non_informative
        )
        confidence   = assign_confidence(has_image, external_image, caption_flag)
        eligible     = is_eligible_for_generation(final_flag)

        caption_word_count = (
            len(caption.strip().split())
            if caption and caption.strip().lower() not in EMPTY_CAPTION_SENTINELS
            else 0
        )

        if external_image:
            image_status = f"external:{external_href}" if external_href else "external"
        elif not has_image:
            image_status = "missing"
        elif not image_resolved:
            image_status = "not_resolved"
        else:
            image_status = "resolved"

        return {
            "caption_flag":        caption_flag,
            "caption_word_count":  caption_word_count,
            "image_status":        image_status,
            "confidence":          confidence,
            "final_flag":          final_flag,
            "eligible":            eligible,
            "has_image":           has_image,
            "external_image":      external_image,
            "processing_status":   STATUS_PROCESSED,  # ← was missing
        }

    except Exception as e:
        return {
            "caption_flag":        None,
            "caption_word_count":  0,
            "image_status":        "error",
            "confidence":          None,
            "final_flag":          None,
            "eligible":            False,
            "has_image":           False,
            "external_image":      False,
            "processing_status":   STATUS_ERROR,
            "error_detail":        str(e),  # surfaced for logging
        }