"""Destructive-command policy: allow / deny / ask per pattern, evaluated on tool-call arguments.

See ``default_policy.yaml`` for the rule format. Shell commands are tokenised, not just grepped: quoted text is
data (``echo "rm -rf /"`` is an ``echo``), wrappers (``sudo``, ``env``, ``nohup``, ``bash -c``) are unwrapped and
``$(...)`` / backtick substitutions are checked as commands of their own.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

Action = Literal["allow", "deny", "ask"]
DEFAULT_POLICY = Path(__file__).with_name("default_policy.yaml")
_RANK = {"allow": 0, "ask": 1, "deny": 2}
WRAPPERS = {"sudo", "doas", "nohup", "time", "nice", "command", "exec", "builtin", "ionice", "env", "timeout", "xargs"}
WRAPPER_ARG_FLAGS: dict[str, set[str]] = {
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-r", "-t"},
    "doas": {"-u", "-C"},
    "env": {"-u", "-C"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-s"},
}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
MAX_DEPTH = 4


@dataclass(frozen=True)
class Rule:
    id: str
    action: Action
    regex: re.Pattern[str]
    kind: Literal["command", "text"] = "command"
    description: str = ""
    unless: re.Pattern[str] | None = None
    tools: tuple[str, ...] = ()

    def applies_to(self, tool: str) -> bool:
        return not self.tools or any(fnmatch.fnmatch(tool, g) for g in self.tools)

    def hits(self, s: str) -> bool:
        return bool(self.regex.search(s)) and not (self.unless and self.unless.search(s))


@dataclass
class Decision:
    action: Action = "allow"
    rule: str | None = None
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return self.action != "allow"


@dataclass
class Policy:
    rules: list[Rule]
    shell_tools: list[str] = field(default_factory=list)
    command_keys: list[str] = field(default_factory=list)
    text_keys: list[str] = field(default_factory=list)
    source: str = "default"

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, tool: str, args: Any) -> Decision:
        """Strictest decision over every command / text field of one tool call."""
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"command": args} if self._is_shell(tool) else {}
        best = Decision()
        for kind, value in self._fields(tool, args):
            d = self.check_command(tool, value) if kind == "command" else self.check_text(tool, value)
            if _RANK[d.action] > _RANK[best.action]:
                best = d
        return best

    def check_command(self, tool: str, command: str, _depth: int = 0) -> Decision:
        best = self._first(tool, "text", command)
        for seg in split_segments(command, _depth):
            if isinstance(seg, Nested):
                d = self.check_command(tool, seg.command, _depth + 1) if _depth < MAX_DEPTH else Decision()
            else:
                d = self._first(tool, "command", seg)
            if _RANK[d.action] > _RANK[best.action]:
                best = d
        return best

    def check_text(self, tool: str, text: str) -> Decision:
        return self._first(tool, "text", text)

    def _first(self, tool: str, kind: str, s: str) -> Decision:
        for r in self.rules:
            if r.kind == kind and r.applies_to(tool) and r.hits(s):
                if r.action == "allow":
                    return Decision("allow", r.id, r.description)
                return Decision(r.action, r.id, r.description)
        return Decision()

    # ------------------------------------------------------------------ field extraction
    def _is_shell(self, tool: str) -> bool:
        t = tool.lower()
        return any(fnmatch.fnmatch(t, g) for g in self.shell_tools)

    def _fields(self, tool: str, args: Any) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        shell = self._is_shell(tool)

        def walk(v: Any, key: str | None) -> None:
            k = (key or "").lower()
            if isinstance(v, dict):
                for kk, vv in v.items():
                    walk(vv, str(kk))
            elif isinstance(v, list) and v and all(isinstance(x, str) for x in v) and (k in self.command_keys or shell):
                joined = _join_argv(v) if k == "command" or k == "cmd" else " ; ".join(v)
                out.append(("command", joined))
            elif isinstance(v, list):
                for x in v:
                    walk(x, key)
            elif isinstance(v, str):
                if k in self.command_keys or shell:
                    out.append(("command", v))
                elif k in self.text_keys:
                    out.append(("text", v))

        walk(args, None)
        return out


def _join_argv(argv: list[str]) -> str:
    import shlex

    return shlex.join(argv)


@dataclass
class Nested:
    command: str


# ---------------------------------------------------------------------------------------- shell tokenising
def _scan(cmd: str) -> list[list[str]]:
    """Split on unquoted ; && || | & and newlines; return token lists with quotes removed."""
    segs: list[list[str]] = []
    toks: list[str] = []
    cur: list[str] = []
    have = False
    i, n = 0, len(cmd)
    q: str | None = None

    def end_tok() -> None:
        nonlocal cur, have
        if have:
            toks.append("".join(cur))
        cur, have = [], False

    def end_seg() -> None:
        nonlocal toks
        end_tok()
        if toks:
            segs.append(toks)
        toks = []

    while i < n:
        c = cmd[i]
        if q == "'":
            if c == "'":
                q = None
            else:
                cur.append(c)
        elif q == '"':
            if c == "\\" and i + 1 < n and cmd[i + 1] in '"\\$`':
                cur.append(cmd[i + 1])
                i += 1
            elif c == '"':
                q = None
            else:
                cur.append(c)
        elif c == "\\" and i + 1 < n:
            cur.append(cmd[i + 1])
            have = True
            i += 1
        elif c in "'\"":
            q, have = c, True
        elif c in ";\n&|":
            end_seg()
            while i + 1 < n and cmd[i + 1] in "&|" and c in "&|":
                i += 1
        elif c in "()":
            end_seg()
        elif c in " \t":
            end_tok()
        else:
            cur.append(c)
            have = True
        i += 1
    end_seg()
    return segs


_SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


def _unwrap(tokens: list[str]) -> tuple[list[str], str | None]:
    """Drop VAR=x prefixes and wrapper programs; return (tokens, nested command for `bash -c`/`eval`)."""
    t = list(tokens)
    for _ in range(8):
        while t and re.match(r"^[A-Za-z_]\w*=", t[0]):
            t.pop(0)
        if not t:
            break
        prog = t[0].rsplit("/", 1)[-1]
        if prog in WRAPPERS:
            skip = WRAPPER_ARG_FLAGS.get(prog, set())
            t.pop(0)
            while t and t[0].startswith("-"):
                flag = t.pop(0)
                if flag in skip and t:
                    t.pop(0)
            if prog == "timeout" and t and re.match(r"^[\d.]+[smhd]?$", t[0]):
                t.pop(0)
            if prog == "env":
                continue
            continue
        if prog in SHELLS:
            for j, tok in enumerate(t[1:], 1):
                if tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:] and j + 1 < len(t):
                    return t, t[j + 1]
            break
        if prog == "eval" and len(t) > 1:
            return t, " ".join(t[1:])
        break
    if t:
        t[0] = t[0].rsplit("/", 1)[-1] if "/" in t[0] and not t[0].startswith("-") else t[0]
    return t, None


def split_segments(command: str, depth: int = 0) -> list[str | Nested]:
    """Normalised shell segments (strings) plus nested commands to evaluate recursively."""
    out: list[str | Nested] = []
    for m in _SUBST.finditer(command):
        inner = m.group(1) or m.group(2)
        if inner:
            out.append(Nested(inner))
    stripped = _SUBST.sub(" ", command)
    for toks in _scan(stripped):
        t, nested = _unwrap(toks)
        if nested:
            out.append(Nested(nested))
        if t:
            out.append(" ".join(t))
    return out


# ---------------------------------------------------------------------------------------- loading
def _rule(d: dict[str, Any]) -> Rule:
    action = d.get("action", "ask")
    if action not in _RANK:
        raise ValueError(f"rule {d.get('id')!r}: action must be allow, deny or ask")
    kind = d.get("kind", "command")
    if kind not in ("command", "text"):
        raise ValueError(f"rule {d.get('id')!r}: kind must be command or text")
    return Rule(
        id=d["id"],
        action=action,
        regex=re.compile(d["regex"]),
        kind=kind,
        description=d.get("description", ""),
        unless=re.compile(d["unless"]) if d.get("unless") else None,
        tools=tuple(d.get("tools") or ()),
    )


def _from_dict(data: dict[str, Any], source: str, base: Policy | None) -> Policy:
    rules = [_rule(r) for r in data.get("rules") or []]
    if base is not None:
        return Policy(
            rules=rules + base.rules,
            shell_tools=[*data.get("shell_tools", []), *base.shell_tools],
            command_keys=[*data.get("command_keys", []), *base.command_keys],
            text_keys=[*data.get("text_keys", []), *base.text_keys],
            source=source,
        )
    return Policy(
        rules=rules,
        shell_tools=list(data.get("shell_tools", [])),
        command_keys=list(data.get("command_keys", [])),
        text_keys=list(data.get("text_keys", [])),
        source=source,
    )


def load_policy(path: Path | str | None = None) -> Policy:
    """The shipped default policy, or a custom file (``include_default: true`` keeps the defaults behind it)."""
    default = _from_dict(yaml.safe_load(DEFAULT_POLICY.read_text(encoding="utf-8")), "default", None)
    if path is None:
        return default
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return _from_dict(data, str(p), default if data.get("include_default") else None)
