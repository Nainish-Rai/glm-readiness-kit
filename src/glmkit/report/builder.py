"""Static dashboard builder: result envelopes -> ``site/`` that opens from disk (file://).

Output:
  site/index.html             shell + inline kit CSS (with token contract) + inline shell/page scripts
  site/assets/                vendored Observatory files (tokens.css, components.css, components.iife.js, LICENSE)
  site/data/<page>.js|.json   one data file per page (``window.GLMKIT_DATA[id] = ...``)
  site/data/__site.js         site metadata (tab order, counts, build time)

Pages are discovered (``glmkit.report.registry``); the builder never names one.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from glmkit import __version__
from glmkit.report import tokencheck
from glmkit.report.registry import Page, RunRef, discover
from glmkit.results import load_envelopes

SHELL = Path(__file__).resolve().parent / "shell"
VENDOR_ASSETS = ("tokens.css", "components.css", "components.iife.js", "LICENSE")


class BuildError(RuntimeError):
    pass


@dataclass
class BuildResult:
    index: Path
    pages: list[str]
    runs: int
    warnings: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)


def _data_js(key: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"window.GLMKIT_DATA=window.GLMKIT_DATA||{{}};window.GLMKIT_DATA[{json.dumps(key)}]={payload};\n"


def _window(runs: list[RunRef]) -> str:
    if not runs:
        return "no runs yet"
    times = sorted(r.envelope.started_at for r in runs)
    fmt = "%b %d %H:%M"
    return f"{times[0].strftime(fmt)} to {times[-1].strftime(fmt)} UTC · {len(runs)} run(s)"


def build_site(results: Path, out: Path, *, pages: list[Page] | None = None, check: bool = True) -> BuildResult:
    found, warnings = load_envelopes(results) if results.exists() else ([], [f"{results} does not exist"])
    base = results if results.is_dir() else results.parent
    runs = [RunRef(envelope=env, path=p.relative_to(base).as_posix()) for p, env in found]
    pages = pages if pages is not None else discover()

    out.mkdir(parents=True, exist_ok=True)
    (out / "assets").mkdir(exist_ok=True)
    data_dir = out / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir()
    for name in VENDOR_ASSETS:
        shutil.copyfile(tokencheck.VENDOR / name, out / "assets" / name)

    site_pages: list[dict[str, Any]] = []
    styles = [(SHELL / "kit.css").read_text(encoding="utf-8")]
    scripts = [(SHELL / "shell.js").read_text(encoding="utf-8")]
    data_tags = []
    views = []
    for page in pages:
        data = page.collect(runs)
        if data is None:
            continue
        (data_dir / f"{page.id}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        (data_dir / f"{page.id}.js").write_text(_data_js(page.id, data), encoding="utf-8")
        data_tags.append(f'<script src="data/{page.id}.js"></script>')
        scripts.append(
            f"/* page: {page.id} ({type(page).__module__}) */\n"
            + page.asset_path(page.script).read_text(encoding="utf-8")
        )
        if page.css:
            styles.append(f"/* page: {page.id} */\n" + page.asset_path(page.css).read_text(encoding="utf-8"))
        views.append(f'  <section class="view" id="view-{page.id}" hidden></section>')
        site_pages.append({"id": page.id, "title": page.title, "order": page.order, "count": data.get("count")})

    site = {
        "kit_version": __version__,
        "generated_at": datetime.now(UTC).isoformat(),
        "run_count": len(runs),
        "results": str(results),
        "window": _window(runs),
        "pages": site_pages,
    }
    (data_dir / "__site.js").write_text(_data_js("__site", site), encoding="utf-8")
    data_tags.insert(0, '<script src="data/__site.js"></script>')

    style = "\n".join(styles)
    script = "\n".join(scripts) + "\nGLMKit.boot();"
    contract, missing = tokencheck.contract_block(style + "\n" + script)
    if missing:
        raise BuildError(f"page code reads tokens that tokens.css does not define: {', '.join(missing)}")
    html = (SHELL / "index.html.tmpl").read_text(encoding="utf-8")
    for key, value in {
        "TITLE": "GLM readiness kit",
        "STYLE": contract + "\n" + style,
        "VIEWS": "\n".join(views),
        "DATA_SCRIPTS": "\n".join(data_tags),
        "SCRIPT": script,
    }.items():
        html = html.replace("{{" + key + "}}", value)
    if "</script" in script.replace("</script>\n", ""):
        raise BuildError("a page script contains '</script', which would end the inline script")
    index = out / "index.html"
    index.write_text(html, encoding="utf-8")
    result = BuildResult(index=index, pages=[p["id"] for p in site_pages], runs=len(runs), warnings=warnings)
    if check:
        result.findings = check_site(out)
    return result


def check_site(site: Path) -> list[str]:
    """Observatory token check over everything the kit authors in the site (index.html inlines all kit and
    page CSS/JS) plus the vendored stylesheets. Data files are data, not styling, and are not checked."""
    files = [site / "index.html", site / "assets" / "tokens.css", site / "assets" / "components.css"]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise BuildError(f"not a built site, missing: {', '.join(missing)}")
    return tokencheck.check_files(files)
