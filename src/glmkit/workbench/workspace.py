# ruff: noqa: E501
"""Engagement workspace: scaffold (`glmkit init`) and state derived from the files and results present.

State is never a hand-edited flag: a step is done when the evidence for it is on disk (a filled brief, a profile
file, a golden set, a result envelope of the right component, a built site).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from glmkit import __version__
from glmkit.contracts.envelope import ResultEnvelope

MARKER = "engagement.yaml"
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
BRIEF_SECTIONS = ("Use cases", "Success metrics", "Constraints", "Data residency")


class WorkspaceError(ValueError):
    pass


BRIEF = """\
# Engagement brief: {customer}

Fill in each section. The Engagement tab shows this brief and counts the step as done when no `TODO` is left.

## Use cases
TODO: the 2-5 workloads that would move to GLM (for example "support ticket triage", "contract review").
For each: today's model, monthly volume, and who owns the quality bar.

## Success metrics
TODO: what "good enough" means in numbers. Example: "within 5 points of the incumbent on intent accuracy,
p95 latency under 4 s, cost per 1,000 tickets at or below today's."

## Constraints
TODO: hosting (API / plan / self-hosted), approved vendors, tool and framework stack (OpenAI-compatible SDK,
Claude Code, LiteLLM ...), change-freeze dates, who signs off.

## Data residency
TODO: where prompts and outputs may be processed and stored, retention rules, PII in prompts, regulators involved.
"""

GOLDEN_TEMPLATE = """\
# Golden-set template. Copy to golden/golden-set.yaml and replace the items with your own (50-100 per task type is
# a good start; see docs/contracts.md, "Golden set format"). Graders: exact, regex, numeric, json_schema, sql,
# sql_db, span, tool_use, judge (`glmkit eval graders`).
schema_version: 1
name: {slug}-golden
description: Golden set for {customer}. One task type per use case in engagement brief.
k: 1
params: {{max_tokens: 800}}
graders:
  classify: {{type: exact, ignore_case: true, strip_punct: true}}      # one label out, compare to expected
  extract: {{type: json_schema, schema: {{type: object, required: [name]}}, match_expected: true}}
  summarize: {{type: judge, rubric: "Prefer the summary that keeps every fact in the reference and adds none."}}
items:
  - {{id: classify-001, task_type: classify, prompt: "TODO: a real customer input", expected: TODO_label}}
  - {{id: extract-001, task_type: extract, prompt: "TODO: text to extract from", expected: {{name: TODO}}}}
  - {{id: summarize-001, task_type: summarize, prompt: "TODO: a long input", expected: "TODO: the reference summary"}}
"""

PROFILE_INCUMBENT = """\
# Copy to profiles/incumbent.yaml and fill in. Keys come from the environment variable named below, never from
# this file.
name: incumbent
title: TODO the customer's current model
provider: TODO
base_url: TODO https://api.example.com/v1
protocol: openai_chat
endpoint_class: api
model: TODO
api_key_env: TODO_INCUMBENT_API_KEY
"""

PROFILE_GLM = """\
# Copy to profiles/glm-candidate.yaml. Start from a shipped profile: `glmkit profiles list`, `glmkit profiles show zai-api`.
name: glm-candidate
title: GLM candidate on the host under evaluation
provider: zai
base_url: https://api.z.ai/api/paas/v4
protocol: openai_chat
endpoint_class: api
model: glm-5.3
api_key_env: ZAI_API_KEY
"""

MAKEFILE = """\
# Engagement task list. Run `make help`. Every target calls a real glmkit command; edit the variables below.
GLMKIT   ?= glmkit
CONFORM_TARGET ?= mock
BASELINE ?= incumbent
TARGETS  ?= glm-candidate,glm-candidate@high
JUDGE    ?=
SET      ?= golden/golden-set.yaml
TRACES   ?= traces/traces.jsonl
DEPLOYMENT ?= deployments/zai-api.yaml

