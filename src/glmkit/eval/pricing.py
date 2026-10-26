"""Eval-side view of the one price table, ``glmkit.cost.prices`` (issue 18). No price data lives here.

The eval engine needs ``cost_usd(model, provider, usage)`` and ``version``; both come from the canonical table,
optionally layered with the user's own ``--prices`` files (same entry format as ``glmkit/cost/prices.yaml``, a
``prices:`` list; later files win on the same host + model).

Usage uses the OpenAI/OTel convention of SwitchTrace: ``input_tokens_total`` includes the cached part, and
``output_tokens_total`` includes reasoning tokens (billed as output).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import yaml

from glmkit.cost.prices import PriceEntry, load_price_table
from glmkit.cost.prices import PriceTable as CanonicalPriceTable


class CostModel(Protocol):
    version: str

    def cost_usd(self, model: str, provider: str | None, usage: dict[str, Any]) -> float | None: ...


class PriceTable:
    def __init__(self, table: CanonicalPriceTable, version: str, sources: list[str]) -> None:
        self.table = table
        self.version = version
        self.sources = sources

    @classmethod
    def load(cls, *paths: Path | str) -> PriceTable:
        """The canonical table, then extra files overlaid (later wins on the same host + model)."""
        base = load_price_table()
        versions = [str(base.version)]
        for p in paths:
            versions.append(
                str((yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}).get("version", "unversioned"))
            )
        return cls(base.with_overlay(*paths), "+".join(versions), ["glmkit.cost.prices", *[str(p) for p in paths]])

    def lookup(self, model: str, provider: str | None = None) -> PriceEntry | None:
        """The priced entry for this call, or None (not logged). A provider with no entry for the model uses the
        Z.ai list price; a provider whose price is not logged (Bedrock) stays not logged."""
        return self.table.lookup_for_provider(model, provider)

    def cost_usd(self, model: str, provider: str | None, usage: dict[str, Any]) -> float | None:
        return self.table.cost_usd(model, provider, usage)

    def describe(self, targets: list[tuple[str, str | None]] | None = None) -> dict[str, Any]:
        """Entries as the Migration tab and ``glmkit eval prices`` show them. ``targets`` = (model, provider) pairs
        of a run: only the entries those calls were priced with; without it, every priced entry."""
        if targets is None:
            rows = [e for e in self.table.prices if e.input is not None and e.output is not None]
        else:
            found = [self.lookup(m, p) for m, p in targets]
            rows = list({(e.host, e.model): e for e in found if e is not None}.values())
        return {
            "version": self.version,
            "files": self.sources,
            "entries": [
                {
                    "provider": "*" if e.host == "zai-api" else e.host,
                    "model": e.model,
                    "input": e.input,
                    "cached_input": e.cached_input if e.cached_input is not None else e.input,
                    "output": e.output,
                    "source_url": e.source,
                    "as_of": e.retrieved.isoformat(),
                    "mock": e.mock,
                }
                for e in sorted(rows, key=lambda e: (e.model, e.host))
            ],
        }
