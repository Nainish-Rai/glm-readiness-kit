"""Incremental Server-Sent Events parser. Split-independent: feeding the same bytes in any chunking yields
the same events (tested with every split point)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SSEEvent:
    data: str
    event: str | None = None
    id: str | None = None


@dataclass
class SSEParser:
    _buf: str = ""
    _data: list[str] = field(default_factory=list)
    _event: str | None = None
    _id: str | None = None
    _pending_cr: bool = False

    def feed(self, text: str) -> list[SSEEvent]:
        if self._pending_cr:
            text = "\r" + text
            self._pending_cr = False
        if text.endswith("\r"):
            text, self._pending_cr = text[:-1], True
        self._buf += text.replace("\r\n", "\n").replace("\r", "\n")
        out: list[SSEEvent] = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            ev = self._line(line)
            if ev is not None:
                out.append(ev)
        return out

    def close(self) -> list[SSEEvent]:
        """Flush a final event that was not followed by a blank line."""
        out = self.feed("\n") if self._buf else []
        if self._data:
            out.append(self._emit())
        return out

    def _emit(self) -> SSEEvent:
        ev = SSEEvent(data="\n".join(self._data), event=self._event, id=self._id)
        self._data, self._event = [], None
        return ev

    def _line(self, line: str) -> SSEEvent | None:
        if line == "":
            return self._emit() if self._data else None
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "data":
            self._data.append(value)
        elif name == "event":
            self._event = value
        elif name == "id":
            self._id = value
        return None


def parse_sse(text: str) -> list[SSEEvent]:
    p = SSEParser()
    return p.feed(text) + p.close()
