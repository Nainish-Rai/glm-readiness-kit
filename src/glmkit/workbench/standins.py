# ruff: noqa: E501
"""Synthetic stand-in items, written for this kit (no third-party text), used when a demo set has not been fetched.

They make ``glmkit demo run`` work fully offline and keep the same task types, graders and statistics as the real
sets, with fewer items (30-44 each). The dataset cards say so; ``glmkit demo run`` prints which mode ran.
"""

from __future__ import annotations

import random
from typing import Any

from glmkit.workbench.builders import (
    CUAD_EXPLAIN_SYSTEM,
    CUAD_SPAN_SYSTEM,
    SQL_SYSTEM,
    _meta,
    _set,
    build_tau2,
)
from glmkit.workbench.shop import SCHEMA_PROMPT

BANK = {
    "card_arrival": [
        "My new card has not shown up yet, it was posted {n} days ago.",
        "When will the card I ordered arrive?",
    ],
    "card_not_working": [
        "My card is declined at every shop since {n} o'clock.",
        "The chip on my card stopped working.",
    ],
    "lost_or_stolen_card": [
        "I think someone took my wallet with the card on the train.",
        "My card is gone, please block it.",
    ],
    "pending_transfer": ["My transfer of {n} euros still says pending.", "How long will this payment stay pending?"],
    "cash_withdrawal_charge": [
        "Why was I charged a fee for taking out {n} euros at the ATM?",
        "There is an extra fee on my cash withdrawal.",
    ],
    "exchange_rate": [
        "What rate did you use to convert my {n} dollars?",
        "How is the exchange rate worked out for card payments abroad?",
    ],
    "top_up_failed": [
        "My top up of {n} euros failed but the money left my other account.",
        "Top-up did not go through.",
    ],
    "verify_my_identity": [
        "How do I prove who I am to open the account?",
        "Which documents do you need to verify me, reference {n}?",
    ],
    "balance_not_updated_after_bank_transfer": [
        "I sent {n} euros yesterday and my balance did not change.",
        "Transfer made but balance is the same.",
    ],
    "transfer_not_received_by_recipient": [
        "My friend never received the {n} euros I sent.",
        "The recipient says the transfer did not arrive.",
    ],
}


