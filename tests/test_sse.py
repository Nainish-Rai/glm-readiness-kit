from __future__ import annotations

from glmkit.client.sse import SSEParser, parse_sse

STREAM = (
    'event: message_start\r\ndata: {"a": 1}\r\n\r\n'
    ": keep-alive comment\n\n"
    "data: line one\ndata: line two\n\n"
    'data: {"x": "café 你好"}\n\n'
    "data: [DONE]\n\n"
)
EXPECTED = [("message_start", '{"a": 1}'), (None, "line one\nline two"), (None, '{"x": "café 你好"}'), (None, "[DONE]")]


def test_parse_whole() -> None:
    assert [(e.event, e.data) for e in parse_sse(STREAM)] == EXPECTED


def test_split_independent_at_every_point() -> None:
    for i in range(len(STREAM) + 1):
        p = SSEParser()
        evs = p.feed(STREAM[:i]) + p.feed(STREAM[i:]) + p.close()
        assert [(e.event, e.data) for e in evs] == EXPECTED, f"split at {i}"


def test_one_char_at_a_time_and_unterminated_tail() -> None:
    p = SSEParser()
    evs = [e for ch in STREAM + "data: tail" for e in p.feed(ch)] + p.close()
    assert [(e.event, e.data) for e in evs] == [*EXPECTED, (None, "tail")]
