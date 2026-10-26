"""Report builder: static site from envelopes, discovered tabs, token check (and that it can fail)."""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pytest

from glmkit.conform.runner import RunOptions, run_conformance
from glmkit.contracts.profile import get_profile
from glmkit.report.builder import BuildError, build_site
from glmkit.report.registry import Page, discover, register_page, unregister_page


@pytest.fixture
async def results(tmp_path):
    out = tmp_path / "results"
    await run_conformance(get_profile("mock"), RunOptions(patterns=["WIRE-00*"], out_dir=out))
    await run_conformance(
        get_profile("mock"), RunOptions(patterns=["WIRE-00*"], mock_defects=["forced-thinking-500"], out_dir=out)
    )
    return out


def _data(site, page: str) -> Any:
    return json.loads((site / "data" / f"{page}.json").read_text())


async def test_build_site_with_runs_and_conformance_tabs(results, tmp_path) -> None:
    site = tmp_path / "site"
    res = build_site(results, site)
    assert res.pages[:2] == ["runs", "conformance"] and res.runs == 2 and res.findings == []
    html = (site / "index.html").read_text()
    assert 'src="assets/components.iife.js"' in html and 'type="module"' not in html  # file:// safe
    assert "@contract-start" in html and "prefers-reduced-motion" in html
    for f in ("tokens.css", "components.css", "components.iife.js", "LICENSE"):
        assert (site / "assets" / f).exists()
    runs = _data(site, "runs")
    assert runs["count"] == 2 and {r["verdict"] for r in runs["rows"]} == {"pass", "fail"}
    conf = _data(site, "conformance")
    assert [c["key"] for c in conf["columns"]] == ["mock+forced-thinking-500", "mock"]
    row = next(r for r in conf["rows"] if r["id"] == "WIRE-001[nonstream]")
    assert row["cells"]["mock"]["status"] == "pass" and row["cells"]["mock+forced-thinking-500"]["status"] == "fail"
    evidence = conf["items"][row["cells"]["mock+forced-thinking-500"]["ref"]]["item"]["evidence"][0]
    assert "Traceback" in evidence["response"]["body_text"]


def test_empty_results_still_build(tmp_path) -> None:
    (tmp_path / "results").mkdir()
    res = build_site(tmp_path / "results", tmp_path / "site")
    assert res.runs == 0 and res.findings == [] and "conformance" in res.pages


def _page(tmp_path, css: str) -> Page:
    """A throwaway tab whose CSS is planted by the test."""
    (tmp_path / "planted.js").write_text('GLMKit.registerPage("planted", { render(panel) {} });\n')
    (tmp_path / "planted.css").write_text(css)

    class Planted(Page):
        id = "planted"
        title = "Planted"
        order = 98
        script = "planted.js"
        css: ClassVar[str | None] = "planted.css"

        def collect(self, runs):
            return {"count": 0}

        def asset_path(self, name):
            return tmp_path / name

    return Planted()


@pytest.mark.parametrize(
    ("css", "expect"), [(".x { color: #ff0000; }", "raw-color"), (".x { color: var(--fg-meta); }", None)]
)
def test_token_check_fails_on_planted_raw_colour(tmp_path, css, expect) -> None:
    res = build_site(tmp_path, tmp_path / "site", pages=[*discover(), _page(tmp_path, css)])
    if expect:
        assert any(expect in f and "#ff0000" in f for f in res.findings), res.findings
    else:
        assert res.findings == []


def test_undefined_token_is_a_build_error(tmp_path) -> None:
    with pytest.raises(BuildError, match="--no-such-token"):
        build_site(tmp_path, tmp_path / "site", pages=[_page(tmp_path, ".x { color: var(--no-such-token); }")])


def test_pages_are_discovered_in_order() -> None:
    ids = [p.id for p in discover()]
    assert ids.index("runs") < ids.index("conformance")


def test_register_and_unregister_page() -> None:
    @register_page
    class Temp(Page):
        id = "temp"
        title = "Temp"
        script = "runs.js"

        def collect(self, runs):
            return None

    try:
        assert "temp" in [p.id for p in discover()]
    finally:
        unregister_page("temp")
    assert "temp" not in [p.id for p in discover()]
