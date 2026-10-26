"""Token and spend budgets per session and per key. Prices come from ``glmkit.cost.prices`` only."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from glmkit.cost.prices import PriceEntry, price_for
from glmkit.guard.config import Budget

M = 1_000_000
MAX_BURN = 2000


@dataclass
class Usage:
    input: int
    output: int
    cached: int = 0


def normalize_usage(u: dict[str, Any] | None) -> Usage | None:
    """OpenAI (prompt/completion_tokens) or Anthropic (input/output_tokens + cache_*) usage dicts."""
    if not isinstance(u, dict):
        return None
    if "prompt_tokens" in u or "completion_tokens" in u:
        det = u.get("prompt_tokens_details") or {}
        return Usage(
            int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), int(det.get("cached_tokens") or 0)
        )
    if "input_tokens" in u or "output_tokens" in u:
        cr = int(u.get("cache_read_input_tokens") or 0)
        cw = int(u.get("cache_creation_input_tokens") or 0)
        return Usage(int(u.get("input_tokens") or 0) + cr + cw, int(u.get("output_tokens") or 0), cr)
    return None


def cost_of(u: Usage, entry: PriceEntry | None) -> float | None:
    """USD for one call, or None when the table has no (complete) price. Cache writes bill as fresh input."""
    if entry is None or entry.input is None or entry.output is None:
        return None
    if u.cached and entry.cached_input is None:
        return None
    fresh = max(0, u.input - u.cached)
    return (
        fresh / M * entry.input + (u.cached / M * entry.cached_input if u.cached else 0.0) + u.output / M * entry.output
    )


@dataclass
class Stats:
    tokens: int = 0
    usd: float = 0.0  # sum over the calls that could be priced (a lower bound while unpriced_calls > 0)
    unpriced_calls: int = 0
    requests: int = 0
    soft_fired: set[str] = field(default_factory=set)
    burn: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def exceeded(s: Stats, b: Budget, which: str) -> str | None:
    """'usd' / 'tokens' when the ``soft`` or ``hard`` limit is reached, else None."""
    lim_usd, lim_tok = getattr(b, f"{which}_usd"), getattr(b, f"{which}_tokens")
    if lim_usd is not None and s.usd >= lim_usd:
        return "usd"
    if lim_tok is not None and s.tokens >= lim_tok:
        return "tokens"
    return None


class BudgetTracker:
    def __init__(self, session: Budget, key: Budget, price_host: str = "zai-api") -> None:
        self.session_budget, self.key_budget, self.price_host = session, key, price_host
        self.sessions: dict[str, Stats] = {}
        self.keys: dict[str, Stats] = {}
        self._lock = threading.Lock()

    def _stats(self, scope: str, ident: str) -> Stats:
        table = self.sessions if scope == "session" else self.keys
        return table.setdefault(ident, Stats())

    def hard_hit(self, session: str, key: str | None) -> tuple[str, str, Stats] | None:
        """(scope, unit, stats) of the first hard limit already reached."""
        with self._lock:
            for scope, ident, b in (("session", session, self.session_budget), ("key", key, self.key_budget)):
                if ident is None or not b.active:
                    continue
                s = self._stats(scope, ident)
                unit = exceeded(s, b, "hard")
                if unit:
                    return scope, unit, s
        return None

    def record(
        self, session: str, key: str | None, model: str | None, usage: dict[str, Any] | None, request_id: str
    ) -> dict[str, Any]:
        """Add one finished call. Returns {'tokens', 'usd', 'priced', 'soft': [(scope, unit)], 'hard': [...]}."""
        u = normalize_usage(usage)
        entry = price_for(model, self.price_host) if model else None
        usd = cost_of(u, entry) if u else None
        out: dict[str, Any] = {"tokens": None, "usd": usd, "priced": usd is not None, "soft": [], "hard": []}
        with self._lock:
            for scope, ident, b in (("session", session, self.session_budget), ("key", key, self.key_budget)):
                if ident is None:
                    continue
                s = self._stats(scope, ident)
                s.requests += 1
                if u is not None:
                    s.tokens += u.input + u.output
                if usd is None:
                    s.unpriced_calls += 1  # usd stays a lower bound: a price is never guessed
                else:
                    s.usd = (s.usd or 0.0) + usd
                if scope == "session":
                    s.burn.append(
                        {
                            "ts": datetime.now(UTC).isoformat(),
                            "request_id": request_id,
                            "tokens": (u.input + u.output) if u else None,
                            "usd": usd,
                            "cum_tokens": s.tokens,
                            "cum_usd": s.usd,
                        }
                    )
                    del s.burn[:-MAX_BURN]
                    out["tokens"] = (u.input + u.output) if u else None
                for which in ("soft", "hard"):
                    unit = exceeded(s, b, which)
                    if unit and f"{which}:{unit}" not in s.soft_fired:
                        s.soft_fired.add(f"{which}:{unit}")
                        out[which].append((scope, unit))
        return out
