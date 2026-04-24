import base64
import time
import os
import re
from typing import Optional
from google import genai
from google.genai import types

GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-flash-latest",
]

DELAY_S = 1.0

PROMPT_TEMPLATE = """You are writing alt text for scientific figures to support accessibility for visually impaired readers.

Figure type (classified): {fig_type}
Caption: {caption}

Write 1–2 sentences of alt text for this figure. Follow these rules strictly:
- Start with the figure type (e.g. "Line graph showing...", "Bar chart comparing...")
- Include the key insight, trend, or finding shown
- Use specific details from the caption (variables, units, groups, outcomes)
- Do NOT start with "This image shows..." or "This figure shows..."
- Do NOT copy the caption verbatim
- Do NOT add interpretation beyond what is visible

Alt text:"""


def _parse_retry_delay(err: str, default: float = 5.0) -> float:
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+)s", err)
    return float(min(int(m.group(1)), 60)) if m else default


def _clean_alt_text(raw: str) -> str:
    text = raw.strip().replace("**", "").replace("*", "").replace("`", "")
    fillers = ["this image shows","this figure shows","the image shows",
               "the figure shows","this is a","here we see","shown here is"]
    lower = text.lower()
    for filler in fillers:
        if lower.startswith(filler):
            text = text[len(filler):].lstrip(" ,")
            if text: text = text[0].upper() + text[1:]
            break
    sentences = []
    for part in text.split(". "):
        part = part.strip()
        if part: sentences.append(part)
        if len(sentences) == 2: break
    text = ". ".join(sentences)
    if text and not text.endswith("."): text += "."
    return text


def generate_alt_text(
    image_bytes: bytes,
    fig_type: str,
    caption: str,
    api_key: Optional[str] = None,
) -> tuple[str, str]:
    key = api_key or os.getenv("GEMINI_API_KEY", "")
    if not key:
        return "[SKIPPED — no GEMINI_API_KEY set]", "skipped"

    prompt = PROMPT_TEMPLATE.format(
        fig_type=fig_type,
        caption=caption if caption != "No caption available" else "No caption provided.",
    )

    client = genai.Client(api_key=key)
    last_error = None

    for model in GEMINI_MODELS:
        try:
            response = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_text(text=prompt),
                    types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                ],
            )
            if not response.text:
                raise ValueError("Empty response")
            print(f"    ✓ {model}")
            return _clean_alt_text(response.text.strip()), "done"

        except Exception as e:
            err = str(e)
            if any(x in err for x in ["503","429","404","UNAVAILABLE","NOT_FOUND","quota","RESOURCE_EXHAUSTED"]):
                delay = _parse_retry_delay(err)
                print(f"    [WARN] {model} unavailable ({err[:60]}) — trying next in {delay:.0f}s")
                last_error = e
                time.sleep(delay)
                continue
            return f"[ERROR: {err[:120]}]", "error"

    return f"[ERROR: all models exhausted. Last: {last_error}]", "error"
