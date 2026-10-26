"""``web_search`` server-tool emulation.

GLM hosts do not implement Anthropic's server-side ``web_search`` tool. The bridge either

* **emulates** it when a ``SearchBackend`` is configured: the tool is offered to the model as an ordinary function
  tool, the bridge runs each search itself and feeds the results back, and the client receives the
  ``server_tool_use`` + ``web_search_tool_result`` block pair Anthropic would have produced, or
* **strips** it cleanly (the default): the tool, a ``tool_choice`` that forces it and earlier server-tool blocks
  are removed so the upstream never sees a shape it would reject.

A backend is any object with ``async def search(query, *, max_results) -> list[SearchResult]``. The kit never
calls a real search API in tests: use ``FakeSearchBackend``.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

WEB_SEARCH_NAME = "web_search"
WEB_SEARCH_DESCRIPTION = (
    "Search the web for current information. Returns a numbered list of results with title, url and snippet."
)
WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "The search query"}},
    "required": ["query"],
}
DEFAULT_MAX_USES = 5


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str = ""
    page_age: str | None = None


@runtime_checkable
class SearchBackend(Protocol):
    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]: ...


class FakeSearchBackend:
    """Deterministic backend for tests and demos: the same query always yields the same results. No network."""

    def __init__(self, results: dict[str, list[SearchResult]] | None = None) -> None:
        self.results = dict(results or {})
        self.queries: list[str] = []

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        self.queries.append(query)
        if query in self.results:
            return self.results[query][:max_results]
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:40] or "query"
        return [
            SearchResult(
                url=f"https://example.test/{slug}/{i}",
                title=f"Result {i} for {query}",
                snippet=f"Deterministic snippet {i} about {query}.",
                page_age=f"January {i}, 2026",
            )
            for i in range(1, min(max_results, 3) + 1)
        ]


def is_web_search_tool(tool: Any) -> bool:
    return isinstance(tool, dict) and str(tool.get("type", "")).startswith("web_search")


def function_tool() -> dict[str, Any]:
    return {"name": WEB_SEARCH_NAME, "description": WEB_SEARCH_DESCRIPTION, "input_schema": WEB_SEARCH_SCHEMA}


def format_results(results: list[SearchResult]) -> str:
    if not results:
        return "No results."
    return "\n\n".join(f"{i}. {r.title}\n{r.url}\n{r.snippet}".rstrip() for i, r in enumerate(results, 1))


def result_block(tool_use_id: str, results: list[SearchResult] | None, *, error: str | None = None) -> dict[str, Any]:
    """The ``web_search_tool_result`` block. ``encrypted_content`` is only opaque, not encrypted: the bridge never
    needs to read it back (earlier server-tool blocks are converted from the text the model saw)."""
    if error:
        content: Any = {"type": "web_search_tool_result_error", "error_code": error}
    else:
        content = [
            {
                "type": "web_search_result",
                "url": r.url,
                "title": r.title,
                "encrypted_content": base64.b64encode(hashlib.sha256((r.url + r.snippet).encode()).digest()).decode(),
                "page_age": r.page_age,
            }
            for r in results or []
        ]
    return {"type": "web_search_tool_result", "tool_use_id": tool_use_id, "content": content}


def server_tool_id(tool_use_id: str) -> str:
    return "srvtoolu_" + tool_use_id.split("_", 1)[-1] if not tool_use_id.startswith("srvtoolu_") else tool_use_id
