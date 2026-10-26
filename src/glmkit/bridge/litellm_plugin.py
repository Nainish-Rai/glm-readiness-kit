"""LiteLLM plugin: the GLM bridge fixes as a ``CustomLogger``. Optional extra: ``pip install 'glmkit[litellm]'``.

    # config.yaml
    litellm_settings:
      callbacks: glmkit.bridge.litellm_plugin.plugin

Settings come from the environment (a YAML callback takes no arguments): ``GLMKIT_BRIDGE_FLAVOR``,
``GLMKIT_BRIDGE_OUT`` (where the Bridge tab's session result is written), ``GLMKIT_BRIDGE_LOG_CONTENT``,
``GLMKIT_BRIDGE_MODELS``. See ``docs/bridge-litellm.md`` for which fixes run where, and a ready config in
``examples/litellm/config.yaml``. The logic lives in ``glmkit.bridge.litellm_adapter`` (no LiteLLM import).
"""

from __future__ import annotations

import atexit
import copy
import json
import logging
import threading
from collections.abc import AsyncGenerator
from typing import Any

try:
    from litellm.integrations.custom_logger import CustomLogger
except ImportError as e:  # pragma: no cover - exercised through a subprocess test
    raise ImportError(
        "glmkit.bridge.litellm_plugin needs LiteLLM, which is an optional extra. "
        "Install it with:  pip install 'glmkit[litellm]'   (or: uv add 'glmkit[litellm]')"
    ) from e

from glmkit.bridge.core.stream import DONE
from glmkit.bridge.litellm_adapter import RETRY_FLAG, LiteLLMBridge

FLUSH_EVERY_S = 1.0
log = logging.getLogger("glmkit.bridge.litellm")


