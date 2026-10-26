"""Data-flow diagram from the deployment descriptor: Mermaid source and a built-in SVG renderer.

The Mermaid CLI needs a headless browser download (network), so the pack ships the Mermaid source plus this
small renderer for the fixed shape: customer environment -> host (with logs) -> sub-processors, drawn in three
columns with trust boundaries. The SVG uses ``currentColor`` only, so it works standalone and in both themes.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from html import escape

from glmkit.evidence.descriptor import Deployment
from glmkit.evidence.disclaimer import DISCLAIMER

NODE_W, NODE_H, COL_GAP, ROW_GAP, PAD = 240, 66, 210, 40, 18


@dataclass
class Node:
    id: str
    label: str
    group: str
    col: int
    row: int
    flag: str | None = None  # e.g. "outside EEA"; drawn heavier and shown in the label


@dataclass
class Edge:
    a: str
    b: str
    label: str
    dashed: bool = False
    offset: int = 0


@dataclass
class Flow:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    groups: dict[str, str] = field(default_factory=dict)  # group id -> title


def build_flow(dep: Deployment) -> Flow:
    h = dep.hosting
    f = Flow()
    f.groups["customer"] = "Customer environment"
    f.nodes.append(Node("clients", "Customer applications: " + ", ".join(dep.data_flow.clients), "customer", 0, 0))
    if dep.path == "bedrock":
        where = f"{h.provider} · {h.bedrock_profile} profile · processing Region not pinned"
    else:
        where = f"{h.provider} · {h.region or 'region not stated'} [{h.jurisdiction}]"
    f.groups["host"] = where
    engine = dep.serving.engine or h.endpoint or "endpoint"
    f.nodes.append(Node("endpoint", f"{dep.model.name} inference ({engine})", "host", 1, 0))
    pd = ", ".join(dep.personal_data.categories) or "no personal data declared"
    f.edges.append(Edge("clients", "endpoint", f"prompts · {pd}"))
    f.edges.append(Edge("endpoint", "clients", "completions", dashed=True, offset=18))
    ret = dep.retention
    retention = f"logs {ret.logs_days} d" if ret.logs_days is not None else "retention not logged"
    f.nodes.append(Node("logs", f"Logs and records ({retention})", "host", 1, 1))
    f.edges.append(Edge("endpoint", "logs", "request logs"))
    if dep.sub_processors or dep.path != "self-hosted":
        f.groups["subp"] = "Sub-processors and upstream parties"
    row = 0
    for i, s in enumerate(dep.sub_processors):
        flag = "outside EEA" if s.outside_eea else ("location unknown" if s.outside_eea is None else None)
        loc = f" · {s.location}" if s.location else ""
        f.nodes.append(Node(f"sp{i}", f"{s.name}{loc}" + (f" [{flag}]" if flag else ""), "subp", 2, row, flag))
        f.edges.append(Edge("endpoint", f"sp{i}", s.role or "processing"))
        row += 1
    t = dep.data_flow.telemetry_to_zai
    if dep.path != "zai-api" and t is not False:
        f.groups["subp"] = "Sub-processors and upstream parties"
        state = "unknown" if t is None else "declared"
        f.nodes.append(Node("zai", f"Z.ai: telemetry or fees [{state}]", "subp", 2, row, "unverified"))
        f.edges.append(Edge("endpoint", "zai", f"data/fees? {state}", dashed=True))
    return f


def _m(s: str) -> str:
    return s.replace('"', "'").replace("[", "(").replace("]", ")")


def to_mermaid(f: Flow) -> str:
    out = [f"%% {DISCLAIMER}", "flowchart LR"]
    for gid, title in f.groups.items():
        out.append(f'  subgraph {gid}["{_m(title)}"]')
        for n in f.nodes:
            if n.group == gid:
                out.append(f'    {n.id}["{_m(n.label)}"]')
        out.append("  end")
    for e in f.edges:
        arrow = "-.->" if e.dashed else "-->"
        out.append(f'  {e.a} {arrow}|"{_m(e.label)}"| {e.b}')
    return "\n".join(out) + "\n"


def _lines(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width) or [""]


def to_svg(f: Flow, *, inline: bool = False) -> str:
    cols = max((n.col for n in f.nodes), default=0) + 1
    rows = max((n.row for n in f.nodes), default=0) + 1
    ox, oy = 28, 56
    pos = {n.id: (ox + n.col * (NODE_W + COL_GAP), oy + n.row * (NODE_H + ROW_GAP)) for n in f.nodes}
    width = ox * 2 + cols * NODE_W + (cols - 1) * COL_GAP
    height = oy + rows * (NODE_H + ROW_GAP) + 40
    p: list[str] = []
    # trust boundaries
    for gid, title in f.groups.items():
        ns = [n for n in f.nodes if n.group == gid]
        if not ns:
            continue
        x0 = min(pos[n.id][0] for n in ns) - PAD
        y0 = min(pos[n.id][1] for n in ns) - PAD - 14
        x1 = max(pos[n.id][0] for n in ns) + NODE_W + PAD
        y1 = max(pos[n.id][1] for n in ns) + NODE_H + PAD
        p.append(
            f'<rect class="dfd-bound" x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="8" fill="none" '
            'stroke="currentColor" stroke-opacity="0.45" stroke-dasharray="6 4"/>'
        )
        p.append(
            f'<text class="dfd-cap" x="{x0 + 8}" y="{y0 + 14}" font-size="11" fill="currentColor" fill-opacity="0.75">'
            f"{escape(textwrap.shorten(title, 64, placeholder='...'))}</text>"
        )
    # edges
    for e in f.edges:
        ax, ay = pos[e.a]
        bx, by = pos[e.b]
        if ax != bx:
            sx, sy = (ax + NODE_W, ay + NODE_H // 2) if bx > ax else (ax, ay + NODE_H // 2)
            ex, ey = (bx, by + NODE_H // 2) if bx > ax else (bx + NODE_W, by + NODE_H // 2)
            sy += e.offset
            ey += e.offset
            lx, ly = (sx + ex) // 2, (sy + ey) // 2 - 6
        else:
            sx = ex = ax + NODE_W // 2 + e.offset
            sy, ey = (ay + NODE_H, by) if by > ay else (ay, by + NODE_H)
            lx, ly = sx + 8, (sy + ey) // 2 + 4
        dash = ' stroke-dasharray="5 4"' if e.dashed else ""
        p.append(
            f'<line class="dfd-edge" x1="{sx}" y1="{sy}" x2="{ex}" y2="{ey}" stroke="currentColor"{dash} '
            'marker-end="url(#dfd-arrow)"/>'
        )
        anchor = "middle" if ax != bx else "start"
        label = escape(textwrap.shorten(e.label, 30, placeholder="..."))
        p.append(
            f'<text class="dfd-lab" x="{lx}" y="{ly}" text-anchor="{anchor}" font-size="10.5" fill="currentColor" '
            f'fill-opacity="0.8">{label}</text>'
        )
    # nodes
    for n in f.nodes:
        x, y = pos[n.id]
        sw = "2.5" if n.flag else "1.2"
        p.append(
            f'<rect class="dfd-node" x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="6" fill="none" '
            f'stroke="currentColor" stroke-width="{sw}"/>'
        )
        lines = _lines(n.label, 32)[:3]
        top = y + NODE_H / 2 - (len(lines) - 1) * 7 + 4
        for i, ln in enumerate(lines):
            p.append(
                f'<text x="{x + NODE_W / 2}" y="{top + i * 14}" text-anchor="middle" font-size="12" '
                f'fill="currentColor">{escape(ln)}</text>'
            )
    foot = height - 14
    body = "\n".join(p)
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        'role="img" aria-labelledby="dfd-t dfd-d" font-family="system-ui, sans-serif">'
        if not inline
        else f'<svg class="dfd" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        'aria-labelledby="dfd-t dfd-d">'
    )
    return (
        f'{head}\n<title id="dfd-t">Data-flow diagram</title>\n<desc id="dfd-d">{escape(DISCLAIMER)}</desc>\n'
        '<defs><marker id="dfd-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="currentColor"/></marker></defs>\n'
        f'{body}\n<text x="{ox}" y="{foot}" font-size="10.5" fill="currentColor" fill-opacity="0.7">'
        "Checklist and evidence index, not legal advice. Dashed edges are returns or unverified flows.</text>\n</svg>\n"
    )
