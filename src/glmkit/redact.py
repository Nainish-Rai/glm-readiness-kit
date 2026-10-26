"""Secret redaction for evidence, results and logs.

Keys only ever come from environment variables named in an endpoint profile. Two layers keep them out of
anything written to disk:

1. header redaction by name (``Authorization``, ``x-api-key`` ...), applied when evidence is recorded;
2. value scrubbing: every string written to results, traces or logs is scanned for the literal secret
   values the run knows about (the target's key) and those are replaced.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

REDACTED = "[REDACTED]"
SENSITIVE_HEADERS = frozenset(
    {"authorization", "x-api-key", "api-key", "proxy-authorization", "cookie", "set-cookie", "x-goog-api-key"}
)
MIN_SECRET_LEN = 6


def redact_headers(headers: Iterable[tuple[str, str]] | dict[str, str]) -> dict[str, str]:
    items = headers.items() if isinstance(headers, dict) else headers
    return {k: (REDACTED if k.lower() in SENSITIVE_HEADERS else v) for k, v in items}


class Redactor:
    """Replaces known secret values anywhere in strings or nested JSON-like data."""

    def __init__(self, secrets: Iterable[str | None] = ()) -> None:
        self._secrets: set[str] = set()
        for s in secrets:
            self.add(s)

    def add(self, secret: str | None) -> None:
        if secret and len(secret) >= MIN_SECRET_LEN:
            self._secrets.add(secret)

    @property
    def active(self) -> bool:
        return bool(self._secrets)

    def text(self, s: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            if secret in s:
                s = s.replace(secret, REDACTED)
        return s

    def scrub(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {self.scrub(k): self.scrub(v) for k, v in obj.items()}
        if isinstance(obj, list | tuple):
            return [self.scrub(v) for v in obj]
        return obj


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs secrets from the formatted message."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = self.redactor.text(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        return True
