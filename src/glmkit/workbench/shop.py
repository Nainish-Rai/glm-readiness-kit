"""The bundled demo SQLite database ("shop"): schema and synthetic rows generated here, no third-party data.

``data/shop.sqlite`` is the committed output of ``build_shop_db``; a test rebuilds it and compares the dumps. The
``sql_db`` grader opens it read-only, so a candidate query can never change what the next item sees.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "shop.sqlite"

SCHEMA = """
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL, tier TEXT NOT NULL);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, price REAL NOT NULL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
                     order_date TEXT NOT NULL, status TEXT NOT NULL, total REAL NOT NULL);
CREATE TABLE order_items (order_id INTEGER NOT NULL REFERENCES orders(id),
                          product_id INTEGER NOT NULL REFERENCES products(id), qty INTEGER NOT NULL,
                          PRIMARY KEY (order_id, product_id));
"""
SCHEMA_PROMPT = (
    "Tables (SQLite): customers(id, name, country, tier), products(id, name, category, price), "
    "orders(id, customer_id, order_date, status, total), order_items(order_id, product_id, qty). "
    "order_date is ISO text YYYY-MM-DD; status is one of paid, open, refunded; tier is one of basic, plus, pro."
)
COUNTRIES = ["DE", "FR", "ES", "NL", "PL", "SE"]
TIERS = ["basic", "plus", "pro"]
CATEGORIES = ["audio", "kitchen", "office", "outdoor"]
STATUSES = ["paid", "paid", "paid", "open", "refunded"]
FIRST = ["Anna", "Ben", "Chloe", "Dario", "Elif", "Farid", "Greta", "Hugo", "Ines", "Jonas", "Kira", "Luca"]


def build_shop_db(path: Path, seed: int = 13) -> Path:
    """Write the demo database (deterministic for a seed) and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = random.Random(seed)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO customers VALUES (?,?,?,?)",
            [(i + 1, f"{n} {chr(65 + i)}.", COUNTRIES[i % len(COUNTRIES)], TIERS[i % 3]) for i, n in enumerate(FIRST)],
        )
        products = [
            (i + 1, f"{CATEGORIES[i % 4]}-item-{i + 1}", CATEGORIES[i % 4], round(rng.uniform(5, 250), 2))
            for i in range(16)
        ]
        con.executemany("INSERT INTO products VALUES (?,?,?,?)", products)
        for oid in range(1, 81):
            cust = rng.randrange(1, len(FIRST) + 1)
            date = f"2026-{rng.randrange(1, 10):02d}-{rng.randrange(1, 28):02d}"
            picks = rng.sample(products, rng.randrange(1, 4))
            qtys = [rng.randrange(1, 4) for _ in picks]
            total = round(sum(p[3] * q for p, q in zip(picks, qtys, strict=True)), 2)
            con.execute("INSERT INTO orders VALUES (?,?,?,?,?)", (oid, cust, date, rng.choice(STATUSES), total))
            con.executemany(
                "INSERT INTO order_items VALUES (?,?,?)",
                [(oid, p[0], q) for p, q in zip(picks, qtys, strict=True)],
            )
        con.commit()
    finally:
        con.close()
    return path


def dump(path: Path) -> list[str]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return list(con.iterdump())
    finally:
        con.close()
