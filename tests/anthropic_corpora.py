"""Ten token-accounting corpora (a local copy of the ANTH-004 set from research/01-conformance.md: JSON, code, CJK,
special characters, English, ...). Each is a few thousand characters so framing does not dominate the count."""

from __future__ import annotations

CORPORA: dict[str, str] = {
    "json-flat": '{"key": "value", "n": 12345, "ok": true} ' * 300,
    "json-nested": '{"user": {"id": 7, "tags": ["a", "b", "c"], "address": {"city": "Berlin", "zip": "10115"}}} ' * 120,
    "python-code": "def fib(n: int) -> int:\n    return n if n < 2 else fib(n - 1) + fib(n - 2)\n\n" * 90,
    "typescript-code": "export const add = (a: number, b: number): number => { return a + b; };\n" * 110,
    "english-prose": "The quick brown fox jumps over the lazy dog while the evening sun settles over the harbour. "
    * 80,
    "cjk-chinese": "这是一个用于测试分词器的句子，包含常见的汉字和标点符号，用来检查令牌计数是否一致。" * 130,
    "cjk-japanese": "吾輩は猫である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。" * 150,
    "special-chars": "!@#$%^&*()_+-=[]{}|;':\",./<>?`~\\ \t " * 220,
    "markdown-table": "| col a | col b | col c |\n|-------|-------|-------|\n| 1 | two | 3.0 |\n" * 130,
    "mixed-log": "2026-10-26T10:00:00Z INFO request_id=abc123 path=/v1/messages status=200 latency_ms=42 用户=测试\n"
    * 90,
}
