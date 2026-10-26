"""Deterministic synthetic conversations: the GLM-5#168 styled-reasoning replay, a prose control, and a
bash-tool agent environment for loops and soak runs. No randomness, so a run is reproducible."""

from __future__ import annotations

from typing import Any

BASH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command in the workspace and return its output.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The command to run"}},
            "required": ["command"],
        },
    },
}

COMMANDS = ["ls -la", "pwd", "git status --short", "cat README.md", "grep -rn TODO src", "python -m pytest -q"]
OUTPUTS = [
    "total 8\ndrwxr-xr-x 2 dev dev 4096 src\n-rw-r--r-- 1 dev dev 120 README.md",
    "/workspace/project",
    " M src/app.py",
    "Project readme: a small service.",
    "src/app.py:12: TODO tidy",
    "5 passed in 0.31s",
]

_STYLED_CYCLE = ["=", "the", "=", "result", "the", "=", "check", "the", "=", "step", "the"]
_PROSE = [
    "I will look at the workspace first and then decide which file needs a change.",
    "The last command succeeded, so I can move on to inspecting the next file.",
    "Before editing anything I want to confirm what the tests currently report.",
    "Nothing in this output looks unexpected, so I will continue with the plan.",
    "A short summary of what I found will help the user follow along.",
]


def styled_reasoning(turn: int, words: int = 60) -> str:
    """Reasoning in the drifted style of GLM-5#168: about 62% of the words are ``=`` or ``the``."""
    return " ".join(_STYLED_CYCLE[(turn + i) % len(_STYLED_CYCLE)] for i in range(words))


def prose_reasoning(turn: int, words: int = 60) -> str:
    """Healthy prose reasoning of roughly ``words`` words with an ordinary share of 'the'."""
    out: list[str] = []
    i = turn
    while len(out) < words:
        out += _PROSE[i % len(_PROSE)].split()
        i += 1
    return " ".join(out[:words])


def command(turn: int) -> str:
    return COMMANDS[turn % len(COMMANDS)]


def tool_output(turn: int) -> str:
    return OUTPUTS[turn % len(OUTPUTS)]


def replay_history(style: str, turns: int = 8, words: int = 60) -> list[dict[str, Any]]:
    """``turns`` assistant turns, each with ``reasoning_content`` in ``style`` ('styled' | 'prose'), a bash tool
    call and a tool result, ending with a user message that asks for the next step."""
    make = styled_reasoning if style == "styled" else prose_reasoning
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": "You are a careful coding agent. Use the bash tool to inspect the workspace."},
        {"role": "user", "content": "Check the repository state and report what you find."},
    ]
    for i in range(turns):
        cid = f"call_hist_{i}"
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": make(i, words),
                "tool_calls": [
                    {
                        "id": cid,
                        "type": "function",
                        "function": {"name": "bash", "arguments": f'{{"command": "{command(i)}"}}'},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": cid, "content": tool_output(i)})
    msgs.append({"role": "user", "content": "Summarise what you found in one short sentence."})
    return msgs


def prefix_text(tokens: int) -> str:
    """Filler of about ``tokens`` tokens (4 chars per token, the mock's rule, a fair estimate for English)."""
    lines, size, i = [], 0, 0
    while size < tokens * 4:
        line = f"Reference line {i:04d}: the service keeps record {i * 7 % 1000:03d} in shard {i % 16:02d}."
        lines.append(line)
        size += len(line) + 1
        i += 1
    return "\n".join(lines)[: tokens * 4]
