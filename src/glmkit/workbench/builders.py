# ruff: noqa: E501
"""Turn the pinned raw files of each demo dataset into a golden set (``glmkit.eval.goldenset.GoldenSet``).

Builders are pure functions of parsed data plus a seed, so tests run them on 5-item fixtures. The sampling rule of
each set is stated on its card. Nothing here downloads anything (see ``fetch.py``).
"""

from __future__ import annotations

import csv
import io
import json
import random
import sqlite3
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _set(name: str, description: str, graders: dict[str, Any], items: list[dict[str, Any]], **params: Any) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "description": description,
        "k": 1,
        "params": {"max_tokens": 1024, **params},
        "graders": graders,
        "items": items,
    }


def _meta(source: str, license_spdx: str, **extra: Any) -> dict[str, Any]:
    return {"source": source, "license": license_spdx, **extra}


# ---------------------------------------------------------------------------------------------- Banking77

BANKING_SYSTEM = (
    "You are a banking customer-support intent classifier. Reply with exactly one label from this list and "
    "nothing else:\n{labels}"
)


def build_banking(rows: list[dict[str, str]], n: int = 80, seed: int = 77) -> dict:
    """rows: [{text, category}]. Stratified round-robin over labels, shuffled with ``seed``."""
    rng = random.Random(seed)
    by_label: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_label[r["category"]].append(r)
    labels = sorted(by_label)
    for v in by_label.values():
        v.sort(key=lambda r: r["text"])
        rng.shuffle(v)
    order = labels[:]
    rng.shuffle(order)
    picked: list[dict[str, str]] = []
    depth = 0
    while len(picked) < n and any(len(by_label[x]) > depth for x in order):
        for lab in order:
            if len(by_label[lab]) > depth and len(picked) < n:
                picked.append(by_label[lab][depth])
        depth += 1
    system = BANKING_SYSTEM.format(labels=", ".join(labels))
    items = [
        {
            "id": f"banking77-{i:03d}",
            "task_type": "classify",
            "system": system,
            "prompt": r["text"],
            "expected": r["category"],
            "meta": _meta("banking77", "CC-BY-4.0"),
        }
        for i, r in enumerate(picked)
    ]
    return _set(
        "banking77",
        "Banking77 (PolyAI, CC BY 4.0) intent classification, sampled test items. See the dataset card.",
        {"classify": {"type": "exact", "ignore_case": True, "strip_punct": True}},
        items,
        max_tokens=800,
    )


def build_banking_from_files(raw: Path, n: int = 80) -> dict:
    text = (raw / "banking_data" / "test.csv").read_text(encoding="utf-8")
    return build_banking(list(csv.DictReader(io.StringIO(text))), n)


# ---------------------------------------------------------------------------------------------- CUAD

CUAD_SPAN_SYSTEM = (
    "You are a contract-review assistant. Quote, verbatim from the excerpt, the sentence(s) that make up the "
    "requested clause. Output only the quote. If the excerpt has no such clause, output: none"
)
CUAD_EXPLAIN_SYSTEM = "You are a contract-review assistant. Answer in one or two plain-English sentences."
WINDOW = 2400


def _window(context: str, start: int, length: int, rng: random.Random) -> str:
    room = max(0, WINDOW - length)
    before = rng.randrange(0, room + 1) if room else 0
    lo = max(0, start - before)
    hi = min(len(context), lo + WINDOW)
    lo = max(0, min(lo, start))
    hi = max(hi, start + length)
    # snap to whitespace so the excerpt does not start or end mid-word (the answer itself stays whole)
    while lo > 0 and lo < start and not context[lo - 1].isspace():
        lo += 1
    while hi < len(context) and hi > start + length and not context[hi].isspace():
        hi -= 1
    return context[lo:hi].strip()


