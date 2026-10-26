"""Demo sets: dataset cards (license required), checksummed fetch, builders on tiny fixtures, stand-ins, graders."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
import yaml

from glmkit.eval.goldenset import GoldenSet
from glmkit.eval.graders import GradeContext, grade
from glmkit.workbench import builders, cards, fetch, shop, standins
from glmkit.workbench.cards import CARDS_DIR


def test_every_shipped_card_is_valid_and_states_a_license() -> None:
    assert cards.check_cards() == []
    ids = cards.set_ids()
    assert set(ids) == {"banking77", "cuad", "text2sql", "tau2-retail"}
    for sid in ids:
        c = cards.load_card(sid)
        assert c.license.spdx and c.license.verified_at and c.attribution
        assert 50 <= c.items <= 100 and all(len(f.sha256) == 64 for f in c.source.files)


def test_a_card_without_a_license_fails(tmp_path: Path) -> None:
    good = yaml.safe_load((CARDS_DIR / "banking77.yaml").read_text())
    del good["license"]
    (tmp_path / "planted.yaml").write_text(yaml.safe_dump(good))
    bad = cards.check_cards(tmp_path)
    assert any("no license stated" in b for b in bad)
    from typer.testing import CliRunner

    from glmkit.cli import build_app

    r = CliRunner().invoke(build_app(), ["demo", "cards", "--dir", str(tmp_path)])
    assert r.exit_code == 1 and "no license stated" in r.output


def test_a_card_without_a_pinned_checksum_fails(tmp_path: Path) -> None:
    c = yaml.safe_load((CARDS_DIR / "cuad.yaml").read_text())
    c["source"]["files"][0]["sha256"] = "abc"
    (tmp_path / "x.yaml").write_text(yaml.safe_dump(c))
    assert cards.check_cards(tmp_path)


# ---- fetch: pinned checksums, served by a fake transport


def _card_for(payload: bytes, member_name: str = "banking_data/test.csv") -> cards.DatasetCard:
    data = yaml.safe_load((CARDS_DIR / "banking77.yaml").read_text())
    data["source"]["files"] = [
        {
            "url": "https://example.test/test.csv",
            "path": member_name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
    ]
    return cards.parse_card(data)


CSV = "text,category\n" + "\n".join(f"text {i},label_{i % 3}" for i in range(9)) + "\n"


def _client(body: bytes) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body)))


def test_fetch_verifies_checksum_and_builds_the_set(tmp_path: Path) -> None:
    body = CSV.encode()
    out = fetch.fetch_set("banking77", tmp_path, client=_client(body), card=_card_for(body))
    gs = GoldenSet.model_validate(yaml.safe_load(out.read_text()))
    assert len(gs.items) == 9 and gs.items[0].meta["license"] == "CC-BY-4.0"
    rec = fetch.fetched("banking77", tmp_path)
    assert rec and rec["files"][0]["sha256"] == hashlib.sha256(body).hexdigest()
    # a second run reuses the verified download (no network needed)
    fetch.fetch_set("banking77", tmp_path, client=_client(b"unused"), card=_card_for(body))


def test_fetch_rejects_a_tampered_download_and_leaves_nothing(tmp_path: Path) -> None:
    card = _card_for(CSV.encode())
    with pytest.raises(fetch.ChecksumError):
        fetch.fetch_set("banking77", tmp_path, client=_client(b"text,category\nevil,x\n"), card=card)
    assert not list(tmp_path.rglob("test.csv*")) and fetch.fetched("banking77", tmp_path) is None


def test_fetch_reports_http_errors(tmp_path: Path) -> None:
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(fetch.FetchError):
        fetch.fetch_set("banking77", tmp_path, client=c, card=_card_for(b"x"))


# ---- builders on tiny hand-written fixtures


def test_build_banking_stratifies() -> None:
    rows = [{"text": f"t{i}", "category": f"c{i % 3}"} for i in range(5)]
    gs = GoldenSet.model_validate(builders.build_banking(rows, n=5))
    assert len(gs.items) == 5 and {i.expected for i in gs.items} == {"c0", "c1", "c2"}
    assert "c0, c1, c2" in (gs.items[0].system or "")


def test_build_cuad_makes_span_and_explain_items() -> None:
    clause = "This Agreement shall be governed by the laws of Ireland and any dispute is for its courts."
    ctx = "Intro text. " * 20 + clause + " Closing text. " * 20

    def doc(n: int) -> dict:
        return {
            "title": f"C{n}",
            "paragraphs": [
                {
                    "context": ctx,
                    "qas": [
                        {
                            "id": f"C{n}__Governing Law",
                            "is_impossible": False,
                            "answers": [{"text": clause, "answer_start": ctx.index(clause)}],
                        }
                    ],
                }
            ],
        }

    gs = GoldenSet.model_validate(builders.build_cuad({"data": [doc(i) for i in range(4)]}, n_span=3, n_explain=1))
    kinds = [i.task_type for i in gs.items]
    assert kinds.count("clause_span") == 3 and kinds.count("clause_explain") == 1
    assert all(clause in i.prompt for i in gs.items if i.task_type == "clause_span")


def test_build_text2sql_keeps_only_executable_rows() -> None:
    ctx = "CREATE TABLE t (a INT); INSERT INTO t VALUES (1),(2);"
    rows = [
        {"sql_prompt": "how many?", "sql_context": ctx, "sql": "SELECT COUNT(*) FROM t", "domain": "d"},
        {"sql_prompt": "broken", "sql_context": ctx, "sql": "SELEKT nothing"},
        {"sql_prompt": "empty", "sql_context": ctx, "sql": "SELECT a FROM t WHERE a > 5"},
    ]
    gs = GoldenSet.model_validate(builders.build_text2sql(rows, n=5))
    assert [i.expected for i in gs.items] == ["SELECT COUNT(*) FROM t"]
    assert gs.items[0].grader["setup"] == [ctx]


def test_build_tau2_selects_lookup_first_actions() -> None:
    def task(i: str, name: str, args: dict, known: str) -> dict:
        return {
            "id": i,
            "user_scenario": {"instructions": {"known_info": known, "reason_for_call": "cancel"}},
            "evaluation_criteria": {
                "actions": [
                    {"name": name, "arguments": args},
                    {"name": "get_order_details", "arguments": {"order_id": "#W1"}},
                ]
            },
        }

    tasks = [
        task(
            "0",
            "find_user_id_by_name_zip",
            {"first_name": "A", "last_name": "B", "zip": "12345"},
            "You are A B in zip 12345.",
        ),
        task("1", "find_user_id_by_email", {"email": "a@b.c"}, "Your email is a@b.c"),
        task("2", "find_user_id_by_email", {"email": "hidden@x.y"}, "You forgot your email"),
        task("3", "get_order_details", {"order_id": "#W1"}, "known"),
    ]
    gs = GoldenSet.model_validate(builders.build_tau2(tasks, "policy text"))
    assert [i.id for i in gs.items] == ["tau2-retail-0", "tau2-retail-1"]
    names = {t["function"]["name"] for t in gs.items[0].tools}
    assert {"find_user_id_by_email", "get_order_details"} <= names


def test_mock_answers_are_added_only_to_a_copy() -> None:
    gs = standins.banking()
    mocked = builders.with_mock_answers(gs)
    assert "<mock-answer>" not in gs["items"][0]["prompt"]
    assert f"<mock-answer>{gs['items'][0]['expected']}</mock-answer>" in mocked["items"][0]["prompt"]
    tau = builders.with_mock_answers(standins.tau2())
    assert json.loads(tau["items"][0]["prompt"].split("<mock-answer>")[1].split("</mock-answer>")[0])[0]["name"]


@pytest.mark.parametrize("sid", sorted(standins.STANDINS))
def test_standins_are_valid_golden_sets(sid: str) -> None:
    gs = GoldenSet.model_validate(standins.STANDINS[sid]())
    assert 30 <= len(gs.items) <= 50
    assert all(i.meta["source"] == "standin" for i in gs.items)


# ---- bundled database and graders


def test_bundled_shop_db_matches_its_generator(tmp_path: Path) -> None:
    rebuilt = shop.build_shop_db(tmp_path / "shop.sqlite")
    assert shop.dump(rebuilt) == shop.dump(shop.DB_PATH)


def _ctx(output: str, expected, config: dict) -> GradeContext:
    from glmkit.eval.goldenset import GoldenItem

    return GradeContext(
        item=GoldenItem(id="x", prompt="p", expected=expected), config=config, output=output, target="t"
    )


GOLD = "SELECT COUNT(*) FROM customers WHERE country = 'DE'"


async def test_sql_db_grader_correct_query_passes() -> None:
    g = await grade(_ctx("```sql\nSELECT COUNT(id) FROM customers WHERE country='DE';\n```", GOLD, {"type": "sql_db"}))
    assert g.passed and g.score == 1.0


async def test_sql_db_grader_incorrect_query_fails() -> None:
    g = await grade(_ctx("SELECT COUNT(*) FROM customers", GOLD, {"type": "sql_db"}))
    assert not g.passed and g.score == 0.0 and "expected" in g.detail


async def test_sql_db_grader_invalid_and_destructive_queries_are_wrong_not_crashes() -> None:
    bad = await grade(_ctx("SELEKT 1", GOLD, {"type": "sql_db"}))
    drop = await grade(_ctx("DROP TABLE customers", GOLD, {"type": "sql_db"}))
    assert not bad.passed and not drop.passed and "failed" in drop.detail
    con = sqlite3.connect(f"file:{shop.DB_PATH}?mode=ro", uri=True)
    assert con.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 12


async def test_stock_sql_grader_with_per_item_context() -> None:
    cfg = {"type": "sql", "setup": ["CREATE TABLE t (a INT); INSERT INTO t VALUES (1),(2);"]}
    assert (await grade(_ctx("SELECT COUNT(*) FROM t", "SELECT COUNT(*) FROM t", cfg))).passed
    assert not (await grade(_ctx("SELECT 3", "SELECT COUNT(*) FROM t", cfg))).passed


async def test_span_grader() -> None:
    gold = "Either party may terminate this Agreement on 30 days notice."
    assert (await grade(_ctx(f'"{gold}"', gold, {"type": "span"}))).passed
    assert not (await grade(_ctx("The weather is nice today.", gold, {"type": "span"}))).passed
    assert (await grade(_ctx("none", None, {"type": "span", "expected": []}))).passed


async def test_tool_use_grader() -> None:
    want = [{"name": "find_user_id_by_email", "arguments": {"email": "a@b.c"}}]
    ok = json.dumps([{"name": "find_user_id_by_email", "arguments": {"email": "a@b.c"}}])
    ok_str_args = json.dumps([{"name": "find_user_id_by_email", "arguments": json.dumps({"email": " a@b.c "})}])
    wrong_arg = json.dumps([{"name": "find_user_id_by_email", "arguments": {"email": "x@y.z"}}])
    wrong_tool = json.dumps([{"name": "get_user_details", "arguments": {"user_id": "u"}}])
    cfg = {"type": "tool_use"}
    assert (await grade(_ctx(ok, want, cfg))).passed and (await grade(_ctx(ok_str_args, want, cfg))).passed
    assert "argument 'email'" in (await grade(_ctx(wrong_arg, want, cfg))).detail
    assert "expected 'find_user_id_by_email'" in (await grade(_ctx(wrong_tool, want, cfg))).detail
    assert not (await grade(_ctx("I will look you up", want, cfg))).passed
