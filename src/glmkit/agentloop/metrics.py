"""Pure text metrics: reasoning degeneracy (GLM-5#168, vllm#56868) and CJK leakage (GLM-5#142, #54)."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

# ------------------------------------------------------------------ degeneracy

DEGENERATE_WORDS = {"=", "the"}
MIN_WORDS = 20  # below this, word shares are noise
CHAR_REPEAT = re.compile(r"(.{2,12}?)\1{7,}", re.S)
FFFD = "�"


def _loop_cover(words: list[str], max_period: int = 3, min_reps: int = 4) -> int:
    """Number of word positions inside a loop where a 1..3 word pattern repeats at least ``min_reps`` times."""
    covered = [False] * len(words)
    for period in range(1, max_period + 1):
        i = 0
        while i + period * min_reps <= len(words):
            pat = words[i : i + period]
            reps = 1
            while words[i + reps * period : i + (reps + 1) * period] == pat:
                reps += 1
            if reps >= min_reps:
                for j in range(i, i + reps * period):
                    covered[j] = True
                i += reps * period
            else:
                i += 1
    return sum(covered)


def degeneracy_metrics(text: str | None) -> dict[str, Any]:
    """Metrics for one reasoning or content string.

    ``eq_the_share``: share of words that are ``=`` or ``the`` (healthy reasoning 5-12%, collapse 70-99%).
    ``repeated_token_share``: share of words inside a loop of a 1-3 word pattern repeated 4+ times.
    ``char_repeat``: the same 2-12 character unit repeated 8+ times. ``fffd``: U+FFFD count.
    ``score`` is the larger of the two shares (1.0 when ``char_repeat``), 0.0 for texts under 20 words."""
    text = text or ""
    words = text.lower().split()
    n = len(words)
    eq = sum(1 for w in words if w in DEGENERATE_WORDS) / n if n >= MIN_WORDS else 0.0
    rep = _loop_cover(words) / n if n >= MIN_WORDS else 0.0
    chars = bool(CHAR_REPEAT.search(text)) if len(text) >= 40 else False
    return {
        "words": n,
        "eq_the_share": round(eq, 4),
        "repeated_token_share": round(rep, 4),
        "char_repeat": chars,
        "fffd": text.count(FFFD),
        "score": 1.0 if chars else round(max(eq, rep), 4),
    }


def is_degenerate(m: dict[str, Any], threshold: float = 0.2) -> bool:
    return m["score"] > threshold or m["fffd"] > 0


# ------------------------------------------------------------------ language

CJK_RUN = re.compile(r"[㐀-䶿一-鿿豈-﫿぀-ヿㇰ-ㇿ가-힯]+")
KANA = re.compile(r"[぀-ヿㇰ-ㇿ]")
HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
HANGUL = re.compile(r"[가-힯]")
LATIN = re.compile(r"[A-Za-z]")


def cjk_runs(text: str | None) -> list[str]:
    return CJK_RUN.findall(text or "")


def cjk_leaks(output: str | None, inputs: Iterable[str]) -> list[str]:
    """CJK runs in ``output`` that do not occur in any of the conversation inputs (system, user, tool results).
    A run that the model merely quotes from its input is not a leak."""
    corpus = "\n".join(inputs)
    return [r for r in cjk_runs(output) if r not in corpus]


def language_of(text: str | None) -> str:
    """Coarse script-level language: ``ja`` (kana present), ``ko``, ``zh`` (Han without kana), ``en`` (Latin), or
    ``unknown``. A text counts as CJK when its CJK characters are at least half as many as its Latin letters."""
    t = text or ""
    kana, han, hangul, latin = (len(p.findall(t)) for p in (KANA, HAN, HANGUL, LATIN))
    cjk = kana + han + hangul
    if cjk and cjk * 2 >= latin:
        if kana:
            return "ja"
        return "ko" if hangul > han else "zh"
    return "en" if latin else "unknown"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k hits in n trials."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))