.PHONY: help status conform eval cost evidence report demo all
help:
\t@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\\t/'
status:  ## which steps are done (derived from files and results)
\t$(GLMKIT) status
conform: ## week 1: does GLM work on this host and harness
\t$(GLMKIT) conform run --target $(CONFORM_TARGET) --out results
eval:    ## week 1: should this workload move, at which effort, at what cost
\t$(GLMKIT) eval run --set $(SET) --baseline $(BASELINE) --targets $(TARGETS) $(if $(JUDGE),--judge $(JUDGE)) --profiles-dir profiles --out results
cost:    ## cache and cost forensics over your imported traces (glmkit traces import ...)
\t$(GLMKIT) cost analyze $(TRACES) --out results
evidence: ## compliance evidence pack for the chosen deployment path
\t@test -f $(DEPLOYMENT) || $(GLMKIT) evidence examples --out deployments
\t$(GLMKIT) evidence build --deployment $(DEPLOYMENT) --results results --out evidence
report:  ## static dashboard from everything in results/
\t$(GLMKIT) report build results --out site
demo:    ## everything above on the mock with stand-in or fetched demo sets, offline
\t$(GLMKIT) demo run
all: conform eval cost evidence report
"""

README = """\
# {customer}: GLM migration engagement

Workspace created {created} by glmkit {version}. Everything here is plain files; `glmkit status` shows which steps
are done by looking at them, and the dashboard's Engagement tab shows the same.

## Try the whole path in one command (offline, on the mock)

    glmkit demo run          # conformance, eval on four demo task types, cost, evidence, dashboard
    open site/index.html

The first run uses small synthetic stand-in items and says so. `glmkit demo fetch all` downloads the licensed
datasets (checksum-verified, see `golden/` cards) and the next run uses them.

## The 90-day path

| When | Goal | Steps (make target) |
|---|---|---|
| Week 0 | Agree scope | Fill `brief.md` (use cases, success metrics, constraints, data residency). |
| Week 1 | Does it work, is it worth moving | Copy the profiles in `profiles/` and fill them in. `make conform` (does GLM work on this host and harness), build a golden set from real traffic (`glmkit traces import`, or `golden/golden-set.template.yaml`), `make eval`, `make cost`. Read the Migration and Cost tabs. |
| Weeks 2-4 | Pilot | Route one workload through `glmkit bridge serve`, keep the spend and loop guard on, rerun `make conform eval` weekly and compare runs in the dashboard. |
| Weeks 5-8 | Decide hosting | `glmkit deploy size` for self-hosting, `glmkit conform host` against the real host, `make evidence` with the deployment descriptor. |
| Weeks 9-12 | Roll out | Move traffic in steps by task type (only the task types the eval calls non-inferior), keep `make report` as the weekly status, hand over `evidence/` and the dashboard. |

## Folders

- `brief.md`: the engagement brief. `engagement.yaml`: customer and dates.
- `profiles/`: target profiles (`*.example` files are templates; `glmkit ... --profiles-dir profiles` loads `*.yaml`).
- `golden/`: golden sets and their dataset cards. `traces/`: imported production traces.
- `results/`: one folder per run (`result.json`). `site/`: the dashboard. `evidence/`: the evidence pack.