def build_cuad(squad: dict, n_span: int = 40, n_explain: int = 20, seed: int = 5) -> dict:
    """squad: parsed CUADv1.json (SQuAD layout). One qa per contract; the first n_span become span items."""
    rng = random.Random(seed)
    contracts = sorted(squad["data"], key=lambda d: d["title"])
    rng.shuffle(contracts)
    chosen: list[dict[str, Any]] = []
    for doc in contracts:
        para = doc["paragraphs"][0]
        cands = []
        for qa in para["qas"]:
            if qa.get("is_impossible") or not qa.get("answers"):
                continue
            a = qa["answers"][0]
            if (
                40 <= len(a["text"]) <= 500
                and para["context"][a["answer_start"] : a["answer_start"] + len(a["text"])] == a["text"]
            ):
                cands.append((qa, a))
        if not cands:
            continue
        qa, a = cands[rng.randrange(len(cands))]
        category = qa["id"].split("__", 1)[1] if "__" in qa["id"] else qa["id"]
        excerpt = _window(para["context"], a["answer_start"], len(a["text"]), rng)
        chosen.append({"title": doc["title"], "category": category, "answer": a["text"], "excerpt": excerpt})
        if len(chosen) >= n_span + n_explain:
            break
    items: list[dict[str, Any]] = []
    for i, c in enumerate(chosen):
        body = f'Clause type: {c["category"]}\n\nContract excerpt:\n"""\n{c["excerpt"]}\n"""'
        if i < n_span:
            items.append(
                {
                    "id": f"cuad-span-{i:03d}",
                    "task_type": "clause_span",
                    "cluster": c["title"],
                    "system": CUAD_SPAN_SYSTEM,
                    "prompt": body,
                    "expected": c["answer"],
                    "meta": _meta("cuad", "CC-BY-4.0", category=c["category"]),
                }
            )
        else:
            items.append(
                {
                    "id": f"cuad-explain-{i - n_span:03d}",
                    "task_type": "clause_explain",
                    "cluster": c["title"],
                    "system": CUAD_EXPLAIN_SYSTEM,
                    "prompt": body + f"\n\nExplain what the {c['category']} clause in this excerpt requires or allows.",
                    "expected": f"The excerpt says: {c['answer']}",
                    "meta": _meta("cuad", "CC-BY-4.0", category=c["category"]),
                }
            )
    return _set(
        "cuad-slice",
        "CUAD v1 (The Atticus Project, CC BY 4.0) clause slice: span extraction (token F1) and judged explanation.",
        {
            "clause_span": {"type": "span", "min_f1": 0.5},
            "clause_explain": {
                "type": "judge",
                "rubric": "Prefer the explanation that is more accurate to the clause quoted in the reference "
                "and clearer for a non-lawyer.",
            },
        },
        items,
    )


def build_cuad_from_files(raw: Path, n_span: int = 40, n_explain: int = 20) -> dict:
    with zipfile.ZipFile(raw / "data.zip") as z:
        squad = json.loads(z.read("CUADv1.json"))
    return build_cuad(squad, n_span, n_explain)


# ---------------------------------------------------------------------------------------------- text-to-SQL

SQL_SYSTEM = (
    "You write SQLite queries. Reply with exactly one SQL query and nothing else (no explanation, no code fence)."
)


def _sqlite_rows(context: str, sql: str) -> list[tuple[Any, ...]] | None:
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(context)
        return con.execute(sql.strip().rstrip(";")).fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()


def build_text2sql(rows: list[dict[str, Any]], n: int = 60, seed: int = 11) -> dict:
    """rows: gretel rows {sql_prompt, sql_context, sql, domain, sql_complexity}. Keeps rows that run in SQLite."""
    rng = random.Random(seed)
    pool = sorted(rows, key=lambda r: (str(r.get("id", "")), r["sql_prompt"]))
    rng.shuffle(pool)
    items: list[dict[str, Any]] = []
    for r in pool:
        if len(items) >= n:
            break
        if not r.get("sql_context") or not r.get("sql"):
            continue
        got = _sqlite_rows(r["sql_context"], r["sql"])
        if not got:
            continue
        i = len(items)
        items.append(
            {
                "id": f"text2sql-{i:03d}",
                "task_type": "text_to_sql",
                "system": SQL_SYSTEM,
                "prompt": f"{r['sql_context'].strip()}\n\nQuestion: {r['sql_prompt']}",
                "expected": r["sql"].strip(),
                "grader": {"type": "sql", "setup": [r["sql_context"]], "ordered": False},
                "meta": _meta("text2sql", "Apache-2.0", domain=r.get("domain"), complexity=r.get("sql_complexity")),
            }
        )
    return _set(
        "text2sql",
        "gretelai/synthetic_text_to_sql (Apache-2.0) test items; graded by executing both queries in SQLite.",
        {},
        items,
        max_tokens=800,
    )


def read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as e:  # pragma: no cover - depends on the optional extra
        raise RuntimeError("reading the text-to-SQL parquet needs pyarrow: pip install 'glmkit[datasets]'") from e
    return pq.read_table(path).to_pylist()


