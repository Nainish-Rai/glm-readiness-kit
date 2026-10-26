"""Error classifier: Z.ai business codes -> HTTP status, retryability and ``Retry-After``.

There is no code table here: the one canonical table is ``glmkit.zai_errors`` (issue 19). This module adapts its
``classify()`` to the bridge's ``ClassifiedError`` and keeps the ``ErrorClassifier`` protocol so callers can still
pass their own via ``BridgeCore(classifier=...)``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from glmkit.zai_errors import DEFAULT_RETRY_AFTER_S, flush_seconds
from glmkit.zai_errors import classify as zai_classify


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


MAX_RETRY_AFTER = 24 * 3600

#: canonical category (glmkit.zai_errors.CATEGORIES) -> OpenAI/Anthropic style ``error.type``. Not a code table.
ERROR_TYPE: dict[str, str] = {
    "auth": "authentication_error",
    "billing": "billing_error",
    "permission": "permission_error",
    "usage_limit": "rate_limit_error",
    "rate_limit": "rate_limit_error",
    "overloaded": "overloaded_error",
    "invalid_request": "invalid_request_error",
    "context_length": "invalid_request_error",
    "content_safety": "invalid_request_error",
    "not_found": "not_found_error",
    "server": "api_error",
}


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
    """Seconds until the ``next_flush_time`` in a message (parsed by glmkit.zai_errors), clamped to 1..24h."""
    secs = flush_seconds(message, now)
    return None if secs is None else max(1, min(MAX_RETRY_AFTER, int(secs)))


class TableErrorClassifier:
    """Default classifier. Unknown codes keep the upstream status; 5xx without a JSON body are sanitized."""

    def classify(self, status: int, body: Any, headers: dict[str, str] | None = None) -> ClassifiedError:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        code = error_code(body)
        msg = error_message(body)
        info = zai_classify(code, http_status=status, message=msg, headers=headers)
        if info.known and code is not None:
            st = info.suggested_http
            ra = None
            if info.retryable:
                hint = info.retry_after_s
                ra = (
                    max(1, min(MAX_RETRY_AFTER, int(hint)))
                    if hint is not None
                    else DEFAULT_RETRY_AFTER_S.get(info.category)
                )
            etype = ERROR_TYPE[info.category]
            return ClassifiedError(
                st, status, info.code, info.category, info.retryable, ra, etype, msg, changed=st != status
            )
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
