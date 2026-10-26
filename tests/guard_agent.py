"""A scripted runaway agent for the guard: it keeps running a failing shell command whatever the model says.

The mock replies with the same ``shell`` tool call to every request whose last user message matches RUNAWAY (or the
guard's injected stop message, so the agent 'ignores' the injection). Used by the guard tests and
``tools/guard-proof/proof.py``.
"""

from __future__ import annotations

import json
from typing import Any

import openai

SHELL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "shell",
        "description": "Run a shell command",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    },
}
FAIL_OUTPUT = "make: *** No rule to make target 'test'.  Stop.\nexit code: 2"
OUTPUT_TOKENS = 1500  # each scripted reply bills this many completion tokens


def rules(*, obedient: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if obedient:  # an agent that heeds the injected stop message
        out.append(
            {
                "name": "obey",
                "match": {"last_user_regex": "glmkit-guard"},
                "reply": {"content": "Stopping: the build target is missing."},
            }
        )
    out += [
        {
            "name": "runaway",
            "match": {"last_user_regex": "RUNAWAY|glmkit-guard"},
            "reply": {
                "tool_calls": [{"name": "shell", "arguments": {"command": "make test"}}],
                "usage": {"completion_tokens": OUTPUT_TOKENS},
            },
        },
        {
            "name": "danger",
            "match": {"last_user_regex": "DANGER"},
            "reply": {"tool_calls": [{"name": "shell", "arguments": {"command": "rm -rf /"}}]},
        },
        {
            "name": "careful",
            "match": {"last_user_regex": "CAREFUL"},
            "reply": {
                "tool_calls": [
                    {"name": "shell", "arguments": {"command": "git push --force-with-lease origin feature"}}
                ]
            },
        },
    ]
    return out


def run_agent(
    base_url: str,
    prompt: str = "RUNAWAY: run the build",
    *,
    max_turns: int = 40,
    stream: bool = False,
    headers: dict[str, str] | None = None,
    model: str = "glm-5.3",
) -> dict[str, Any]:
    """Loop until the model stops calling tools, the bridge refuses (HTTP error) or ``max_turns``."""
    client = openai.OpenAI(base_url=base_url, api_key="sk-test-not-a-real-key", max_retries=0, default_headers=headers)
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    executed: list[str] = []
    out: dict[str, Any] = {
        "executed": executed,
        "stopped_by": "cap",
        "turns": 0,
        "final": None,
        "error": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    for turn in range(max_turns):
        out["turns"] = turn + 1
        try:
            if stream:
                msg = _stream_message(client, model, messages)
            else:
                resp = client.chat.completions.create(model=model, messages=messages, tools=[SHELL_TOOL])
                m = resp.choices[0].message
                if resp.usage:
                    out["prompt_tokens"] += resp.usage.prompt_tokens
                    out["completion_tokens"] += resp.usage.completion_tokens
                msg = {"role": "assistant", "content": m.content}
                if m.tool_calls:
                    msg["tool_calls"] = [tc.model_dump() for tc in m.tool_calls]
        except openai.APIStatusError as e:
            out["stopped_by"] = f"http-{e.status_code}"
            out["error"] = (e.body or {}).get("code") if isinstance(e.body, dict) else None
            out["message"] = str(e.message)
            return out
        messages.append(msg)
        if not msg.get("tool_calls"):
            out["stopped_by"], out["final"] = "model", msg.get("content")
            return out
        for tc in msg["tool_calls"]:
            cmd = json.loads(tc["function"]["arguments"]).get("command", "")
            executed.append(cmd)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": FAIL_OUTPUT})
    return out


def _stream_message(client: openai.OpenAI, model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    calls: dict[int, dict[str, Any]] = {}
    text = ""
    for chunk in client.chat.completions.create(model=model, messages=messages, tools=[SHELL_TOOL], stream=True):
        if not chunk.choices:
            continue
        d = chunk.choices[0].delta
        text += d.content or ""
        for tc in d.tool_calls or []:
            c = calls.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
            c["id"] = tc.id or c["id"]
            if tc.function:
                c["name"] += tc.function.name or ""
                c["arguments"] += tc.function.arguments or ""
    msg: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        msg["tool_calls"] = [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
            for _, c in sorted(calls.items())
        ]
    return msg
