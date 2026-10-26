"""Versioned price table: the one place glmkit reads prices from.

Public API (also documented in docs/contracts.md, "Price table")::

    from glmkit.cost.prices import load_price_table, price_for
    table = load_price_table()                  # shipped prices.yaml, or $GLMKIT_PRICES, or an explicit path
    entry = price_for("glm-5.3")                # PriceEntry | None; host defaults to "zai-api"
    entry.input, entry.cached_input, entry.output   # USD per 1M tokens, None = "not logged"
    table.hosts("glm-5.3")                      # every host that lists the model
    table.coding_plan.tiers

Every entry must carry a ``source`` URL and a ``retrieved`` date; loading fails otherwise (so a test can pin it).
"""

from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_HOST = "zai-api"
# profile `provider` values that name a host differently from the table
PROVIDER_HOSTS = {"zai": DEFAULT_HOST, "*": DEFAULT_HOST}
SHIPPED = Path(__file__).with_name("prices.yaml")


class _Sourced(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(description="URL (or 'URL (note)') the value was read from")
    retrieved: date

    @field_validator("source")
    @classmethod
    def _has_url(cls, v: str) -> str:
        if not v.strip().startswith(("http://", "https://")):
            raise ValueError("source must start with a URL")
        return v


class PriceEntry(_Sourced):
    host: str
    model: str
    input: float | None = None
    cached_input: float | None = None
    output: float | None = None
    note: str | None = None
    title: str | None = None  # label for UIs (Deploy tab API picker)
    secondary: bool = False  # aggregator / calculator page rather than the vendor's own price page
    mock: bool = False  # mock-server stand-in model: illustrative, never a real vendor price

    @property
    def complete(self) -> bool:
        return None not in (self.input, self.cached_input, self.output)


class Tier(_Sourced):
    name: str
    usd_per_month: float
    credits_5h: int
    credits_weekly: int


class Discount(_Sourced):
    period: str
    discount: float


class PeakWindow(_Sourced):
    description: str
    tz: str
    start_hour: int
    end_hour: int
    weekdays_only: bool = True


class Multiplier(_Sourced):
    value: float
    heuristic: bool = False


class CodingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tiers: list[Tier]
    billing_discounts: list[Discount] = Field(default_factory=list)
    peak_window: PeakWindow
    off_peak_multiplier: Multiplier
    reported_peak_multiplier: Multiplier
    counted_tokens: str = ""


class EffortFactor(_Sourced):
    from_: str = Field(alias="from")
    to: str
    factor: float
    heuristic: bool = True
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PriceTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    currency: str = "USD"
    unit: str = "per_million_tokens"
    updated: date
    prices: list[PriceEntry]
    coding_plan: CodingPlan
    effort_output_factor: list[EffortFactor] = Field(default_factory=list)

    def lookup(self, model: str, host: str = DEFAULT_HOST) -> PriceEntry | None:
        for e in self.prices:
            if e.host == host and e.model == model:
                return e
        return None

    def hosts(self, model: str) -> list[PriceEntry]:
        return [e for e in self.prices if e.model == model]

    def models(self) -> list[str]:
        """Real (non-mock) model names."""
        return sorted({e.model for e in self.prices if not e.mock})

    def lookup_for_provider(self, model: str, provider: str | None = None) -> PriceEntry | None:
        """Entry whose price can be applied to a call on ``provider`` (a host name, or ``zai`` for ``zai-api``).

        A model with no entry on that host falls back to the Z.ai list price; a host entry that exists but has no
        input or output price returns None (not logged), it is never replaced by another host's price.
        """
        base = model.rsplit("/", 1)[-1].lower()
        host = PROVIDER_HOSTS.get(provider or "", provider or DEFAULT_HOST)
        e = self.lookup(base, host)
        if e is None and host != DEFAULT_HOST:
            e = self.lookup(base, DEFAULT_HOST)
        return e if e is not None and e.input is not None and e.output is not None else None

    def cost_usd(self, model: str, provider: str | None, usage: dict[str, Any]) -> float | None:
        """USD for one call (``input_tokens_total`` includes the cached part, ``output_tokens_total`` includes
        reasoning), or None when the model has no price (``not logged``, never zero). A missing cached-input price
        is billed as fresh input (an upper bound)."""
        e = self.lookup_for_provider(model, provider)
        if e is None or e.input is None or e.output is None:
            return None
        total = int(usage.get("input_tokens_total") or 0)
        cached = min(int(usage.get("cache_read_tokens") or 0), total)
        out = int(usage.get("output_tokens_total") or 0)
        cached_price = e.cached_input if e.cached_input is not None else e.input
        return ((total - cached) * e.input + cached * cached_price + out * e.output) / 1_000_000

    def with_overlay(self, *paths: str | Path) -> PriceTable:
        """Copy with extra files layered on top (``prices:`` lists in the same entry format; later wins on the
        same host + model). The files need no coding plan."""
        merged = {(e.host, e.model): e for e in self.prices}
        for p in paths:
            raw = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
            for item in raw.get("prices", []):
                e = PriceEntry.model_validate(item)
                merged[(e.host, e.model)] = e
        return self.model_copy(update={"prices": list(merged.values())})

    def effort_factor(self, from_effort: str, to_effort: str) -> EffortFactor | None:
        for f in self.effort_output_factor:
            if f.from_ == from_effort and f.to == to_effort:
                return f
        return None


def load_price_table(path: str | Path | None = None) -> PriceTable:
    """Load and validate a price table. ``path`` > ``$GLMKIT_PRICES`` > the shipped file."""
    p = Path(path) if path else Path(os.environ.get("GLMKIT_PRICES") or SHIPPED)
    return PriceTable.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def _default() -> PriceTable:
    return load_price_table()


def price_for(model: str, host: str = DEFAULT_HOST, table: PriceTable | None = None) -> PriceEntry | None:
    """Price entry for ``model`` on ``host`` (default Z.ai pay-as-you-go), or None when the table has none."""
    return (table or _default()).lookup(model, host)
