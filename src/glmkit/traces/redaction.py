"""Optional redaction pass for imported traces: emails, phone numbers, API-key-like strings, custom regexes.

Applied to every string in a trace except identifiers and provenance (``call_id``, ``trace_id``,
``parent_call_id``, ``ts_start``, ``source``). Pattern based, so it is best effort: review a sample before
sharing a trace set outside the customer's boundary.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from glmkit.contracts.trace import SwitchTrace

BUILTIN: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "api_key": re.compile(
        r"(?:sk-ant-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}"
        r"|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|[0-9a-f]{32}\.[A-Za-z0-9]{12,}"
        r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
        r"|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{16,}"
        r"|(?i:(?:api[_-]?key|secret|token|password|passwd)\s*[:=]\s*[\"']?)[^\s\"',;]{8,})"
    ),
    "phone": re.compile(
        r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])\d{3,4}[\s.-]\d{3,4}(?![\w-])"
    ),
}
SKIP_KEYS = frozenset({"call_id", "trace_id", "parent_call_id", "ts_start", "source", "schema"})


class TraceRedactor:
    def __init__(self, custom: list[str] | None = None) -> None:
        self.patterns = dict(BUILTIN)
        for i, rx in enumerate(custom or []):
            self.patterns[f"custom{i + 1}"] = re.compile(rx)
        self.counts: Counter[str] = Counter()

    def text(self, s: str) -> str:
        for name, rx in self.patterns.items():
            s, n = rx.subn(f"[REDACTED:{name}]", s)
            self.counts[name] += n
        return s

    def scrub(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {k: self.scrub(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.scrub(v) for v in obj]
        return obj

    def trace(self, t: SwitchTrace) -> SwitchTrace:
        d = t.model_dump(mode="json", by_alias=True)
        out = {k: (v if k in SKIP_KEYS else self.scrub(v)) for k, v in d.items()}
        out["privacy"]["redacted"] = True
        return SwitchTrace.model_validate(out)