def banking(seed: int = 7) -> dict:
    rng = random.Random(seed)
    labels = sorted(BANK)
    system = (
        "You are a banking customer-support intent classifier. Reply with exactly one label from this list and "
        "nothing else:\n" + ", ".join(labels)
    )
    items = []
    for i in range(40):
        lab = labels[i % len(labels)]
        text = BANK[lab][(i // len(labels)) % 2].format(n=rng.randrange(2, 900))
        items.append(
            {
                "id": f"banking-standin-{i:03d}",
                "task_type": "classify",
                "system": system,
                "prompt": text,
                "expected": lab,
                "meta": _meta("standin", "synthetic"),
            }
        )
    return _set(
        "banking-standin",
        "Synthetic stand-in for Banking77: invented tickets over 10 real Banking77 label names.",
        {"classify": {"type": "exact", "ignore_case": True, "strip_punct": True}},
        items,
        max_tokens=800,
    )


CLAUSES = {
    "Governing Law": "This Agreement shall be governed by and construed in accordance with the laws of {place}, without regard to its conflict of laws rules.",
    "Termination For Convenience": "Either party may terminate this Agreement for any reason upon {n} days prior written notice to the other party.",
    "Non-Compete": "During the Term and for {n} months thereafter, the Supplier shall not provide services to any direct competitor of the Customer in {place}.",
    "Audit Rights": "The Customer may, on {n} days notice and not more than once per year, audit the Supplier's records relating to the fees charged under this Agreement.",
    "Cap On Liability": "In no event shall either party's aggregate liability under this Agreement exceed the fees paid in the {n} months preceding the claim.",
    "Renewal Term": "This Agreement renews automatically for successive periods of {n} months unless either party gives notice of non-renewal.",
}
FILLER = [
    "The parties have entered into this Agreement on the date first written above.",
    "Capitalised terms used in this section have the meanings given in Schedule 1.",
    "Each party shall bear its own costs in connection with the negotiation of this Agreement.",
    "Notices under this Agreement must be in writing and delivered to the addresses set out in Schedule 2.",
    "Nothing in this Agreement creates a partnership or agency between the parties.",
    "Headings are for convenience only and do not affect interpretation.",
]
PLACES = ["Ireland", "the State of New York", "Germany", "England and Wales", "Singapore"]


def cuad(seed: int = 5) -> dict:
    rng = random.Random(seed)
    names = sorted(CLAUSES)
    items = []
    for i in range(44):
        cat = names[i % len(names)]
        clause = CLAUSES[cat].format(n=rng.choice([15, 30, 45, 60, 90]), place=rng.choice(PLACES))
        sents = rng.sample(FILLER, 4)
        pos = rng.randrange(0, 5)
        sents.insert(pos, clause)
        excerpt = " ".join(sents)
        body = f'Clause type: {cat}\n\nContract excerpt:\n"""\n{excerpt}\n"""'
        title = f"Standin Agreement {i // 2:02d}"
        if i < 24:
            items.append(
                {
                    "id": f"cuad-standin-span-{i:03d}",
                    "task_type": "clause_span",
                    "cluster": title,
                    "system": CUAD_SPAN_SYSTEM,
                    "prompt": body,
                    "expected": clause,
                    "meta": _meta("standin", "synthetic", category=cat),
                }
            )
        else:
            items.append(
                {
                    "id": f"cuad-standin-explain-{i - 24:03d}",
                    "task_type": "clause_explain",
                    "cluster": title,
                    "system": CUAD_EXPLAIN_SYSTEM,
                    "prompt": body + f"\n\nExplain what the {cat} clause in this excerpt requires or allows.",
                    "expected": f"The excerpt says: {clause}",
                    "meta": _meta("standin", "synthetic", category=cat),
                }
            )
    return _set(
        "cuad-standin",
        "Synthetic stand-in for the CUAD slice: invented contract excerpts with templated clauses.",
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


def text2sql(seed: int = 3) -> dict:
    """Questions over the bundled 'shop' database; the gold SQL is written from templates and checked to run."""
    from glmkit.eval.graders.sql_db import run_readonly
    from glmkit.workbench.shop import COUNTRIES, DB_PATH

    rng = random.Random(seed)
    qs: list[tuple[str, str]] = []
    for c in COUNTRIES:
        qs.append((f"How many customers live in {c}?", f"SELECT COUNT(*) FROM customers WHERE country = '{c}'"))
        qs.append(
            (
                f"What is the total value of paid orders from customers in {c}?",
                f"SELECT ROUND(SUM(o.total), 2) FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.country = '{c}' AND o.status = 'paid'",
            )
        )
    for cat in ["audio", "kitchen", "office", "outdoor"]:
        qs.append(
            (
                f"What is the average price of {cat} products?",
                f"SELECT ROUND(AVG(price), 2) FROM products WHERE category = '{cat}'",
            )
        )
        qs.append(
            (
                f"Which {cat} product is the most expensive? Return its name.",
                f"SELECT name FROM products WHERE category = '{cat}' ORDER BY price DESC LIMIT 1",
            )
        )
    for tier in ["basic", "plus", "pro"]:
        qs.append(
            (
                f"How many orders were placed by customers on the {tier} tier?",
                f"SELECT COUNT(*) FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.tier = '{tier}'",
            )
        )
    for month in range(1, 10):
        qs.append(
            (
                f"How many refunded orders were placed in month {month:02d} of 2026?",
                f"SELECT COUNT(*) FROM orders WHERE status = 'refunded' AND order_date LIKE '2026-{month:02d}-%'",
            )
        )
    for st in ("paid", "open", "refunded"):
        qs.append((f"How many orders have status {st}?", f"SELECT COUNT(*) FROM orders WHERE status = '{st}'"))
    for tier in ["basic", "plus", "pro"]:
        qs.append(
            (
                f"What is the sum of all paid order totals for {tier} tier customers?",
                f"SELECT ROUND(SUM(o.total), 2) FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.tier = '{tier}' AND o.status = 'paid'",
            )
        )
    for cat in ["audio", "kitchen", "office", "outdoor"]:
        qs.append(
            (
                f"How many units of {cat} products were ordered in total?",
                f"SELECT SUM(oi.qty) FROM order_items oi JOIN products p ON p.id = oi.product_id WHERE p.category = '{cat}'",
            )
        )
    for k in (3, 5):
        qs.append(
            (
                f"List the ids of the {k} largest orders by total, largest first.",
                f"SELECT id FROM orders ORDER BY total DESC, id LIMIT {k}",
            )
        )
    rng.shuffle(qs)
    items = []
    for i, (q, gold) in enumerate(qs[:44]):
        if not run_readonly(DB_PATH, gold):
            raise ValueError(f"stand-in gold SQL returns no rows: {gold}")
        items.append(
            {
                "id": f"text2sql-standin-{i:03d}",
                "task_type": "text_to_sql",
                "system": SQL_SYSTEM,
                "prompt": f"{SCHEMA_PROMPT}\n\nQuestion: {q}",
                "expected": gold,
                "grader": {"type": "sql_db", "database": "shop", "ordered": False},
                "meta": _meta("standin", "synthetic"),
            }
        )
    return _set(
        "text2sql-standin",
        "Synthetic stand-in for text-to-SQL: questions over the kit's bundled 'shop' SQLite database.",
        {},
        items,
        max_tokens=800,
    )


def tau2(seed: int = 9) -> dict:
    rng = random.Random(seed)
    firsts = ["Maya", "Omar", "Sofia", "Lukas", "Nia", "Tariq"]
    lasts = ["Fernandez", "Okoye", "Lindqvist", "Petrov", "Haddad", "Moreau"]
    reasons = [
        "I want to cancel my pending order #W{n}.",
        "My package #W{n} arrived with a broken item and I need a return.",
        "Please change the delivery address on order #W{n}.",
        "I would like to exchange an item from order #W{n} for a different colour.",
    ]
    tasks: list[dict[str, Any]] = []
    for i in range(30):
        first, last, zipc = rng.choice(firsts), rng.choice(lasts), f"{rng.randrange(10000, 99999)}"
        email = f"{first.lower()}.{last.lower()}{rng.randrange(10, 99)}@example.com"
        by_email = i % 3 == 0
        known = f"You are {first} {last}" + (f" and your email is {email}." if by_email else f" in zip code {zipc}.")
        args = {"email": email} if by_email else {"first_name": first, "last_name": last, "zip": zipc}
        action = {"name": "find_user_id_by_email" if by_email else "find_user_id_by_name_zip", "arguments": args}
        tasks.append(
            {
                "id": f"s{i:02d}",
                "user_scenario": {
                    "instructions": {
                        "known_info": known,
                        "reason_for_call": rng.choice(reasons).format(n=rng.randrange(1000000, 9999999)),
                    }
                },
                "evaluation_criteria": {
                    "actions": [
                        action,
                        {"name": "get_order_details", "arguments": {"order_id": "#W1000000"}},
                        {
                            "name": "cancel_pending_order",
                            "arguments": {"order_id": "#W1000000", "reason": "no longer needed"},
                        },
                    ]
                },
            }
        )
    policy = "Authenticate the customer first: look the user up by email, or by first name, last name and zip code, before anything else. (Synthetic stand-in policy.)"
    s = build_tau2(tasks, policy, n=30)
    s["name"] = "tau2-standin"
    s["description"] = "Synthetic stand-in for the tau2 retail slice: invented customer openers, same lookup tools."
    for it in s["items"]:
        it["id"] = it["id"].replace("tau2-retail-", "tau2-standin-")
        it["meta"] = _meta("standin", "synthetic")
    return s


STANDINS = {"banking77": banking, "cuad": cuad, "text2sql": text2sql, "tau2-retail": tau2}
