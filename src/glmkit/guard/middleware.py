# ruff: noqa: E501
"""``GuardMiddleware``: budgets, loop detector and destructive-command gate as a ``BridgeMiddleware``.

Enable it from the CLI (``glmkit bridge serve --guard [--guard-config guard.yaml]``) or in code::

    GuardMiddleware(GuardConfig(...)) -> ProxyConfig(..., middleware=[guard])

* ``before_request``: hard budget (session or key) -> ``Reject`` 402; loop streak -> inject a stop message and/or
  ``Reject`` 400 (per ``loop.action``); streamed OpenAI calls get ``stream_options.include_usage`` so spend is known.
* ``after_response`` / ``on_stream_event``: every tool call the model returned is checked against the policy
  before the client sees it. A denied (or, by default, 'ask') call is removed and replaced by a short assistant
  message; when nothing is left the finish reason becomes ``stop`` / ``end_turn``. OpenAI and Anthropic shapes.
* ``on_finish``: spend accounting from the final ``usage``; soft limit -> warning, hard -> next request blocked.

Everything is written to the hash-chained audit log; message content never is. Agent and session come from
``ctx.extras`` (``agent``, ``session_id``, ``key_id``, set by the host from ``x-glmkit-agent`` / ``x-glmkit-session``
/ a key fingerprint) and otherwise from ``body.user`` / ``metadata`` / a fingerprint of the conversation start.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from glmkit.bridge.core.middleware import BridgeMiddleware, Reject
from glmkit.guard import loop as loopmod
from glmkit.guard.audit import AuditLog
from glmkit.guard.budget import BudgetTracker
from glmkit.guard.config import GuardConfig
from glmkit.guard.policy import Decision, Policy, load_policy
from glmkit.guard.recorder import GuardRecorder

SOURCE = "bridge"


def block_text(tool: str, d: Decision) -> str:
    verb = "needs approval" if d.action == "ask" else "is not allowed"
    return (
        f"[glmkit-guard] The tool call `{tool}` was not run: it {verb} by policy rule `{d.rule}` ({d.reason}). "
        "Do not retry it unchanged. Choose a safer approach or ask the user to run it themselves."
    )


class GuardMiddleware(BridgeMiddleware):
    def __init__(self, config: GuardConfig | None = None, *, out_dir: Path | str | None = None) -> None:
        self.cfg = config or GuardConfig()
        self.policy: Policy | None = load_policy(self.cfg.policy) if self.cfg.policy_enabled else None
        self.audit = AuditLog(self.cfg.audit_path, store_args=self.cfg.log_args)
        self.budget = BudgetTracker(self.cfg.session_budget, self.cfg.key_budget, self.cfg.price_host)
        self.sessions_meta: dict[str, dict[str, Any]] = {}
        self.recorder: GuardRecorder | None = None
        if out_dir is not None:
            self.recorder = GuardRecorder(
                Path(out_dir),
                self.audit.path,
                {"guard": self.cfg.model_dump(mode="json"), "policy": self.policy.source if self.policy else None},
                self.snapshot,
            )

    # ------------------------------------------------------------------ identity
    def identify(self, ctx: Any, body: dict[str, Any]) -> dict[str, Any]:
        ex = ctx.extras
        if "guard" in ex:
            return ex["guard"]
        meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        session = ex.get("session_id") or body.get("user") or meta.get("session_id") or meta.get("user_id")
        if not session:
            first = next(
                (m for m in body.get("messages") or [] if isinstance(m, dict) and m.get("role") in ("system", "user")),
                {},
            )
            seed = json.dumps(
                [body.get("system"), first.get("content")], sort_keys=True, ensure_ascii=False, default=str
            )
            session = "conv-" + hashlib.sha256(seed.encode()).hexdigest()[:12]
        ident = {
            "session": str(session),
            "agent": ex.get("agent") or self.cfg.agent,
            "key": ex.get("key_id"),
            "model": ctx.model,
        }
        ex["guard"] = ident
        m = self.sessions_meta.setdefault(ident["session"], {"agent": ident["agent"], "model": ctx.model})
        m["agent"] = m.get("agent") or ident["agent"]
        m["model"] = ctx.model or m.get("model")
        return ident

    def _log(self, ctx: Any, ident: dict[str, Any], **kw: Any) -> None:
        self.audit.append(
            source=SOURCE, agent=ident["agent"], session=ident["session"], request_id=ctx.request_id, **kw
        )

    def _event(self, kind: str, action: str, ident: dict[str, Any], ctx: Any, **kw: Any) -> None:
        if self.recorder:
            self.recorder.event(
                kind, action, session=ident["session"], agent=ident["agent"], request_id=ctx.request_id, **kw
            )
            self.recorder.flush()

    # ------------------------------------------------------------------ request side
    def before_request(self, ctx: Any, body: dict[str, Any]) -> dict[str, Any] | Reject:
        ident = self.identify(ctx, body)
        hit = self.budget.hard_hit(ident["session"], ident["key"])
        if hit:
            scope, unit, s = hit
            b = self.budget.session_budget if scope == "session" else self.budget.key_budget
            limit = b.hard_usd if unit == "usd" else b.hard_tokens
            spent = f"${s.usd:.4f}" if unit == "usd" else f"{s.tokens} tokens"
            msg = f"glmkit guard: {scope} hard budget reached ({spent} of {limit}{' USD' if unit == 'usd' else ' tokens'}). Request blocked."
            self._log(
                ctx,
                ident,
                event="budget",
                decision="block",
                reason=f"{scope} hard {unit} budget reached",
                extra={"spent": spent},
            )
            self._event("budget", "block", ident, ctx, reason=msg)
            return Reject(402, msg, "guard_budget_exceeded", "insufficient_quota", fix_id="guard-budget-hard")
        out = body
        if body.get("stream") and "stream_options" not in body and ctx.extras.get("protocol", "openai") == "openai":
            out = {**body, "stream_options": {"include_usage": True}}  # spend needs the final usage chunk
        lc = self.cfg.loop
        if lc.enabled:
            v = loopmod.detect(body.get("messages") or [], similarity=lc.similarity)
            if v.hit(lc.repeat_threshold):
                block = lc.action == "block" or (
                    lc.action == "inject_then_block" and v.streak >= lc.repeat_threshold + lc.escalate_after
                )
                info = {"streak": v.streak, "loop_kind": v.kind, "threshold": lc.repeat_threshold}
                if block:
                    msg = (
                        f"glmkit guard: loop stopped. `{v.tool}` failed {v.streak} times in a row with the same "
                        "arguments or error. Change the approach or ask the user before continuing."
                    )
                    self._log(
                        ctx,
                        ident,
                        event="loop",
                        decision="block",
                        tool=v.tool,
                        reason=f"{v.kind} x{v.streak}",
                        extra=info,
                    )
                    self._event("loop", "block", ident, ctx, tool=v.tool, reason=f"{v.kind} x{v.streak}", **info)
                    return Reject(400, msg, "guard_loop_blocked", "invalid_request_error", fix_id="guard-loop-block")
                self._log(
                    ctx, ident, event="loop", decision="inject", tool=v.tool, reason=f"{v.kind} x{v.streak}", extra=info
                )
                self._event("loop", "inject", ident, ctx, tool=v.tool, reason=f"{v.kind} x{v.streak}", **info)
                ctx.emit(
                    "guard-loop-inject", f"{v.tool} failed x{v.streak}", "stop/rethink message injected", details=info
                )
                out = loopmod.inject_message(out, v)
        return out

    # ------------------------------------------------------------------ tool-call gate
    def _judge(self, ctx: Any, name: str, args: Any) -> Decision | None:
        """None = allowed. Otherwise the Decision that was enforced (action 'ask' only when ask_action=block)."""
        ident = ctx.extras["guard"]
        d = self.policy.evaluate(name, args) if self.policy else Decision()
        enforced = d.action == "deny" or (d.action == "ask" and self.cfg.ask_action == "block")
        self._log(
            ctx,
            ident,
            event="tool_call",
            decision=d.action,
            reason=d.reason if d.action != "allow" else "no rule matched",
            tool=name,
            rule=d.rule,
            args=args if isinstance(args, (dict, list)) else _parse(args),
            extra={"enforced": "block" if enforced else "allow"} if d.action == "ask" else None,
        )
        if d.action == "allow":
            return None
        if enforced:
            self._event("command", d.action, ident, ctx, tool=name, rule=d.rule, reason=d.reason, enforced="block")
            ctx.emit(
                "guard-command-" + ("blocked" if d.action == "deny" else "ask"),
                f"{name} -> {d.rule}",
                "tool call removed from the response",
                details={"tool": name, "rule": d.rule},
                ok=False,
            )
            return d
        self._event("command", "ask", ident, ctx, tool=name, rule=d.rule, reason=d.reason, enforced="allow")
        return None

    def after_response(self, ctx: Any, body: dict[str, Any]) -> dict[str, Any] | Reject:
        self.identify(ctx, {})
        for ch in body.get("choices") or []:  # OpenAI
            msg = ch.get("message") or {}
            kept, notes = [], []
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                d = self._judge(ctx, fn.get("name", ""), fn.get("arguments"))
                if d:
                    notes.append(block_text(fn.get("name", ""), d))
                else:
                    kept.append(tc)
            if notes and "tool_calls" in msg:
                msg["content"] = "\n".join(filter(None, [msg.get("content"), *notes]))
                if kept:
                    msg["tool_calls"] = kept
                else:
                    msg.pop("tool_calls", None)
                    if ch.get("finish_reason") == "tool_calls":
                        ch["finish_reason"] = "stop"
        if isinstance(body.get("content"), list):  # Anthropic Messages response
            blocks, dropped = [], 0
            for b in body["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    d = self._judge(ctx, b.get("name", ""), b.get("input"))
                    if d:
                        blocks.append({"type": "text", "text": block_text(b.get("name", ""), d)})
                        dropped += 1
                        continue
                blocks.append(b)
            body["content"] = blocks
            if dropped and not any(b.get("type") == "tool_use" for b in blocks if isinstance(b, dict)):
                body["stop_reason"] = "end_turn"
        return body

    def on_stream_event(self, ctx: Any, event: Any) -> list[Any] | Reject:
        if not isinstance(event, dict):
            return [event]
        self.identify(ctx, {})
        st = ctx.extras.setdefault("guard_stream", {"blocked": set(), "allowed": 0, "dropped": 0, "blocks": {}})
        if "choices" in event:
            return self._openai_event(ctx, event, st)
        if "type" in event:
            return self._anthropic_event(ctx, event, st)
        return [event]

    def _openai_event(self, ctx: Any, event: dict[str, Any], st: dict[str, Any]) -> list[Any]:
        choices = event.get("choices") or []
        if not choices:
            return [event]
        ch = choices[0]
        delta = ch.get("delta") or {}
        tcs = delta.get("tool_calls")
        if tcs:
            kept, notes = [], []
            for tc in tcs:
                idx = tc.get("index", 0)
                fn = tc.get("function") or {}
                if idx in st["blocked"]:
                    continue
                if not fn.get("name"):
                    kept.append(tc)  # a bare argument fragment of an allowed call
                    continue
                d = self._judge(ctx, fn["name"], fn.get("arguments"))
                if d:
                    st["blocked"].add(idx)
                    st["dropped"] += 1
                    notes.append(block_text(fn["name"], d))
                else:
                    st["allowed"] += 1
                    kept.append(tc)
            if len(kept) != len(tcs):
                new = {**event, "choices": [{**ch, "delta": {k: v for k, v in delta.items() if k != "tool_calls"}}]}
                nd = new["choices"][0]["delta"]
                if kept:
                    nd["tool_calls"] = kept
                if notes:
                    nd["content"] = "\n".join([*((nd.get("content") and [nd["content"]]) or []), *notes])
                return [new] if (nd.get("tool_calls") or nd.get("content")) else []
        if ch.get("finish_reason") == "tool_calls" and st["dropped"] and not st["allowed"]:
            return [{**event, "choices": [{**ch, "finish_reason": "stop"}, *choices[1:]]}]
        return [event]

    def _anthropic_event(self, ctx: Any, ev: dict[str, Any], st: dict[str, Any]) -> list[Any]:
        t, idx = ev.get("type"), ev.get("index")
        blocks: dict[int, dict[str, Any]] = st["blocks"]
        if t == "content_block_start" and (ev.get("content_block") or {}).get("type") == "tool_use":
            blocks[idx] = {"events": [ev], "name": ev["content_block"].get("name", ""), "json": []}
            return []
        if idx in blocks and t == "content_block_delta":
            blocks[idx]["events"].append(ev)
            blocks[idx]["json"].append((ev.get("delta") or {}).get("partial_json", ""))
            return []
        if idx in blocks and t == "content_block_stop":
            b = blocks.pop(idx)
            raw = "".join(b["json"])
            d = self._judge(ctx, b["name"], raw or "{}")
            if not d:
                st["allowed"] += 1
                return [*b["events"], ev]
            st["dropped"] += 1
            return [
                {"type": "content_block_start", "index": idx, "content_block": {"type": "text", "text": ""}},
                {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "text_delta", "text": block_text(b["name"], d)},
                },
                ev,
            ]
        if t == "message_delta" and st["dropped"] and not st["allowed"]:
            return [{**ev, "delta": {**(ev.get("delta") or {}), "stop_reason": "end_turn"}}]
        return [ev]

    # ------------------------------------------------------------------ spend
    def on_finish(self, ctx: Any, usage: dict[str, Any] | None) -> None:
        ident = ctx.extras.get("guard")
        if ident is None:
            return
        res = self.budget.record(ident["session"], ident["key"], ctx.model, usage, ctx.request_id)
        for which in ("soft", "hard"):
            for scope, unit in res[which]:
                lim = getattr(
                    self.cfg.session_budget if scope == "session" else self.cfg.key_budget,
                    f"{which}_{'usd' if unit == 'usd' else 'tokens'}",
                )
                reason = f"{scope} {which} {unit} budget reached (limit {lim})"
                self._log(
                    ctx, ident, event="budget", decision="warn" if which == "soft" else "limit-reached", reason=reason
                )
                self._event("budget", "warn" if which == "soft" else "limit-reached", ident, ctx, reason=reason)
                if which == "soft":
                    ctx.emit("guard-budget-soft", f"{scope} {unit}", "warning", details={"limit": lim})
        if self.recorder:
            self.recorder.flush()

    # ------------------------------------------------------------------ dashboard data
    def snapshot(self) -> dict[str, Any]:
        sessions = []
        for sid, s in self.budget.sessions.items():
            meta = self.sessions_meta.get(sid, {})
            b = self.cfg.session_budget
            sessions.append(
                {
                    "session": sid,
                    "agent": meta.get("agent"),
                    "model": meta.get("model"),
                    "requests": s.requests,
                    "tokens": s.tokens,
                    "usd": s.usd,
                    "unpriced_calls": s.unpriced_calls,
                    "budget": b.model_dump(),
                    "burn": s.burn,
                }
            )
        return {"sessions": sessions, "price_host": self.cfg.price_host}


def _parse(args: Any) -> Any:
    if isinstance(args, str):
        try:
            return json.loads(args)
        except ValueError:
            return args
    return args
