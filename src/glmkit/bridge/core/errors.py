"""Error classifier: Z.ai business codes -> HTTP status, retryability and ``Retry-After``.

The table is the proposal in research/02-bridge.md section 5 (semantics are an inference from Z.ai's code table
and are verified in issue 16). Issue 03 builds a Z.ai taxonomy module in parallel; it can be plugged in by
implementing the ``ErrorClassifier`` protocol and passing it to ``BridgeCore(classifier=...)``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable


@dataclass
class ClassifiedError:
    status: int  # status to return to the client
    upstream_status: int
    code: str | None  # Z.ai business code as a string, when present
    kind: str  # rate_limit | overloaded | quota | billing | invalid_request | too_long | server | leak | auth | other
    retryable: bool
    retry_after_s: int | None = None
    error_type: str = "api_error"  # OpenAI/Anthropic style error.type
    message: str = ""
    sanitized: bool = False  # the upstream body was not a JSON error (e.g. a traceback) and was replaced
    changed: bool = False  # status differs from upstream's


@runtime_checkable
class ErrorClassifier(Protocol):
    def classify(self, status: int, body: Any, headers: dict[str, str] | None = None) -> ClassifiedError: ...


# code -> (client status, kind, retryable, default retry-after seconds, error.type)
CODE_TABLE: dict[str, tuple[int, str, bool, int | None, str]] = {
    "1302": (429, "rate_limit", True, 2, "rate_limit_error"),
    "1305": (503, "overloaded", True, 10, "overloaded_error"),
    "1308": (429, "quota", True, 300, "rate_limit_error"),
    "1316": (429, "quota", True, 300, "rate_limit_error"),
    "1317": (429, "quota", True, 300, "rate_limit_error"),
    "1318": (429, "quota", True, 300, "rate_limit_error"),
    "1310": (429, "quota", False, None, "rate_limit_error"),
    "1313": (429, "rate_limit", False, None, "rate_limit_error"),
    "1113": (402, "billing", False, None, "billing_error"),
    "1309": (402, "billing", False, None, "billing_error"),
    "1311": (402, "billing", False, None, "billing_error"),
    "1314": (402, "billing", False, None, "billing_error"),
    "1315": (402, "billing", False, None, "billing_error"),
    "1210": (400, "invalid_request", False, None, "invalid_request_error"),
    "1211": (404, "invalid_request", False, None, "not_found_error"),
    "1213": (400, "invalid_request", False, None, "invalid_request_error"),
    "1214": (400, "invalid_request", False, None, "invalid_request_error"),
    "1261": (400, "too_long", False, None, "invalid_request_error"),
    "1000": (401, "auth", False, None, "authentication_error"),
    "1001": (401, "auth", False, None, "authentication_error"),
}
_FLUSH = re.compile(r"next[_ ]flush[_ ]time\D{0,6}(\d{10,13}|\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)")
MAX_RETRY_AFTER = 24 * 3600


def error_code(body: Any) -> str | None:
    if isinstance(body, dict):
        err = body.get("error")
        code = err.get("code") if isinstance(err, dict) else body.get("code")
        return None if code is None else str(code)
    return None


def error_message(body: Any) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        msg = err.get("message") if isinstance(err, dict) else body.get("msg") or body.get("message")
        return str(msg or "")
    return ""


def seconds_until_flush(message: str, now: datetime | None = None) -> int | None:
    """Parse ``next_flush_time`` (epoch s/ms or ``YYYY-MM-DD HH:MM[:SS]``, UTC assumed) out of a message."""
    m = _FLUSH.search(message)
    if not m:
        return None
    now = now or datetime.now(UTC)
    raw = m.group(1)
    try:
        if raw.isdigit():
            ts = int(raw) / (1000 if len(raw) == 13 else 1)
            target = datetime.fromtimestamp(ts, UTC)
        else:
            target = datetime.fromisoformat(raw.replace(" ", "T")).replace(tzinfo=UTC)
    except ValueError:
        return None
    return max(1, min(MAX_RETRY_AFTER, int((target - now).total_seconds())))


class TableErrorClassifier:
    """Default classifier. Unknown codes keep the upstream status; 5xx without a JSON body are sanitized."""

    def classify(self, status: int, body: Any, headers: dict[str, str] | None = None) -> ClassifiedError:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        code = error_code(body)
        msg = error_message(body)
        if code in CODE_TABLE:
            st, kind, retryable, default_ra, etype = CODE_TABLE[code]
            ra = None
            if retryable:
                ra = seconds_until_flush(msg) or _header_int(headers.get("retry-after")) or default_ra
            return ClassifiedError(st, status, code, kind, retryable, ra, etype, msg, changed=st != status)
        if not isinstance(body, dict) or ("error" not in body and "code" not in body):
            # no JSON error object: an HTML page or a stack trace. Never pass internals to the client.
            st = 502 if status >= 500 else status
            return ClassifiedError(
                st,
                status,
                code,
                "leak" if status >= 500 else "other",
                status >= 500,
                _header_int(headers.get("retry-after")),
                "api_error",
                "Upstream returned an error without a JSON body.",
                sanitized=True,
                changed=st != status,
            )
        retryable = status in (408, 409, 429) or status >= 500
        kind = (
            "rate_limit"
            if status == 429
            else "server"
            if status >= 500
            else "invalid_request"
            if status < 500
            else "other"
        )
        etype = {429: "rate_limit_error", 401: "authentication_error", 404: "not_found_error"}.get(
            status, "api_error" if status >= 500 else "invalid_request_error"
        )
        return ClassifiedError(
            status,
            status,
            code,
            kind,
            retryable,
            _header_int(headers.get("retry-after")) if retryable else None,
            etype,
            msg,
        )


def _header_int(v: str | None) -> int | None:
    try:
        return max(0, int(float(v))) if v is not None else None
    except ValueError:
        return None


def parse_body(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None