No secrets in this folder: profiles name environment variables, never keys.
"""

GITIGNORE = "results/\nsite/\nevidence/\ngolden/raw/\n*.part\n.env\n"


def slugify(customer: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", customer.strip()).strip("-").lower()


def init_workspace(customer: str, parent: Path, *, force: bool = False) -> Path:
    if not customer.strip():
        raise WorkspaceError("customer name is empty")
    slug = slugify(customer)
    if not SLUG.match(slug):
        raise WorkspaceError(f"cannot make a folder name from {customer!r}")
    root = parent / slug
    if root.exists() and any(root.iterdir()) and not force:
        raise WorkspaceError(f"{root} exists and is not empty (use --force to add missing files)")
    created = date.today().isoformat()
    files = {
        "engagement.yaml": yaml.safe_dump(
            {"customer": customer.strip(), "slug": slug, "created": created, "glmkit_version": __version__}
        ),
        "brief.md": BRIEF.format(customer=customer.strip()),
        "README.md": README.format(customer=customer.strip(), created=created, version=__version__),
        "Makefile": MAKEFILE,
        ".gitignore": GITIGNORE,
        "profiles/incumbent.yaml.example": PROFILE_INCUMBENT,
        "profiles/glm-candidate.yaml.example": PROFILE_GLM,
        "golden/golden-set.template.yaml": GOLDEN_TEMPLATE.format(slug=slug, customer=customer.strip()),
        "traces/.gitkeep": "",
    }
    for rel, content in files.items():
        p = root / rel
        if p.exists() and force:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def find_workspace(start: Path) -> Path | None:
    for d in [start.resolve(), *start.resolve().parents]:
        if (d / MARKER).is_file():
            return d
    return None


# ------------------------------------------------------------------------------------------------ state


@dataclass
class Step:
    id: str
    title: str
    week: str
    command: str
    tab: str | None  # dashboard tab that shows the result
    done: bool = False
    evidence: str = "nothing yet"
    run_id: str | None = None
    verdict: str | None = None
    detail: str = ""


@dataclass
class State:
    root: Path
    customer: str
    created: str
    steps: list[Step] = field(default_factory=list)
    brief: dict[str, str] = field(default_factory=dict)
    brief_todos: int = 0

    @property
    def done(self) -> int:
        return sum(s.done for s in self.steps)


def read_brief(path: Path) -> tuple[dict[str, str], int]:
    """Sections of brief.md by heading and the number of TODO lines left."""
    if not path.is_file():
        return {}, 0
    text = path.read_text(encoding="utf-8")
    sections: dict[str, list[str]] = {}
    cur: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            sections[cur] = []
        elif cur is not None:
            sections[cur].append(line)
    out = {k: "\n".join(v).strip() for k, v in sections.items()}
    return out, sum(1 for line in text.splitlines() if "TODO" in line)


def _latest(envs: list[ResultEnvelope], component: str) -> ResultEnvelope | None:
    mine = [e for e in envs if e.component == component]
    return max(mine, key=lambda e: e.started_at) if mine else None


def derive_state(root: Path, envelopes: list[ResultEnvelope], *, site_built: bool | None = None) -> State:
    meta: dict[str, Any] = yaml.safe_load((root / MARKER).read_text(encoding="utf-8")) or {}
    brief, todos = read_brief(root / "brief.md")
    state = State(
        root, str(meta.get("customer", root.name)), str(meta.get("created", "")), brief=brief, brief_todos=todos
    )
    st = state.steps

    s = Step("brief", "Engagement brief filled in", "Week 0", "edit brief.md", None)
    if (root / "brief.md").is_file() and todos == 0 and brief:
        s.done, s.evidence = True, "brief.md has no TODO left"
    else:
        s.evidence = f"brief.md has {todos} TODO line(s)" if (root / "brief.md").is_file() else "brief.md is missing"
    st.append(s)

    profs = sorted(p.name for p in (root / "profiles").glob("*.yaml")) if (root / "profiles").is_dir() else []
    s = Step("profiles", "Target profiles added", "Week 1", "cp profiles/*.example and fill in", "hosts")
    if profs:
        s.done, s.evidence = True, ", ".join(profs[:4]) + (f" (+{len(profs) - 4})" if len(profs) > 4 else "")
    st.append(s)

    sets = (
        [p for p in sorted((root / "golden").glob("*.yaml")) if ".template." not in p.name]
        if (root / "golden").is_dir()
        else []
    )
    s = Step(
        "golden", "Golden set ready", "Week 1", "glmkit demo fetch all  |  copy golden-set.template.yaml", "migration"
    )
    if sets:
        s.done, s.evidence = (
            True,
            ", ".join(p.name for p in sets[:4]) + (f" (+{len(sets) - 4})" if len(sets) > 4 else ""),
        )
    else:
        run = _latest(envelopes, "eval")
        if run is not None and (run.data.get("set") or {}).get("name"):
            s.done, s.evidence = True, f"set {run.data['set']['name']} (from run {run.run_id})"
    st.append(s)

    for sid, title, week, cmd, tab, comp in (
        ("conform", "Conformance run", "Week 1", "make conform", "conformance", "conform"),
        ("eval", "Migration eval", "Week 1", "make eval", "migration", "eval"),
        ("cost", "Cost and cache forensics", "Week 1", "make cost", "cost", "cost"),
        ("evidence", "Evidence pack", "Weeks 5-8", "make evidence", "evidence", "evidence"),
    ):
        step = Step(sid, title, week, cmd, tab)
        env = _latest(envelopes, comp)
        if env is not None:
            step.done, step.run_id, step.verdict = True, env.run_id, env.verdict
            step.evidence = f"run {env.run_id}"
            step.detail = f"{sum(1 for e in envelopes if e.component == comp)} run(s)"
        st.append(step)

    built = (root / "site" / "index.html").is_file() if site_built is None else site_built
    s = Step("report", "Dashboard built", "Weekly", "make report", "runs")
    if built:
        s.done, s.evidence = True, "site/index.html"
    st.append(s)
    return state
