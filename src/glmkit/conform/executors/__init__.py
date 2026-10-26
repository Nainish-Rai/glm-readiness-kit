"""Executors decide how a case talks to the target for one attempt and return the exchanges it produced.

``single`` (default) sends the case request once. New case shapes (multi-turn agent loops, latency probes,
soak runs ...) register an executor in a module of this package (or a plugin) and select it with
``executor: <name>`` in the case file; per-case knobs go in ``executor_config``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from glmkit.client import GLMClient
from glmkit.contracts.case import ConformanceCase, Variant
from glmkit.contracts.evidence import Exchange
from glmkit.contracts.profile import EndpointProfile
from glmkit.plugins import import_submodules, load_plugins


@dataclass
class ExecContext:
    case: ConformanceCase
    target: EndpointProfile
    client: GLMClient
    request: dict[str, Any]  # canonical request after the variant merge and $ref resolution
    mode: str  # nonstream | stream
    attempt: int
    variant: Variant | None = None
    config: dict[str, Any] = field(default_factory=dict)  # case.executor_config

    @property
    def stream(self) -> bool:
        return self.mode == "stream"


ExecutorFn = Callable[[ExecContext], Awaitable[list[Exchange]]]
_REGISTRY: dict[str, ExecutorFn] = {}
_discovered = False


def executor(name: str) -> Callable[[ExecutorFn], ExecutorFn]:
    def deco(fn: ExecutorFn) -> ExecutorFn:
        if name in _REGISTRY and _REGISTRY[name] is not fn:
            raise ValueError(f"executor {name!r} registered twice")
        _REGISTRY[name] = fn
        return fn

    return deco


def discover() -> dict[str, ExecutorFn]:
    global _discovered
    if not _discovered:
        import_submodules(__name__)
        _discovered = True
    load_plugins()
    return dict(_REGISTRY)


def get_executor(name: str) -> ExecutorFn:
    reg = discover()
    if name not in reg:
        raise KeyError(f"unknown executor {name!r}; known: {', '.join(sorted(reg))}")
    return reg[name]
