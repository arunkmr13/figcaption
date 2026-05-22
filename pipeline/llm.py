import time
import os
import re
from typing import Optional
from google import genai
from google.genai import types

from .validator import (
    is_eligible_for_generation,
    FLAG_ALT_ALREADY_PRESENT,
    FLAG_EXTERNAL_IMAGE,
    CONF_HIGH, CONF_MEDIUM, CONF_LOW,
)

# ── Model Fallback Chain (best → fastest → cheapest) ────────────────────────
GEMINI_MODELS = [
    "gemini-2.5-flash",        # best quality, try first
    "gemini-2.0-flash",        # fast, reliable fallback
    "gemini-2.0-flash-lite",   # cheaper fallback
    "gemini-2.5-flash-lite",   # lite variant fallback
]

DELAY_S     = 1.0   # inter-call delay between successful requests
MAX_RETRIES = 1     # retries per model on transient errors

TRANSIENT_ERROR_CODES = ["503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "quota"]
FATAL_ERROR_CODES     = ["404", "NOT_FOUND", "INVALID_ARGUMENT", "API_KEY_INVALID"]

# ── Prompts ──────────────────────────────────────────────────────────────────
PROMPT_TEMPLATE = """You are writing alt text for scientific figures to support accessibility for visually impaired readers.

Figure type (classified): {fig_type}
Caption: {caption}

Write 1-2 sentences of alt text for this figure. Follow these rules strictly:
- Start with the figure type (e.g. "Line graph showing...", "Bar chart comparing...")
- Include the key insight, trend, or finding shown
- Use specific details from the caption (variables, units, groups, outcomes)
- Do NOT start with "This image shows..." or "This figure shows..."
- Do NOT copy the caption verbatim
- Do NOT add interpretation beyond what is visible

Alt text:"""

PROMPT_CAPTION_ONLY = """You are writing alt text for scientific figures to support accessibility for visually impaired readers.

Note: Only the caption is available for this figure — no image was provided. Generate alt text based on the figure type and caption alone.

Figure type (classified): {fig_type}
Caption: {caption}

Write 1-2 sentences of alt text describing what this figure likely shows. Follow these rules:
- Start with the figure type (e.g. "Line graph showing...", "Bar chart comparing...")
- Include the key finding or comparison described in the caption
- Use specific details from the caption (variables, groups, outcomes, statistics)
- Do NOT start with "This image shows..." or "This figure shows..."
- Do NOT copy the caption verbatim

Alt text:"""


# ── Helpers ──────────────────────────────────────────────────────────────────
def _parse_retry_delay(err: str, default: float = 5.0) -> float:
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+)s", err)
    return float(min(int(m.group(1)), 60)) if m else default


def _clean_alt_text(raw: str) -> str:
    text = raw.strip().replace("**", "").replace("*", "").replace("`", "")
    fillers = [
        "this image shows", "this figure shows", "the image shows",
        "the figure shows", "this is a", "here we see", "shown here is",
    ]
    lower = text.lower()
    for filler in fillers:
        if lower.startswith(filler):
            text = text[len(filler):].lstrip(" ,")
            if text:
                text = text[0].upper() + text[1:]
            break
    sentences = []
    for part in text.split(". "):
        part = part.strip()
        if part:
            sentences.append(part)
        if len(sentences) == 2:
            break
    text = ". ".join(sentences)
    if text and not text.endswith("."):
        text += "."
    return text


# ── Main Generation Function ─────────────────────────────────────────────────
def generate_alt_text(
    image_bytes:  Optional[bytes],
    fig_type:     str,
    caption:      str,
    final_flag:   str = "OK",
    existing_alt: str = "",
    confidence:   Optional[str] = None,   # FIX 3: now accepted + logged
    api_key:      Optional[str] = None,
) -> tuple[str, str]:

    # ── Gate 1: already has alt text ─────────────────────────────────────────
    if final_flag == FLAG_ALT_ALREADY_PRESENT and existing_alt:
        return existing_alt, "skipped_existing_alt"

    # ── Gate 2: not eligible ──────────────────────────────────────────────────
    if not is_eligible_for_generation(final_flag):
        return "", "skipped"           # FIX 5: clean empty string, not noisy sentinel

    # ── Gate 3: no API key ────────────────────────────────────────────────────
    key = api_key or os.getenv("GEMINI_API_KEY", "")
    if not key:
        print("    [ERROR] No GEMINI_API_KEY set")
        return "", "error"             # FIX 5: clean return, error logged not stored

    caption_text = caption if caption not in ("", "No caption available") else "No caption provided."
    has_image    = isinstance(image_bytes, (bytes, bytearray)) and len(image_bytes) > 100

    # ── FIX 3: log confidence level ───────────────────────────────────────────
    conf_label = confidence or "N/A"

    if has_image:
        prompt   = PROMPT_TEMPLATE.format(fig_type=fig_type, caption=caption_text)
        contents = [
            types.Part.from_text(text=prompt),
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
        ]
        print(f"    [mode: image+caption | confidence: {conf_label}]")
    else:
        # FIX 6: generic "no image" message, not just "external image"
        prompt   = PROMPT_CAPTION_ONLY.format(fig_type=fig_type, caption=caption_text)
        contents = [types.Part.from_text(text=prompt)]
        reason   = "external" if final_flag == FLAG_EXTERNAL_IMAGE else "missing"
        print(f"    [mode: caption-only ({reason}) | confidence: {conf_label}]")

    client = genai.Client(api_key=key)
    last_error = None

    for model in GEMINI_MODELS:
        # FIX 2: retry each model up to MAX_RETRIES times on transient errors
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model=model, contents=contents
                )
                if not response.text:
                    raise ValueError("Empty response from model")

                # FIX 4: apply inter-call delay after every successful generation
                time.sleep(DELAY_S)

                print(f"    ✓ {model} (attempt {attempt})")
                return _clean_alt_text(response.text.strip()), "done"

            except Exception as e:
                err = str(e)

                # Fatal error — don't retry this model or any other
                if any(code in err for code in FATAL_ERROR_CODES):
                    print(f"    [FATAL] {model}: {err[:120]}")
                    return "", "error"   # FIX 5: clean return

                # Transient error — retry same model or move to next
                if any(code in err for code in TRANSIENT_ERROR_CODES):
                    delay = _parse_retry_delay(err)
                    print(f"    [WARN] {model} attempt {attempt}/{MAX_RETRIES} "
                          f"— retrying in {delay:.0f}s")
                    last_error = err
                    time.sleep(delay)
                    continue

                # Unknown error — log and move to next model
                print(f"    [ERROR] {model}: {err[:120]}")
                last_error = err
                break  # try next model

    # All models exhausted
    print(f"    [ERROR] All models exhausted. Last error: {last_error}")
    return "", "error"   # FIX 5: clean return