class GLMBridgePlugin(CustomLogger):
    def __init__(self, bridge: LiteLLMBridge | None = None) -> None:
        super().__init__()
        self.bridge = bridge or LiteLLMBridge.from_env()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        atexit.register(self.close)

    # -- session file
    def _ensure_flusher(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="glmkit-bridge-flush", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(FLUSH_EVERY_S):
            try:
                self.bridge.flush()
            except Exception as e:  # the session file must never take down the gateway
                log.warning("glmkit bridge: session file not written (%s)", type(e).__name__)

    def close(self) -> None:
        self._stop.set()
        try:
            self.bridge.flush(final=True)
        except Exception as e:
            log.warning("glmkit bridge: final session file not written (%s)", type(e).__name__)

    # -- request: forced thinking, effort clamp, schema flatten, reasoning replay
    async def async_pre_call_deployment_hook(self, kwargs: dict[str, Any], call_type: Any) -> dict | None:
        self._ensure_flusher()
        if not _is_chat(call_type):
            return None  # e.g. the outer anthropic_messages call: its inner chat call (translated) is rewritten
        return self.bridge.rewrite_kwargs(kwargs)

    # -- non-stream response: tool-argument repair (and schema unflatten)
    async def async_post_call_success_deployment_hook(self, request_data: dict, response: Any, call_type: Any) -> Any:
        if not _is_chat(call_type):
            return None
        return self.bridge.fix_response(request_data, response)

    # -- the one retry with the parse error shown to the model (LiteLLM's chat-completion agentic loop)
    async def async_should_run_chat_completion_agentic_loop(
        self,
        response: Any,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        stream: bool,
        custom_llm_provider: str,
        kwargs: dict,
    ) -> tuple[bool, dict]:
        if stream:
            return False, {}
        full = {**kwargs, "model": f"{custom_llm_provider}/{model}" if "/" not in model else model}
        plan = self.bridge.failures_in(response, full, messages)
        return (True, {"plan": plan}) if plan is not None else (False, {})

    async def async_run_chat_completion_agentic_loop(
        self,
        tools: dict,
        model: str,
        messages: list[dict],
        response: Any,
        optional_params: dict,
        logging_obj: Any,
        stream: bool,
        kwargs: dict,
    ) -> Any:
        import litellm

        plan = tools["plan"]
        keep = {
            k: v
            for k, v in optional_params.items()
            if k in ("tools", "tool_choice", "extra_body", "temperature", "top_p", "max_tokens", "stop")
        }
        call: dict[str, Any] = {
            "model": model,
            "messages": plan.request["messages"],
            "custom_llm_provider": kwargs.get("custom_llm_provider"),
            "api_base": kwargs.get("api_base"),
            "api_key": kwargs.get("api_key"),
            "metadata": {RETRY_FLAG: True},
            **keep,
        }
        return await litellm.acompletion(**{k: v for k, v in call.items() if v is not None})

    # -- proxy-level: turn a final unrepairable result into an error instead of handing the client garbage
    async def async_post_call_success_hook(self, data: dict, user_api_key_dict: Any, response: Any) -> Any:
        tracked = self.bridge.live.pop(self.bridge.call_id(data))
        if tracked is not None and tracked.ctx.extras.get("unrepaired"):
            from fastapi import HTTPException

            raise HTTPException(status_code=502, detail=tracked.ctx.extras["unrepaired"]["error"])
        return response

    # -- stream: stateful tool-argument buffering per index (proxy only)
    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict: Any, response: Any, request_data: dict
    ) -> AsyncGenerator[Any, None]:
        tracked = self.bridge.live.get(self.bridge.call_id(request_data))
        if tracked is None:
            async for item in response:
                yield item
            return
        core = self.bridge.core_for_ctx(tracked.ctx)
        rw = core.new_stream(tracked.ctx)
        failed = False
        last: Any = None
        try:
            async for chunk in response:
                last = chunk
                d = _chunk_dict(chunk)
                before = copy.deepcopy(d)
                for out in rw.feed(d):
                    # untouched chunks go out as LiteLLM's own object; edited or synthesised ones are rebuilt
                    yield chunk if out is d and d == before else _to_chunk(chunk, out)
            for out in rw.close():
                if out != DONE:
                    yield _to_chunk(last, out)
            if rw.needs_retry:
                released = await self.bridge.retry_stream(tracked, rw, _complete)
                if released is None:
                    failed = True
                    # the proxy ends a stream on a "data: ..." string chunk: error frame, then [DONE]
                    yield "data: " + json.dumps({"error": tracked.ctx.extras["unrepaired"]["error"]})
                    return
                for out in released:
                    if out != DONE:
                        yield _to_chunk(last, out)
        finally:
            self.bridge.finish(tracked, 502 if failed else 200, rw.usage)
            self.bridge.live.pop(self.bridge.call_id(request_data))

    # -- errors: Z.ai business codes to honest status codes
    async def async_post_call_failure_hook(
        self,
        request_data: dict,
        original_exception: Exception,
        user_api_key_dict: Any,
        traceback_str: str | None = None,
    ) -> Any:
        mapped = self.bridge.map_failure(request_data, original_exception)
        if mapped is None:
            return None
        from fastapi import HTTPException

        status, error, headers = mapped
        return HTTPException(status_code=status, detail=error, headers=headers)


def _is_chat(call_type: Any) -> bool:
    return getattr(call_type, "value", call_type) in ("acompletion", "completion")


async def _complete(body: dict[str, Any], conn: dict[str, Any]) -> dict[str, Any]:
    """The one non-stream re-ask of a stream whose tool arguments were unusable."""
    import litellm

    extra = {k: body[k] for k in ("thinking", "reasoning_effort") if k in body}
    call = {
        **conn,
        "messages": body["messages"],
        "tools": body.get("tools"),
        "tool_choice": body.get("tool_choice"),
        "extra_body": extra or None,
        "metadata": {RETRY_FLAG: True},
    }
    resp = await litellm.acompletion(**{k: v for k, v in call.items() if v is not None})
    return resp.model_dump()


def _chunk_dict(chunk: Any) -> dict[str, Any]:
    return chunk.model_dump(exclude_none=True) if hasattr(chunk, "model_dump") else dict(chunk)


def _to_chunk(template: Any, out: Any) -> Any:
    """A rewriter-synthesised chunk (one head+arguments delta) as the same type LiteLLM streams."""
    if isinstance(out, str) or template is None:
        return out
    return type(template)(**{k: v for k, v in out.items() if v is not None})


plugin = GLMBridgePlugin()
