"""
qc.py — Phase 2 Alt Text Quality Control
─────────────────────────────────────────
Runs deterministic checks on generated alt text after LLM generation.
Attaches qc_flag and qc_notes to each Figure for Excel/JSON output.

QC Flags (controlled vocabulary):
    QC_PASS             — passes all checks
    QC_TOO_SHORT        — alt text under minimum word threshold
    QC_TOO_LONG         — alt text over maximum word threshold
    QC_FILLER_PHRASE    — starts with a generic non-informative opener
    QC_MISSING_INSIGHT  — no overlap with caption keywords (low specificity)
    QC_MULTIPLE         — more than one issue found (comma-separated in notes)
    QC_SKIPPED          — not applicable (figure was skipped/errored)
"""

from pipeline.qc import run_qc_on_figures, print_qc_metrics
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_ALT_WORDS = 10    # fewer than this = too short
MAX_ALT_WORDS = 60    # more than this  = too long

# ── Filler phrases (case-insensitive prefix match) ────────────────────────────
FILLER_PHRASES = [
    "this image shows",
    "this figure shows",
    "the image shows",
    "the figure shows",
    "this is a",
    "here we see",
    "shown here is",
    "the image depicts",
    "this image depicts",
    "image of",
    "a figure showing",
    "a graph showing",
    "a chart showing",
]

# ── Insight keywords — at least one must appear in alt text ──────────────────
# Pulled from the same domain vocabulary as validator.py
INSIGHT_KEYWORDS = [
    "increase", "decrease", "higher", "lower", "greater", "less",
    "significant", "compared", "association", "correlation", "trend",
    "distribution", "difference", "effect", "outcome", "result",
    "survival", "risk", "hazard", "odds", "rate", "ratio", "percent",
    "treatment", "group", "patient", "cohort", "sample",
    "mean", "median", "confidence", "interval", "error",
    "show", "indicate", "demonstrate", "suggest", "reveal",
]

# ── QC Flag constants ─────────────────────────────────────────────────────────
QC_PASS            = "QC_PASS"
QC_TOO_SHORT       = "QC_TOO_SHORT"
QC_TOO_LONG        = "QC_TOO_LONG"
QC_FILLER_PHRASE   = "QC_FILLER_PHRASE"
QC_MISSING_INSIGHT = "QC_MISSING_INSIGHT"
QC_MULTIPLE        = "QC_MULTIPLE"
QC_SKIPPED         = "QC_SKIPPED"

# Statuses that should not be QC'd
SKIP_STATUSES = {"skipped", "skipped_existing_alt", "dry_run", "error", ""}


@dataclass
class QCResult:
    qc_flag:  str
    qc_notes: str   # human-readable detail for SME review


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_length(alt_text: str) -> Optional[str]:
    words = alt_text.strip().split()
    if len(words) < MIN_ALT_WORDS:
        return f"{QC_TOO_SHORT} ({len(words)} words, min {MIN_ALT_WORDS})"
    if len(words) > MAX_ALT_WORDS:
        return f"{QC_TOO_LONG} ({len(words)} words, max {MAX_ALT_WORDS})"
    return None


def _check_filler(alt_text: str) -> Optional[str]:
    lower = alt_text.strip().lower()
    for phrase in FILLER_PHRASES:
        if lower.startswith(phrase):
            return f"{QC_FILLER_PHRASE} (starts with: '{phrase}')"
    return None


def _check_insight(alt_text: str, caption: str) -> Optional[str]:
    """
    Two-stage insight check:
    1. Does alt text contain any global insight keyword?
    2. Does alt text share at least one meaningful word with the caption?
       (catches domain-specific terms not in the keyword list)
    """
    alt_lower = alt_text.lower()

    # Stage 1: global keyword match
    if any(kw in alt_lower for kw in INSIGHT_KEYWORDS):
        return None

    # Stage 2: caption overlap (words > 4 chars to skip stop words)
    if caption and caption.strip().lower() not in ("no caption available", "no caption provided."):
        caption_words = {
            w.strip(".,;:()[]") for w in caption.lower().split()
            if len(w) > 4
        }
        alt_words = {
            w.strip(".,;:()[]") for w in alt_lower.split()
        }
        if caption_words & alt_words:
            return None

    return f"{QC_MISSING_INSIGHT} (no insight keywords or caption overlap found)"


# ── Main QC function ──────────────────────────────────────────────────────────

def run_qc(alt_text: str, caption: str, status: str) -> QCResult:
    """
    Run all QC checks on a single generated alt text.
    Returns a QCResult with flag + human-readable notes.
    """
    # Skip figures that weren't generated
    if not alt_text or status in SKIP_STATUSES:
        return QCResult(qc_flag=QC_SKIPPED, qc_notes="not generated")

    issues = []

    length_issue  = _check_length(alt_text)
    filler_issue  = _check_filler(alt_text)
    insight_issue = _check_insight(alt_text, caption)

    if length_issue:
        issues.append(length_issue)
    if filler_issue:
        issues.append(filler_issue)
    if insight_issue:
        issues.append(insight_issue)

    if not issues:
        return QCResult(qc_flag=QC_PASS, qc_notes="")

    if len(issues) == 1:
        # Single issue — use its specific flag as the top-level flag
        raw_flag = issues[0].split(" ")[0]   # e.g. "QC_TOO_SHORT"
        return QCResult(qc_flag=raw_flag, qc_notes=issues[0])

    # Multiple issues
    return QCResult(
        qc_flag  = QC_MULTIPLE,
        qc_notes = " | ".join(issues),
    )


# ── Batch runner ──────────────────────────────────────────────────────────────

def run_qc_on_figures(figures: list) -> list:
    """
    Run QC on all figures in place.
    Attaches qc_flag and qc_notes attributes to each Figure.
    Returns list of figures that failed QC (for easy reporting).
    """
    failed = []
    for fig in figures:
        result = run_qc(
            alt_text = getattr(fig, "alt_text", ""),
            caption  = getattr(fig, "caption", ""),
            status   = getattr(fig, "status", ""),
        )
        fig.qc_flag  = result.qc_flag
        fig.qc_notes = result.qc_notes

        if result.qc_flag not in (QC_PASS, QC_SKIPPED):
            failed.append(fig)

    return failed


# ── QC metrics printer ────────────────────────────────────────────────────────

def print_qc_metrics(figures: list):
    from collections import Counter

    qc_counts  = Counter(getattr(f, "qc_flag", QC_SKIPPED) for f in figures)
    total      = len(figures)
    generated  = sum(1 for f in figures if getattr(f, "status", "") == "done")
    passed     = qc_counts.get(QC_PASS, 0)
    skipped_qc = qc_counts.get(QC_SKIPPED, 0)
    failed     = total - passed - skipped_qc

    pass_rate  = f"{(passed / generated * 100):.1f}%" if generated else "N/A"

    print(f"""
---------- QC SUMMARY ----------
Generated (QC eligible)   : {generated}
QC_PASS                   : {passed}  ({pass_rate} pass rate)
QC_SKIPPED                : {skipped_qc}
QC_TOO_SHORT              : {qc_counts.get(QC_TOO_SHORT, 0)}
QC_TOO_LONG               : {qc_counts.get(QC_TOO_LONG, 0)}
QC_FILLER_PHRASE          : {qc_counts.get(QC_FILLER_PHRASE, 0)}
QC_MISSING_INSIGHT        : {qc_counts.get(QC_MISSING_INSIGHT, 0)}
QC_MULTIPLE               : {qc_counts.get(QC_MULTIPLE, 0)}
QC Failed (total)         : {failed}
--------------------------------""")