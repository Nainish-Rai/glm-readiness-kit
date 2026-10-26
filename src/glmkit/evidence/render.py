# ruff: noqa: E501
"""Render layer: a page is a list of blocks, written as HTML and Markdown through two templates.

Both templates carry ``{{DISCLAIMER}}``. ``render`` refuses to return a page that does not contain the disclaimer
text, so removing the placeholder from a template (or a block that overwrites it) fails the build, not just a test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from glmkit import __version__
from glmkit.evidence.disclaimer import DISCLAIMER
from glmkit.report import tokencheck

TEMPLATES = Path(__file__).resolve().parent / "templates"
STATUS_TONE = {"met": "ok", "gap": "err", "needs-counsel": "warn", "n/a": ""}


class MissingDisclaimer(RuntimeError):
    pass


# ---- blocks -----------------------------------------------------------------------------------------------
@dataclass
class H:
    level: int
    text: str
    anchor: str | None = None


@dataclass
class P:
    text: str
    cls: str | None = None


@dataclass
class UL:
    items: list[str]


@dataclass
class Table:
    head: list[str]
    rows: list[list[str]]


@dataclass
class Code:
    text: str
    lang: str = ""


@dataclass
class Figure:
    html: str
    md: str


@dataclass
class Card:
    """A checklist item card: heading line, then blocks."""

    anchor: str
    head: str
    blocks: list[Any]


_INLINE = re.compile(
    r"\{anchor:([\w.:-]+)\}|\{status:([a-z/-]+)\}|\{bar:(\d+),(\d+),(\d+),(\d+)\}|\[([^\]]+)\]\(([^)\s]+)\)|`([^`]+)`"
)


def inline(text: str, fmt: str) -> str:
    """Tiny inline markup: ``[t](u)`` links, `` `code` ``, ``{status:met}`` pills. HTML escapes everything else."""
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        out.append(escape(text[pos : m.start()]) if fmt == "html" else text[pos : m.start()])
        anchor, status, bm, bg, bn, bx, label, url, code = m.groups()
        if anchor:
            out.append(f'<a id="{escape(anchor)}"></a>' if fmt == "html" else "")
        elif bm is not None:
            n = [int(bm), int(bg), int(bn), int(bx)]
            tot = sum(n) or 1
            cls = ("s-met", "s-gap", "s-needs", "s-na")
            segs = "".join(
                f'<span class="{c}" style="width:{100 * v / tot:.1f}%"></span>'
                for c, v in zip(cls, n, strict=True)
                if v
            )
            out.append(
                f'<span class="pk-bar" role="img" aria-label="{n[0]} met, {n[1]} gap, {n[2]} needs-counsel, {n[3]} n/a">{segs}</span>'
                if fmt == "html"
                else ""
            )
        elif status:
            tone = STATUS_TONE.get(status, "")
            out.append(f'<span class="pill {tone}">{escape(status)}</span>' if fmt == "html" else f"**{status}**")
        elif label:
            out.append(
                f'<a href="{escape(url, quote=True)}">{escape(label)}</a>' if fmt == "html" else f"[{label}]({url})"
            )
        else:
            out.append(f"<code>{escape(code)}</code>" if fmt == "html" else f"`{code}`")
        pos = m.end()
    out.append(escape(text[pos:]) if fmt == "html" else text[pos:])
    return "".join(out)


def _html(blocks: list[Any]) -> str:
    out: list[str] = []
    for b in blocks:
        if isinstance(b, H):
            a = f' id="{escape(b.anchor)}"' if b.anchor else ""
            out.append(f"<h{b.level}{a}>{inline(b.text, 'html')}</h{b.level}>")
        elif isinstance(b, P):
            c = f' class="{b.cls}"' if b.cls else ""
            out.append(f"<p{c}>{inline(b.text, 'html')}</p>")
        elif isinstance(b, UL):
            out.append("<ul>" + "".join(f"<li>{inline(i, 'html')}</li>" for i in b.items) + "</ul>")
        elif isinstance(b, Table):
            head = "".join(f'<th scope="col">{inline(h, "html")}</th>' for h in b.head)
            rows = "".join("<tr>" + "".join(f"<td>{inline(c, 'html')}</td>" for c in r) + "</tr>" for r in b.rows)
            out.append(
                f'<div class="pk-tbl-wrap"><table class="pk-tbl"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>'
            )
        elif isinstance(b, Code):
            out.append(f'<pre class="pk-code">{escape(b.text)}</pre>')
        elif isinstance(b, Figure):
            out.append(f'<figure class="pk-fig" style="margin:0">{b.html}</figure>')
        elif isinstance(b, Card):
            out.append(
                f'<section class="pk-item" id="{escape(b.anchor)}"><h3>{inline(b.head, "html")}</h3>{_html(b.blocks)}</section>'
            )
    return "\n".join(out)


def _md(blocks: list[Any]) -> str:
    out: list[str] = []
    for b in blocks:
        if isinstance(b, H):
            out.append("#" * b.level + " " + inline(b.text, "md"))
        elif isinstance(b, P):
            out.append(inline(b.text, "md"))
        elif isinstance(b, UL):
            out.append("\n".join(f"- {inline(i, 'md')}" for i in b.items))
        elif isinstance(b, Table):
            esc = lambda s: inline(s, "md").replace("|", "\\|").replace("\n", " ")  # noqa: E731
            out.append(
                "| "
                + " | ".join(esc(h) for h in b.head)
                + " |\n|"
                + "---|" * len(b.head)
                + "\n"
                + "\n".join("| " + " | ".join(esc(c) for c in r) + " |" for r in b.rows)
            )
        elif isinstance(b, Code):
            out.append(f"```{b.lang}\n{b.text.rstrip()}\n```")
        elif isinstance(b, Figure):
            out.append(b.md)
        elif isinstance(b, Card):
            out.append(f"### {inline(b.head, 'md')}\n\n{_md(b.blocks)}")
    return "\n\n".join(out)


def read_template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def _style() -> str:
    css = read_template("pack.css")
    contract, missing = tokencheck.contract_block(css)
    if missing:
        raise RuntimeError(f"pack.css reads tokens that tokens.css does not define: {', '.join(missing)}")
    return contract + "\n" + css


def render(
    fmt: str,
    *,
    title: str,
    blocks: list[Any],
    deployment: str,
    nav: list[tuple[str, str]],
    current: str,
    template: str | None = None,
) -> str:
    """One page as ``html`` or ``md``. Raises ``MissingDisclaimer`` if the result lacks the disclaimer."""
    tmpl = template if template is not None else read_template(f"page.{fmt}.tmpl")
    ext = ".html" if fmt == "html" else ".md"
    if fmt == "html":
        links = " ".join(
            f'<a href="{escape(slug)}{ext}"'
            + (' aria-current="page"' if slug == current else "")
            + f">{escape(label)}</a>"
            for slug, label in nav
        )
        body = _html(blocks)
    else:
        links = " · ".join(f"[{label}]({slug}{ext})" for slug, label in nav)
        body = _md(blocks)
    page = tmpl
    for key, value in {
        "TITLE": escape(title) if fmt == "html" else title,
        "DEPLOYMENT": escape(deployment) if fmt == "html" else deployment,
        "NAV": links,
        "STYLE": _style() if fmt == "html" else "",
        "VERSION": __version__,
        "DISCLAIMER": DISCLAIMER,
        "BODY": body,
    }.items():
        page = page.replace("{{" + key + "}}", value)
    if DISCLAIMER not in page:
        raise MissingDisclaimer(f"page {title!r} ({fmt}) has no disclaimer; every generated page must carry it")
    return page
