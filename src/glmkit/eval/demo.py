"""A self-contained demo for the mock server: a synthetic golden set with five task types and mock targets whose
answer quality is *planted* (see ``glmkit.mock.defects.eval_quality``). Real engagements bring their own golden set
(issue 13 builds the licensed demo sets); this one exists to prove the engine and to render the Migration tab.

``glmkit eval demo --out DIR`` writes ``DIR/demo-set.yaml`` and ``DIR/profiles/*.yaml``.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import yaml

SQL_SETUP = (
    "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer TEXT, total REAL, status TEXT);"
    "INSERT INTO orders VALUES (1,'alice',120.5,'paid'),(2,'alice',80,'open'),(3,'bob',42,'paid'),"
    "(4,'bob',17.25,'paid'),(5,'carol',300,'open'),(6,'carol',55.5,'paid'),(7,'dave',9,'paid');"
)
INTENTS = ["card_lost", "refund_status", "change_address", "close_account", "dispute_charge", "reset_password"]
TICKETS = {
    "card_lost": "I cannot find my debit card anywhere since Monday.",
    "refund_status": "Where is the refund for order {n}? It was promised last week.",
    "change_address": "I moved flats and need to update my billing address (ref {n}).",
    "close_account": "Please close my account {n} and delete my data.",
    "dispute_charge": "There is a charge of {n} euros I do not recognise.",
    "reset_password": "The reset link for user {n} expires before I can use it.",
}
CUSTOMERS = ["alice", "bob", "carol", "dave"]
JSON_SCHEMA = {
    "type": "object",
    "required": ["name", "amount", "currency"],
    "properties": {"name": {"type": "string"}, "amount": {"type": "number"}, "currency": {"type": "string"}},
}
# profile name -> (model, degrade rate, salt, role)
TARGETS = {
    "demo-incumbent": ("mock-incumbent-1", 0.03, "incumbent", "stand-in incumbent (planted error rate 3%)"),
    "demo-glm-equal": ("glm-5.3-flash", 0.03, "equal", "GLM-5.3 Flash, planted equal to the incumbent (3%)"),
    "demo-glm-worse": ("glm-5.3-flashx", 0.25, "worse", "GLM-5.3 FlashX, planted worse (25% errors)"),
    "demo-glm": ("glm-5.3", 0.03, "glm", "GLM-5.3 for the effort sweep (low/high/max), planted equal (3%)"),
}


def build_items(n_scale: int = 1, seed: int = 7) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    items: list[dict[str, Any]] = []
    for i in range(120 * n_scale):
        intent = INTENTS[rng.randrange(len(INTENTS))]
        text = TICKETS[intent].format(n=rng.randrange(1000, 9999))
        items.append(
            {
                "id": f"classify-{i:03d}",
                "task_type": "classify",
                "prompt": f"Classify the ticket into one intent label.\nTicket {i}: {text}\n<mock-answer>{intent}</mock-answer>",  # noqa: E501
                "expected": intent,
                "critical": intent == "dispute_charge",
            }
        )
    for i in range(80 * n_scale):
        a, b = rng.randrange(10, 500), rng.randrange(10, 500)
        items.append(
            {
                "id": f"math-{i:03d}",
                "task_type": "arithmetic",
                "prompt": f"What is {a} + {b}? Give only the number.\n<mock-answer>{a + b}</mock-answer>",
                "expected": a + b,
            }
        )
    for i in range(60 * n_scale):
        amount = round(rng.uniform(5, 900), 2)
        obj = {"name": f"vendor-{i}", "amount": amount, "currency": rng.choice(["EUR", "USD", "GBP"])}
        items.append(
            {
                "id": f"extract-{i:03d}",
                "task_type": "extract",
                "prompt": f"Extract the invoice fields as JSON.\nInvoice: {obj['name']} charged {amount} {obj['currency']}.\n"  # noqa: E501
                f"<mock-answer>{json.dumps(obj)}</mock-answer>",
                "expected": obj,
            }
        )
    for i in range(40 * n_scale):
        who = CUSTOMERS[i % len(CUSTOMERS)]
        agg = ["SUM(total)", "COUNT(*)", "MAX(total)", "MIN(total)", "AVG(total)"][(i // len(CUSTOMERS)) % 5]
        sql = f"SELECT {agg} FROM orders WHERE customer = '{who}' AND status = 'paid'"
        items.append(
            {
                "id": f"sql-{i:03d}",
                "task_type": "text_to_sql",
                "prompt": f"Write one SQLite query: {agg} of paid orders of customer {who}.\n<mock-answer>{sql}</mock-answer>",  # noqa: E501
                "expected": sql,
            }
        )
    for i in range(40 * n_scale):
        ticket = f"ticket-{i // 2:03d}"
        summary = f"Customer reports problem {i} with the order and asks for a callback tomorrow."
        items.append(
            {
                "id": f"summ-{i:03d}",
                "task_type": "summarize",
                "cluster": ticket,
                "prompt": f"Summarise the conversation in one sentence.\nConversation {i}: ...\n<mock-answer>{summary}</mock-answer>",  # noqa: E501
                "expected": summary,
            }
        )
    return items


def build_set(n_scale: int = 1, seed: int = 7) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "name": "mock-demo",
        "description": "Synthetic mock-only set; the mock answers from the <mock-answer> directive of each prompt.",
        "k": 1,
        "params": {"max_tokens": 600},
        "graders": {
            "classify": {"type": "exact", "ignore_case": True},
            "arithmetic": {"type": "numeric", "extract": "last"},
            "extract": {"type": "json_schema", "schema": JSON_SCHEMA, "match_expected": True},
            "text_to_sql": {"type": "sql", "setup": [SQL_SETUP]},
            "summarize": {
                "type": "judge",
                "rubric": "The better summary is the one closest in meaning to the reference.",
            },
        },
        "items": build_items(n_scale, seed),
    }


def build_profiles() -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for name, (model, rate, salt, title) in TARGETS.items():
        profiles[name] = {
            "name": name,
            "title": title,
            "provider": "mock",
            "base_url": "{mock}/api/paas/v4",
            "protocol": "openai_chat",
            "endpoint_class": "api",
            "model": model,
            "api_key_required": False,
            "spawn_mock": True,
            "headers": {
                "X-GLMKit-Defects": "eval-oracle,degrade-quality",
                "X-GLMKit-Degrade-Rate": str(rate),
                "X-GLMKit-Degrade-Salt": salt,
            },
        }
    profiles["demo-judge"] = {
        "name": "demo-judge",
        "title": "Mock judge (other model family, 15% planted first-position bias)",
        "provider": "mock",
        "base_url": "{mock}/api/paas/v4",
        "protocol": "openai_chat",
        "endpoint_class": "api",
        "model": "mock-judge-1",
        "api_key_required": False,
        "spawn_mock": True,
        "headers": {"X-GLMKit-Defects": "mock-judge", "X-GLMKit-Judge-Position-Bias": "0.15"},
    }
    return profiles


def write_demo(out: Path, n_scale: int = 1, seed: int = 7) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    (out / "profiles").mkdir(exist_ok=True)
    set_path = out / "demo-set.yaml"
    set_path.write_text(yaml.safe_dump(build_set(n_scale, seed), sort_keys=False, allow_unicode=True), encoding="utf-8")
    for name, prof in build_profiles().items():
        (out / "profiles" / f"{name}.yaml").write_text(yaml.safe_dump(prof, sort_keys=False), encoding="utf-8")
    return {"set": set_path, "profiles": out / "profiles"}
