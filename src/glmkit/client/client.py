"""Unified async client for both GLM protocols, stream and non-stream.

Every call returns an ``Exchange``: redacted request, raw response (body or SSE events with timestamps),
a ``NormalizedResponse`` and timings (TTFB, TTFT, total). Transport failures are recorded, not raised, so a
broken host produces evidence instead of a crash.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import httpx

from glmkit.client.adapters import ANTHROPIC_VERSION, StreamAccumulator, build_body, normalize
from glmkit.client.sse import SSEParser
from glmkit.contracts.evidence import (
    MAX_BODY_CHARS,
    MAX_EVENTS,
    Exchange,
    HttpRequestRecord,
    HttpResponseRecord,
    NormalizedResponse,
    SSEEventRecord,
    Timing,
)
from glmkit.contracts.profile import EndpointProfile
from glmkit.redact import Redactor, redact_headers


class MissingKeyError(RuntimeError):
    pass


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 3)


class GLMClient:
    def __init__(
        self,
        profile: EndpointProfile,
        *,
        timeout_s: float = 60.0,
        stream_timeout_s: float = 120.0,
        extra_headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.profile = profile
        self.timeout_s = timeout_s
        self.stream_timeout_s = stream_timeout_s
        self.extra_headers = dict(extra_headers or {})
        key = profile.api_key()
        if profile.api_key_required and not key:
            raise MissingKeyError(f"target {profile.name!r} needs a key in ${profile.api_key_env}")
        self._key = key
        self.redactor = redactor or Redactor()
        self.redactor.add(key)
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout_s), transport=transport)

    async def __aenter__(self) -> GLMClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"content-type": "application/json", "user-agent": "glmkit"}
        if self.profile.protocol == "anthropic_messages":
            h["anthropic-version"] = ANTHROPIC_VERSION
        if self._key:
            if self.profile.auth_style == "x-api-key":
                h["x-api-key"] = self._key
            else:
                h["authorization"] = f"Bearer {self._key}"
        h.update(self.profile.headers)
        h.update(self.extra_headers)
        h.update(extra or {})
        return h

    def body_for(self, canon: dict[str, Any], *, stream: bool) -> dict[str, Any]:
        return build_body(self.profile.protocol, canon, model=self.profile.model, stream=stream)

    async def send_canonical(self, canon: dict[str, Any], *, stream: bool, label: str = "main", **kw: Any) -> Exchange:
        return await self.send(self.body_for(canon, stream=stream), stream=stream, label=label, **kw)

    async def send(
        self, body: dict[str, Any], *, stream: bool, label: str = "main", headers: dict[str, str] | None = None
    ) -> Exchange:
        protocol = self.profile.protocol
        hdrs = self.headers(headers)
        raw = json.dumps(body, ensure_ascii=False)
        ex = Exchange(
            id=uuid.uuid4().hex[:12],
            label=label,
            protocol=protocol,
            mode="stream" if stream else "nonstream",
            request=HttpRequestRecord(
                url=self.profile.url,
                headers=redact_headers(hdrs),
                body=self.redactor.scrub(body),
                body_bytes=len(raw.encode()),
            ),
        )
        timing = Timing()
        t0 = time.perf_counter()
        resp_rec = HttpResponseRecord()
        acc: StreamAccumulator | None = None
        try:
            async with asyncio.timeout(self.stream_timeout_s if stream else self.timeout_s + 5):
                req = self._http.build_request("POST", self.profile.url, content=raw.encode(), headers=hdrs)
                resp = await self._http.send(req, stream=True)
                try:
                    timing.ttfb_ms = _ms(t0)
                    resp_rec.status = resp.status_code
                    resp_rec.headers = redact_headers(resp.headers.items())
                    ctype = resp.headers.get("content-type", "")
                    resp_rec.content_type = ctype
                    if "text/event-stream" in ctype:
                        acc = StreamAccumulator(protocol, resp.status_code)
                        resp_rec.events = []
                        parser = SSEParser()
                        async for chunk in resp.aiter_text():
                            for ev in parser.feed(chunk):
                                self._record_event(ev.event, ev.data, t0, acc, resp_rec, timing)
                        for ev in parser.close():
                            self._record_event(ev.event, ev.data, t0, acc, resp_rec, timing)
                    else:
                        text = (await resp.aread()).decode("utf-8", errors="replace")
                        self._record_body(text, resp_rec)
                finally:
                    await resp.aclose()
        except TimeoutError:
            ex.transport_error = "stream_timeout" if resp_rec.status is not None else "timeout"
            if acc and acc.n.stream:
                acc.n.stream.timed_out = True
        except httpx.HTTPError as e:
            ex.transport_error = f"{type(e).__name__}: {self.redactor.text(str(e))}"
        timing.total_ms = _ms(t0)
        ex.timing = timing
        ex.response = resp_rec if resp_rec.status is not None else None
        if acc is not None:
            ex.normalized = acc.finish()
        elif resp_rec.status is not None:
            ex.normalized = normalize(protocol, resp_rec.status, resp_rec.body_json)
            if resp_rec.body_json is None and resp_rec.status >= 400:
                ex.normalized.error_message = (resp_rec.body_text or "")[:500]
        else:
            ex.normalized = NormalizedResponse()
        return ex

    def _record_body(self, text: str, rec: HttpResponseRecord) -> None:
        text = self.redactor.text(text)
        rec.truncated = len(text) > MAX_BODY_CHARS
        rec.body_text = text[:MAX_BODY_CHARS]
        try:
            rec.body_json = json.loads(text)
        except ValueError:
            rec.body_json = None

    def _record_event(
        self, event: str | None, data: str, t0: float, acc: StreamAccumulator, rec: HttpResponseRecord, timing: Timing
    ) -> None:
        data = self.redactor.text(data)
        parsed: Any = data
        if data != "[DONE]":
            try:
                parsed = json.loads(data)
            except ValueError:
                parsed = data
        t_ms = _ms(t0)
        if acc.add(event, parsed) and timing.ttft_ms is None:
            timing.ttft_ms = t_ms
        assert rec.events is not None
        if len(rec.events) < MAX_EVENTS:
            rec.events.append(SSEEventRecord(event=event, data=parsed, t_ms=t_ms))
        else:
            rec.events_truncated = True
