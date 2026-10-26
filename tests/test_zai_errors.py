"""Error taxonomy: every documented code is covered, unknown codes are kept, retry hints parse."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from glmkit.zai_errors import CATEGORIES, TABLE, classify, retry_after_hint, table_rows
from tests.conftest import REPO

DOCUMENTED = [
    *[
        "1000",
        "1001",
        "1003",
        "1005",
        "1113",
        "1200",
        "1210",
        "1211",
        "1212",
        "1213",
        "1214",
        "1215",
        "1220",
        "1221",
        "1222",
        "1230",
        "1234",
        "1261",
        "1301",
        "1302",
        "1305",
    ],
    *["1308", "1309", "1310", "1311", "1313", "1314", "1315", "1316", "1317", "1318", "1319", "1320", "1321"],
]


def test_table_covers_every_documented_code() -> None:
    assert set(DOCUMENTED) <= set(TABLE)
    assert all(classify(c).known and classify(c).category in CATEGORIES for c in DOCUMENTED)


def test_table_matches_the_research_table() -> None:
    text = (REPO / "research" / "01-conformance.md").read_text()
    section = text.split("### 2.5 Error table")[1].split("### 2.6")[0]
    codes: set[str] = set()
    for m in re.finditer(r"^\| ([0-9 /-]+) \|", section, re.M):
        for part in m.group(1).split("/"):
            part = part.strip()
            if "-" in part:
                lo, hi = (int(x) for x in part.split("-"))
                codes |= {str(c) for c in range(lo, hi + 1)}
            elif part:
                codes.add(part)
    assert codes and codes <= set(TABLE), sorted(codes - set(TABLE))


def test_classification_fields() -> None:
    a = classify(1302, http_status=429)
    assert (a.category, a.retryable, a.retry_strategy, a.suggested_http) == ("rate_limit", True, "backoff", 429)
    b = classify("1113", http_status=429)
    assert (b.category, b.retryable, b.upstream_http, b.suggested_http) == ("billing", False, 429, 402)
    assert classify("1308").retry_strategy == "wait_for_reset"
    assert classify("1211").suggested_http == 404 and classify("1210").suggested_http == 400


def test_no_code_5xx_is_server_error_and_unknown_codes_are_kept() -> None:
    s = classify(None, http_status=500)
    assert s.known and s.category == "server" and s.retryable
    u = classify("7777", http_status=429)
    assert not u.known and u.category == "unknown" and u.code == "7777" and u.retryable and u.suggested_http == 429
    v = classify("7777", http_status=400)
    assert not v.known and not v.retryable and v.suggested_http == 400
    assert classify(None, http_status=404).category == "unknown"


def test_retry_after_hints() -> None:
    now = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
    assert retry_after_hint(None, {"Retry-After": "7"}) == 7
    assert retry_after_hint("Rate limit reached. Retry after 30 seconds") == 30
    assert retry_after_hint("try again in 500 ms") == 0.5
    assert retry_after_hint("usage limit reached, resets at 2026-10-26 13:00:00", now=now) == 3600
    assert retry_after_hint("nothing useful here") is None
    assert classify("1302", message="retry after 2 seconds").retry_after_s == 2


@pytest.mark.parametrize("row", table_rows(), ids=lambda r: r["code"])
def test_rows_are_complete(row) -> None:
    assert row["title"] and row["meaning"] and 400 <= row["suggested_http"] < 600
