"""Versioned price table and cost computation (kept behind this small interface so issue 12's richer table can
replace it at merge: anything with ``cost_usd(model, provider, usage)`` and ``version`` works).

Usage uses the OpenAI/OTel convention of SwitchTrace: ``input_tokens_total`` includes the cached part, and
``output_tokens_total`` includes reasoning tokens (billed as output).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

DEFAULT_TABLE = Path(__file__).with_name("prices.yaml")


class CostModel(Protocol):
    version: str

    def cost_usd(self, model: str, provider: str | None, usage: dict[str, Any]) -> float | None: ...


@dataclass(frozen=True)
class PriceEntry:
    model: str
    input: float
    cached_input: float
    output: float
    source_url: str
    as_of: str
    provider: str = "*"
    note: str = ""


class PriceTable:
    def __init__(self, entries: list[PriceEntry], version: str, sources: list[str] | None = None) -> None:
        self.version = version
        self.sources = sources or []
        self.entries = {(e.provider, e.model): e for e in entries}

    @classmethod
    def load(cls, *paths: Path | str) -> PriceTable:
        """Load the shipped table, then overlay extra files (later wins on the same provider+model)."""
        files = [DEFAULT_TABLE, *[Path(p) for p in paths]]
        entries: dict[tuple[str, str], PriceEntry] = {}
        versions = []
        for f in files:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            versions.append(str(data.get("version", "unversioned")))
            for raw in data.get("entries", []):
                missing = [k for k in ("model", "input", "output", "source_url", "as_of") if k not in raw]
                if missing:
                    raise ValueError(f"{f}: price entry {raw.get('model')!r} lacks {missing}")
                e = PriceEntry(
                    model=raw["model"],
                    input=float(raw["input"]),
                    cached_input=float(raw.get("cached_input", raw["input"])),
                    output=float(raw["output"]),
                    source_url=str(raw["source_url"]),
                    as_of=str(raw["as_of"]),
                    provider=str(raw.get("provider", "*")),
                    note=str(raw.get("note", "")),
                )
                entries[(e.provider, e.model)] = e
        return cls(list(entries.values()), "+".join(versions), [str(f) for f in files])

    def lookup(self, model: str, provider: str | None = None) -> PriceEntry | None:
        base = model.rsplit("/", 1)[-1].lower()
        for key in ((provider or "*", base), ("*", base)):
            if key in self.entries:
                return self.entries[key]
        return None

    def cost_usd(self, model: str, provider: str | None, usage: dict[str, Any]) -> float | None:
        """USD for one call, or None when the model has no price (shown as ``not logged``, never zero)."""
        e = self.lookup(model, provider)
        if e is None:
            return None
        total = int(usage.get("input_tokens_total") or 0)
        cached = min(int(usage.get("cache_read_tokens") or 0), total)
        out = int(usage.get("output_tokens_total") or 0)
        return ((total - cached) * e.input + cached * e.cached_input + out * e.output) / 1_000_000

    def describe(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "files": self.sources,
            "entries": [
                {
                    "provider": e.provider,
                    "model": e.model,
                    "input": e.input,
                    "cached_input": e.cached_input,
                    "output": e.output,
                    "source_url": e.source_url,
                    "as_of": e.as_of,
                }
                for e in sorted(self.entries.values(), key=lambda e: (e.model, e.provider))
            ],
        }
