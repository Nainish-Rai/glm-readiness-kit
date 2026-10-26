"""Z.ai error taxonomy: business code -> category, retryability, suggested HTTP status, retry-after hint.

Source: https://docs.z.ai/api-reference/api-code.md (research/01-conformance.md §2.5). One public function,
:func:`classify`, serves the conformance Errors tab and the bridge (slice 06)::

    from glmkit.zai_errors import classify
    info = classify("1302", http_status=429, message="Rate limit reached", headers={"retry-after": "2"})
    info.category        # 'rate_limit'
    info.retryable       # True   (same request may succeed later, no change needed)
    info.retry_strategy  # 'backoff' | 'wait_for_reset' | 'none'
    info.suggested_http  # status a bridge should emit so clients behave correctly
    info.retry_after_s   # 2.0 (from the Retry-After header or the message), else None

Codes missing from the table are reported with ``category="unknown"`` and ``known=False``. They are never
dropped, and the HTTP status that came with them (when given) drives the retry decision.

``upstream_http`` is the status Z.ai documents. ``suggested_http`` differs where the documented status would
make a client do the wrong thing (for example 1113 "no balance" arrives as 429, which clients retry).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

#: category -> (title, one-line meaning). Order is the display order of the dashboard.
CATEGORIES: dict[str, tuple[str, str]] = {
    "auth": ("Authentication", "Key missing, wrong, expired or needs a second factor. Fix the credential."),
    "billing": ("Billing and plan state", "No balance or resource package, plan or enterprise package expired."),
    "permission": ("Permission", "Key or plan has no access to this API or model."),
    "usage_limit": ("Usage window exhausted", "5h / 7d / monthly plan window used up. Retry only after the reset."),
    "rate_limit": ("Rate limit", "Too many requests right now. Back off and retry the same request."),
    "overloaded": ("Service overloaded", "Z.ai is temporarily saturated. Back off and retry."),
    "invalid_request": ("Invalid request", "A parameter is missing, invalid or in conflict. Fix the request."),
    "not_found": ("Model or API not found", "Unknown model code, or the API is offline / does not exist."),
    "context_length": ("Context length", "Prompt longer than the model accepts. Shorten the input."),
    "content_safety": ("Content safety", "Input or output blocked by the safety filter."),
    "server": ("Server error", "Z.ai failed internally. Retry with backoff; report if persistent."),
    "unknown": ("Unknown code", "A business code that is not in the documented table. Reported, never dropped."),
}

BACKOFF, WAIT_FOR_RESET, NO_RETRY = "backoff", "wait_for_reset", "none"


@dataclass(frozen=True)
class ErrorInfo:
    code: str | None
    known: bool
    category: str
    retryable: bool
    retry_strategy: str
    upstream_http: int | None
    suggested_http: int
    meaning: str
    retry_after_s: float | None = None

    @property
    def title(self) -> str:
        return CATEGORIES[self.category][0]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"title": self.title}


# code -> (category, retry strategy, documented HTTP status, suggested HTTP status, meaning)
_T = tuple[str, str, int, int, str]
TABLE: dict[str, _T] = {
    "1000": ("auth", NO_RETRY, 401, 401, "Authentication failed"),
    "1001": ("auth", NO_RETRY, 401, 401, "Authentication header missing"),
    "1003": ("auth", NO_RETRY, 401, 401, "Token expired"),
    "1005": ("auth", NO_RETRY, 401, 401, "Second factor needed"),
    "1113": ("billing", NO_RETRY, 429, 402, "Insufficient balance or no resource package"),
    "1200": ("server", BACKOFF, 500, 500, "API call error"),
    "1210": ("invalid_request", NO_RETRY, 400, 400, "Invalid API parameter (also: forced thinking disabled)"),
    "1211": ("not_found", NO_RETRY, 400, 404, "Unknown model"),
    "1212": ("invalid_request", NO_RETRY, 400, 400, "Model does not support this method"),
    "1213": ("invalid_request", NO_RETRY, 400, 400, "Required parameter not received"),
    "1214": ("invalid_request", NO_RETRY, 400, 400, "Parameter invalid"),
    "1215": ("invalid_request", NO_RETRY, 400, 400, "Two parameters cannot be set together"),
    "1220": ("permission", NO_RETRY, 403, 403, "No permission for this API"),
    "1221": ("not_found", NO_RETRY, 400, 404, "API offline"),
    "1222": ("not_found", NO_RETRY, 400, 404, "API does not exist"),
    "1230": ("server", BACKOFF, 500, 500, "API call process error"),
    "1234": ("server", BACKOFF, 500, 502, "Network error (carries an error id)"),
    "1261": ("context_length", NO_RETRY, 400, 400, "Prompt too long"),
    "1301": ("content_safety", NO_RETRY, 400, 400, "Unsafe or sensitive content"),
    "1302": ("rate_limit", BACKOFF, 429, 429, "Rate limit reached for requests"),
    "1305": ("overloaded", BACKOFF, 429, 503, "Service temporarily overloaded"),
    "1308": ("usage_limit", WAIT_FOR_RESET, 429, 429, "Usage limit reached for a time unit; resets at a time"),
    "1309": ("billing", NO_RETRY, 429, 402, "Coding Plan expired"),
    "1310": ("usage_limit", WAIT_FOR_RESET, 429, 429, "Weekly or monthly limit exhausted"),
    "1311": ("permission", NO_RETRY, 429, 403, "Plan has no access to this model"),
    "1313": ("usage_limit", WAIT_FOR_RESET, 429, 429, "Fair Usage Policy limiting"),
    "1314": ("billing", NO_RETRY, 429, 402, "Enterprise package expired"),
    "1315": ("permission", NO_RETRY, 429, 403, "Key limited to the enterprise coding scenario"),
    **{
        str(c): ("usage_limit", WAIT_FOR_RESET, 429, 429, "5h / 7d usage limit, extra-usage balance or spend limit")
        for c in range(1316, 1322)
    },
}

_NO_CODE_SERVER: _T = ("server", BACKOFF, 500, 500, "Internal error (no business code)")


def normalize_code(code: Any) -> str | None:
    """``1210``, ``"1210"`` and ``" 1210 "`` all become ``"1210"``; empty or missing becomes None."""
    if code is None or isinstance(code, bool):
        return None
    s = str(code).strip()
    return s or None


# ------------------------------------------------------------------ retry-after hint

_SECONDS = re.compile(
    r"(?:retry|try again|wait|resets?|available)\D{0,24}?(\d+(?:\.\d+)?)\s*"
    r"(ms|milliseconds?|s|secs?|seconds?|m|mins?|minutes?|h|hours?)\b",
    re.I,
)
_RESET_AT = re.compile(
    r"(?:reset|resets|refresh\w*|available)\D{0,16}?(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)", re.I
)
_UNIT = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}


def retry_after_hint(
    message: str | None = None, headers: Mapping[str, str] | None = None, *, now: datetime | None = None
) -> float | None:
    """Seconds to wait, from a ``Retry-After`` header (seconds or HTTP date) or from the message text
    ("retry after 30 seconds", "resets at 2026-10-26 12:00:00"). None when there is no hint."""
    now = now or datetime.now(UTC)
    if headers:
        h = {str(k).lower(): v for k, v in headers.items()}
        raw = h.get("retry-after") or h.get("x-ratelimit-reset-after")
        if raw:
            try:
                return max(0.0, float(raw))
            except ValueError:
                try:
                    when = parsedate_to_datetime(raw)
                    return max(0.0, (when - now).total_seconds())
                except (TypeError, ValueError):
                    pass
    if message:
        m = _RESET_AT.search(message)
        if m:
            try:
                when = datetime.fromisoformat(m.group(1).replace(" ", "T")).replace(tzinfo=UTC)
                return max(0.0, (when - now).total_seconds())
            except ValueError:
                pass
        m = _SECONDS.search(message)
        if m:
            unit = m.group(2).lower()
            key = "ms" if unit.startswith("milli") or unit == "ms" else unit[0]
            return float(m.group(1)) * _UNIT[key]
    return None


# ------------------------------------------------------------------ public API


def classify(
    code: Any = None,
    *,
    http_status: int | None = None,
    message: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> ErrorInfo:
    """Map a Z.ai business ``code`` (string or integer; None when the body has none) to an :class:`ErrorInfo`.

    ``http_status`` / ``message`` / ``headers`` refine the answer: they feed the retry-after hint and decide
    the fallback for codes outside the table (HTTP 429/5xx are retryable, other statuses are not).
    """
    c = normalize_code(code)
    hint = retry_after_hint(message, headers)
    if c in TABLE:
        cat, strategy, up, sug, meaning = TABLE[c]
        return ErrorInfo(c, True, cat, strategy != NO_RETRY, strategy, up, sug, meaning, hint)
    if c is None and http_status is not None and http_status >= 500:
        cat, strategy, up, sug, meaning = _NO_CODE_SERVER
        return ErrorInfo(None, True, cat, True, strategy, http_status, http_status, meaning, hint)
    # not in the documented table: keep it, say so, and fall back on the HTTP status
    if http_status == 429 or (http_status is not None and http_status >= 500):
        retryable, strategy = True, BACKOFF
    else:
        retryable, strategy = False, NO_RETRY
    meaning = "Business code not in the documented table" if c else "Error without a business code"
    return ErrorInfo(c, False, "unknown", retryable, strategy, http_status, http_status or 500, meaning, hint)


def table_rows() -> list[dict[str, Any]]:
    """The whole documented table as rows (for docs, tests and the dashboard), ordered by code."""
    return [classify(c).to_dict() for c in sorted(TABLE)]


def is_throttle(code: Any) -> bool:
    """True for the codes the conformance runner treats as throttled (rate limit, overload, usage windows)."""
    c = normalize_code(code)
    return c in TABLE and TABLE[c][0] in ("rate_limit", "overloaded", "usage_limit")
