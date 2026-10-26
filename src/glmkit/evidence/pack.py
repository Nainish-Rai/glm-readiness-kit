# ruff: noqa: E501
"""Build the evidence pack: pages (HTML + Markdown), diagram, AI-BOM, manifest, and a result envelope."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from glmkit import __version__
from glmkit.contracts.envelope import ItemResult, ResultEnvelope, new_run_id
from glmkit.evidence import aibom, dataflow
from glmkit.evidence.checklist import (
    CATALOGUE,
    FRAMEWORKS,
    STATUS_ORDER,
    Ctx,
    Item,
    Resolved,
    framework_counts,
    resolve_all,
    sort_gaps_first,
)
from glmkit.evidence.descriptor import PATH_TITLES, Deployment, load_deployment
from glmkit.evidence.disclaimer import DISCLAIMER
from glmkit.evidence.index import IndexedRun, build_index
from glmkit.evidence.license_notes import RECORDED_AS, license_blocks
from glmkit.evidence.render import UL, Card, Code, Figure, H, P, Table, render
from glmkit.report import tokencheck
from glmkit.results import write_envelope

PAGES = [
    ("index", "Overview"),
    ("checklist", "Checklist"),
    ("data-flow", "Data flow"),
    ("license", "License notes"),
    ("evidence-index", "Evidence index"),
    ("ai-bom", "AI-BOM"),
]
STATUS_TO_ITEM = {"met": "pass", "gap": "fail", "needs-counsel": "soft_fail", "n/a": "skip"}
GENERATED = {
    "data-flow": "data-flow.html",
    "license-notes": "license.html",
    "ai-bom": "ai-bom.cdx.json",
    "disclaimer": "index.html",
}


@dataclass
class PackResult:
    out: Path
    files: list[Path]
    items: list[Resolved]
    deployment: Deployment
    runs: list[IndexedRun]
    bom_errors: list[str]
    envelope_path: Path | None = None
    run_id: str | None = None
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _item_blocks(r: Resolved) -> list[Any]:
    b: list[Any] = [P(r.reason, "why")]
    if r.in_force:
        b.append(P(f"In force: {r.in_force}", "pk-meta"))
    if r.interpretive:
        b.append(P("Depends on legal interpretation: never recorded as met without counsel review.", "pk-meta"))
    if r.prompts:
        b.append(UL(list(r.prompts)))
    cites = []
    for c in r.citations:
        bit = c["label"]
        if c.get("url", "").startswith("http"):
            bit = f"[{c['label']}]({c['url']})"
        cites.append(
            f"{bit} · {c.get('article') or 'no article'} · {c.get('date', 'undated')} · source class {c['reliability']}"
        )
    b.append(P("Citations"))
    b.append(UL(cites))
    ev = []
    for e in r.evidence:
        if e.kind == "run":
            ev.append(f"run [`{e.ref}`](evidence-index.html#run-{e.ref}) ({e.label})")
        elif e.kind == "generated":
            ev.append(f"generated [`{e.ref}`]({e.ref})")
        else:
            ev.append(f"{e.kind} `{e.ref}`")
    b.append(P("Evidence: " + ("; ".join(ev) if ev else "none linked"), "pk-meta"))
    for f in r.flags:
        b.append(P("Unverified or caveat: " + f, "pk-flag"))
    return b


def _blocks_index(
    dep: Deployment, items: list[Resolved], counts: list[dict], runs: list[IndexedRun], files: list[str]
) -> list[Any]:
    total = {s: sum(1 for i in items if i.status == s) for s in STATUS_ORDER}
    rows = [
        [
            c["title"],
            str(c["met"]),
            str(c["gap"]),
            str(c["needs-counsel"]),
            str(c["na"]),
            f"{{bar:{c['met']},{c['gap']},{c['needs-counsel']},{c['na']}}}",
        ]
        for c in counts
        if c["total"]
    ]
    open_items = [i for i in sort_gaps_first(items) if i.status in ("gap", "needs-counsel")]
    return [
        P(dep.description or PATH_TITLES[dep.path]),
        H(2, "Deployment"),
        Table(
            ["Field", "Value"],
            [
                ["Name", f"`{dep.name}`"],
                ["Path", PATH_TITLES[dep.path]],
                ["Model", f"{dep.model.id} ({dep.model.quantization or 'quantization not logged'})"],
                [
                    "Hosting",
                    f"{dep.hosting.provider}, {dep.hosting.region or 'region not stated'} [{dep.hosting.jurisdiction}]",
                ],
                ["Jurisdictions checked", ", ".join(dep.jurisdictions)],
                ["License", RECORDED_AS],
                ["Linked runs", str(len(runs))],
            ],
        ),
        H(2, "Status by framework"),
        P(" · ".join(f"{s} {total[s]}" for s in ("met", "gap", "needs-counsel", "n/a")), "pk-meta"),
        Table(["Framework", "met", "gap", "needs-counsel", "n/a", "Share"], rows),
        H(2, "Open items, gaps first"),
        Table(
            ["Item", "Title", "Status", "Why"],
            [[f"[{i.id}](checklist.html#{i.id})", i.title, f"{{status:{i.status}}}", i.reason] for i in open_items],
        ),
        H(2, "Files"),
        UL([f"[{f}]({f})" for f in files]),
    ]


def _blocks_checklist(items: list[Resolved]) -> list[Any]:
    blocks: list[Any] = [
        P(
            "Each item is a prompt for the human reviewer with a citation and an evidence link. Statuses: "
            "{status:met}, {status:gap}, {status:needs-counsel}, {status:n/a}. Gaps are listed first. "
            "Article numbers are from Regulation (EU) 2024/1689; the amending text is Regulation (EU) "
            "2026/1744, so verify amended wording on EUR-Lex.",
            "pk-meta",
        )
    ]
    for key, meta in FRAMEWORKS.items():
        rows = sort_gaps_first([i for i in items if i.framework == key])
        if not rows:
            continue
        blocks.append(H(2, meta["title"], key))
        for r in rows:
            blocks.append(Card(r.id, f"{{status:{r.status}}} {r.id} {r.title}", _item_blocks(r)))
    return blocks


def _blocks_flow(dep: Deployment, svg: str, mmd: str) -> list[Any]:
    return [
        P(
            "Generated from the deployment descriptor. Dashed edges are returns or flows the descriptor leaves unverified; "
            "bold boxes mark a flagged party (outside the EEA, unknown location, or unverified)."
        ),
        Figure(svg, "![Data-flow diagram](dataflow.svg)"),
        H(2, "Mermaid source"),
        P(
            "Rendered with the kit's built-in SVG renderer for this fixed shape; the Mermaid CLI needs a browser download. "
            "Paste the source into any Mermaid renderer for an alternative layout.",
            "pk-meta",
        ),
        Code(mmd, "mermaid"),
    ]


def _blocks_runs(runs: list[IndexedRun], items: list[Resolved]) -> list[Any]:
    if not runs:
        return [
            P(
                "No runs found in the results folder. Claims in this pack have no reproducible run behind them yet; "
                "run `glmkit conform run` (and eval, guard, deploy) and rebuild."
            )
        ]
    rows = []
    for r in runs:
        rows.append(
            [
                f"{{anchor:run-{r.run_id}}}`{r.run_id}`",
                r.component,
                r.verdict,
                r.started_at[:19].replace("T", " ") + " UTC",
                f"`results/{r.path}`",
                ", ".join(r.models) or "not logged",
                f"{r.passed}/{r.total}",
                ", ".join(f"[{i}](checklist.html#{i})" for i in r.linked_items) or "none",
            ]
        )
    return [
        P(
            "Runs the checklist links to, by run id. Every claim of `met` that rests on a run points at one of these "
            "reproducible result envelopes."
        ),
        Table(["Run id", "Component", "Verdict", "Started", "File", "Model", "Passed", "Linked items"], rows),
    ]


def _blocks_bom(doc: dict[str, Any], errors: list[str], spdx: bool) -> list[Any]:
    comps = [
        [c["type"], c["name"], c.get("version", ""), ", ".join(h["alg"] for h in c.get("hashes", [])) or "no hash"]
        for c in doc["components"]
    ]
    svcs = [
        [
            s["name"],
            s.get("description", ""),
            next((p["value"] for p in s["properties"] if p["name"] == "glmkit:jurisdiction"), ""),
        ]
        for s in doc["services"]
        if "properties" in s
    ]
    ok = not errors
    return [
        P(
            f"CycloneDX {doc['specVersion']} AI-BOM, [ai-bom.cdx.json](ai-bom.cdx.json). Validation against the vendored "
            f"JSON schemas for {' and '.join(aibom.VALIDATE_VERSIONS)}: **{'valid' if ok else 'INVALID'}** "
            "(schemas are bundled with the kit, Apache-2.0; nothing is downloaded)."
        ),
        *([UL(errors[:10])] if errors else []),
        H(2, "Components"),
        Table(["Type", "Name", "Version", "Hashes"], comps),
        H(2, "Services"),
        Table(["Name", "Description", "Jurisdiction"], svcs),
        P(
            "Component counts for GLM-5.3 are the same model counted three ways (total 753.3B, 744B excluding the MTP "
            "layer, about 40B active).",
            "pk-meta",
        ),
        P(
            "SPDX 3.0 AI export: [ai-bom.spdx3.json](ai-bom.spdx3.json) (optional, not schema-validated by the kit)."
            if spdx
            else "SPDX 3.0 AI export: not requested (`--spdx`).",
            "pk-meta",
        ),
    ]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_pack(
    deployment: Path,
    results: Path | None,
    out: Path,
    *,
    spdx: bool = False,
    write_env: bool = True,
    check: bool = True,
    now: datetime | None = None,
    catalogue: tuple[Item, ...] = CATALOGUE,
    templates: dict[str, str] | None = None,
) -> PackResult:
    now = now or datetime.now(UTC)
    dep = load_deployment(deployment)
    runs, warnings = build_index(results)
    out.mkdir(parents=True, exist_ok=True)
    (out / "assets").mkdir(exist_ok=True)
    for name in ("tokens.css", "components.css", "LICENSE"):
        shutil.copyfile(tokencheck.VENDOR / name, out / "assets" / name)

    ctx = Ctx(dep=dep, runs=runs, generated=dict(GENERATED), bom_valid=False)
    first = {r.id: r for r in resolve_all(ctx, catalogue)}
    residency = first["RES-1"].status if "RES-1" in first else None
    doc = aibom.build_cyclonedx(dep, runs, residency=residency, now=now)
    bom_errors = aibom.validate_cyclonedx(doc)
    ctx.bom_valid = not bom_errors
    items = resolve_all(ctx, catalogue)
    for r in runs:
        r.linked_items = sorted({i.id for i in items for e in i.evidence if e.kind == "run" and e.ref == r.run_id})
    counts = framework_counts(items)

    flow = dataflow.build_flow(dep)
    mmd, svg_file, svg_inline = dataflow.to_mermaid(flow), dataflow.to_svg(flow), dataflow.to_svg(flow, inline=True)

    written: list[Path] = []

    def put(name: str, text: str) -> None:
        p = out / name
        p.write_text(text, encoding="utf-8")
        written.append(p)

    put("dataflow.mmd", mmd)
    put("dataflow.svg", svg_file)
    put("ai-bom.cdx.json", json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    if spdx:
        put("ai-bom.spdx3.json", json.dumps(aibom.build_spdx3(dep, now=now), indent=2) + "\n")
    put(
        "checklist.json",
        json.dumps(
            {
                "disclaimer": DISCLAIMER,
                "deployment": dep.name,
                "frameworks": counts,
                "items": [i.as_dict() for i in items],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )
    shutil.copyfile(deployment, out / "deployment.yaml")
    written.append(out / "deployment.yaml")

    data_files = ["checklist.json", "dataflow.mmd", "dataflow.svg", "ai-bom.cdx.json"] + (
        ["ai-bom.spdx3.json"] if spdx else []
    )
    by_id = {i.id: i for i in items}
    pages: dict[str, tuple[str, list[Any]]] = {
        "index": (
            "Evidence pack overview",
            _blocks_index(
                dep,
                items,
                counts,
                runs,
                [f"{s}.html" for s, _ in PAGES[1:]]
                + [f"{s}.md" for s, _ in PAGES]
                + data_files
                + ["deployment.yaml", "manifest.json"],
            ),
        ),
        "checklist": ("Checklist", _blocks_checklist(items)),
        "data-flow": ("Data-flow diagram", _blocks_flow(dep, svg_inline, mmd)),
        "license": ("License notes", license_blocks(dep, by_id)),
        "evidence-index": ("Evidence index", _blocks_runs(runs, items)),
        "ai-bom": ("AI-BOM", _blocks_bom(doc, bom_errors, spdx)),
    }
    html_files: list[Path] = []
    tmpl = templates or {}
    for slug, (title, blocks) in pages.items():
        for fmt in ("html", "md"):
            text = render(
                fmt, title=title, blocks=blocks, deployment=dep.name, nav=PAGES, current=slug, template=tmpl.get(fmt)
            )
            put(f"{slug}.{fmt}", text)
            if fmt == "html":
                html_files.append(out / f"{slug}.html")

    manifest = {
        "schema_version": 1,
        "disclaimer": DISCLAIMER,
        "kit_version": __version__,
        "deployment": dep.name,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": {p.name: _sha(p) for p in sorted(written, key=lambda p: p.name)},
    }
    put("manifest.json", json.dumps(manifest, indent=2) + "\n")

    res = PackResult(
        out=out, files=written, items=items, deployment=dep, runs=runs, bom_errors=bom_errors, warnings=warnings
    )
    if check:
        res.findings = tokencheck.check_files(html_files)

    if write_env and results is not None:
        run_id = new_run_id("evidence", now)
        env = ResultEnvelope(
            run_id=run_id,
            kit_version=__version__,
            component="evidence",
            started_at=now,
            config={"deployment": dep.name, "path": dep.path, "pack_dir": str(out)},
        )
        env.environment = {"glmkit": __version__}
        for i in items:
            env.items.append(
                ItemResult(
                    id=i.id,
                    group=i.framework,
                    title=i.title,
                    kind="checklist_item",
                    category=i.framework,
                    severity="major" if (i.id == "RES-1" and i.status == "gap") else "info",
                    status=STATUS_TO_ITEM[i.status],
                    reason=i.reason,  # type: ignore[arg-type]
                    source={"urls": [c["url"] for c in i.citations if c.get("url", "").startswith("http")]},
                    data={
                        "checklist_status": i.status,
                        "prompts": i.prompts,
                        "citations": i.citations,
                        "in_force": i.in_force,
                        "flags": i.flags,
                        "interpretive": i.interpretive,
                        "attested": i.attested,
                        "evidence": [e.as_dict() for e in i.evidence],
                    },
                )
            )
        env.data = {
            "disclaimer": DISCLAIMER,
            "deployment": dep.model_dump(mode="json", exclude={"attestations"}),
            "path_title": PATH_TITLES[dep.path],
            "frameworks": counts,
            "license": RECORDED_AS,
            "evidence_index": [r.as_dict() for r in runs],
            "bom_valid": not bom_errors,
            "pack_dir": str(out),
            "files": sorted(p.name for p in written),
        }
        env.finalize()
        results.mkdir(parents=True, exist_ok=True)
        res.envelope_path = write_envelope(env, results if results.is_dir() else results.parent)
        res.run_id = run_id
    return res


def example_descriptors() -> list[Path]:
    return sorted((Path(__file__).resolve().parent / "examples").glob("*.yaml"))


def dump_example(name: str) -> str:
    for p in example_descriptors():
        if p.stem == name:
            return p.read_text(encoding="utf-8")
    raise KeyError(name)