def build_text2sql_from_files(raw: Path, n: int = 60) -> dict:
    return build_text2sql(read_parquet_rows(raw / "synthetic_text_to_sql_test.snappy.parquet"), n)


# ---------------------------------------------------------------------------------------------- tau2 retail

TAU_LOOKUPS = {"find_user_id_by_name_zip", "find_user_id_by_email"}
TAU_SYSTEM = (
    "You are a customer service agent for an online retailer. Follow this policy.\n\n{policy}\n\n"
    "Make exactly one tool call: the first call you would make for this customer. Do not answer in prose."
)


def _json_type(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"type": "boolean"}
    if isinstance(v, int):
        return {"type": "integer"}
    if isinstance(v, float):
        return {"type": "number"}
    if isinstance(v, list):
        return {"type": "array", "items": _json_type(v[0]) if v else {"type": "string"}}
    return {"type": "string"}


def tools_from_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI tool definitions derived from the reference actions (name, argument names and value types)."""
    spec: dict[str, dict[str, Any]] = {}
    for t in tasks:
        for a in (t.get("evaluation_criteria") or {}).get("actions") or []:
            props = spec.setdefault(a["name"], {})
            for k, v in (a.get("arguments") or {}).items():
                props.setdefault(k, _json_type(v))
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Retail tool {name} (schema derived from the tau2-bench reference actions).",
                "parameters": {"type": "object", "properties": props, "required": sorted(props)},
            },
        }
        for name, props in sorted(spec.items())
    ]


def build_tau2(tasks: list[dict[str, Any]], policy: str, n: int = 100) -> dict:
    tools = tools_from_tasks(tasks)
    system = TAU_SYSTEM.format(policy=policy.strip())
    items = []
    for t in tasks:
        actions = (t.get("evaluation_criteria") or {}).get("actions") or []
        ins = (t.get("user_scenario") or {}).get("instructions") or {}
        if not actions or not isinstance(ins, dict):
            continue
        first, known = actions[0], str(ins.get("known_info") or "")
        if first["name"] not in TAU_LOOKUPS or not all(str(v) in known for v in first["arguments"].values()):
            continue
        items.append(
            {
                "id": f"tau2-retail-{t['id']}",
                "task_type": "tool_use",
                "cluster": known,
                "system": system,
                "prompt": f"Customer facts: {known}\nCustomer request: {ins.get('reason_for_call', '')}",
                "tools": tools,
                "expected": [{"name": first["name"], "arguments": first["arguments"]}],
                "meta": _meta("tau2-retail", "MIT", task_id=str(t["id"])),
            }
        )
        if len(items) >= n:
            break
    return _set(
        "tau2-retail",
        "tau2-bench retail (Sierra Research, MIT) single-turn first-tool-call slice. Not the full multi-turn benchmark.",
        {"tool_use": {"type": "tool_use", "mode": "first"}},
        items,
        max_tokens=1024,
    )


def build_tau2_from_files(raw: Path, n: int = 100) -> dict:
    base = raw / "data" / "tau2" / "domains" / "retail"
    tasks = json.loads((base / "tasks.json").read_text(encoding="utf-8"))
    return build_tau2(tasks, (base / "policy.md").read_text(encoding="utf-8"), n)


BUILDERS = {
    "banking77": build_banking_from_files,
    "cuad": build_cuad_from_files,
    "text2sql": build_text2sql_from_files,
    "tau2-retail": build_tau2_from_files,
}

# ---------------------------------------------------------------------------------------------- mock oracle


def with_mock_answers(golden: dict) -> dict:
    """Copy of a golden set whose prompts carry ``<mock-answer>`` (the mock's eval oracle). Used only when every
    target is a mock profile; real targets never see the directive."""
    out = json.loads(json.dumps(golden))
    for it in out["items"]:
        exp = it.get("expected")
        ans = exp if isinstance(exp, str) else json.dumps(exp, ensure_ascii=False)
        it["prompt"] = f"{it['prompt']}\n<mock-answer>{ans}</mock-answer>"
    return out


def merge_sets(name: str, sets: list[dict]) -> dict:
    graders: dict[str, Any] = {}
    items: list[dict[str, Any]] = []
    for s in sets:
        graders.update(s.get("graders") or {})
        items += s["items"]
    desc = "Combined demo sets: " + ", ".join(s["name"] for s in sets)
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "description": desc,
        "k": 1,
        "params": {"max_tokens": 1024},
        "graders": graders,
        "items": items,
    }
