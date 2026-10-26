"""Deploy tab: builds, passes the token check, carries the calculator payload; the token check can fail."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from glmkit.cli import build_app
from glmkit.report.builder import build_site, check_site


def test_deploy_tab_builds_with_payload_and_saved_run(tmp_path) -> None:
    res = tmp_path / "results"
    r = CliRunner().invoke(build_app(), ["deploy", "size", "--out", str(res)])
    assert r.exit_code == 0, r.output
    site = tmp_path / "site"
    built = build_site(res, site)
    assert "deploy" in built.pages and built.findings == []
    data = json.loads((site / "data" / "deploy.json").read_text())
    assert data["payload"]["spec"]["checkpoint_gb"]["fp8"] > 755 and len(data["saved"]) == 1
    assert all(v["ok"] for v in data["validation"])
    html = (site / "index.html").read_text()
    assert (
        "GLMDeploySizing" in html
        and "heuristic" in html
        and "http://" not in html.split("<body>")[1].split("<script")[0]
    )


def test_token_check_fails_on_a_planted_raw_colour_in_the_deploy_css(tmp_path) -> None:
    """Plant a raw colour in the deploy tab's CSS (as the builder would inline it) and see the check fail."""
    res = tmp_path / "results"
    res.mkdir()
    site = tmp_path / "site"
    clean = build_site(res, site)
    assert clean.findings == []
    css = (Path(__file__).resolve().parents[1] / "src/glmkit/report/pages/deploy.css").read_text()
    index = site / "index.html"
    index.write_text(index.read_text().replace(css, css + "\n.dp-note { color: #ff0000; }\n"))
    findings = check_site(site)
    assert any("raw-color" in f and "#ff0000" in f for f in findings), findings
