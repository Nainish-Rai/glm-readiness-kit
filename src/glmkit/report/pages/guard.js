/* Guard tab: budget burn-down per session, loop interventions, blocked commands, audit-chain status. */
GLMKit.registerPage("guard", {
  render(panel, data, kit) {
    const { O, el } = kit;
    const stx = (attrs, text) => { const n = O.sv("text", attrs); n.textContent = text; return n; };
    const sessions = data.sessions || [];
    const events = data.events || [];
    const usd = (v) => (v == null ? "not logged" : "$" + (Math.abs(v) < 1 ? v.toFixed(4) : v.toFixed(2)));
    const card = (cls, title, hint, ...body) => el("div", { class: "card " + cls },
      el("div", { class: "card-h" }, el("h2", {}, title), el("span", { class: "hint" }, hint)), ...body);

    /* ---- budget burn-down ------------------------------------------------------------------ */
    const sel = { id: (sessions.slice().sort((a, b) => (b.usd || 0) - (a.usd || 0))[0] || {}).session };
    const chartHost = el("div", { class: "card-b" });
    const summary = el("p", { class: "summary", "aria-live": "polite" });
    const pick = el("div", { class: "card-b sess-pick" });

    function limits(s) {
      const b = s.budget || {};
      if (b.hard_usd != null) return { unit: "usd", hard: b.hard_usd, soft: b.soft_usd };
      if (b.hard_tokens != null) return { unit: "tokens", hard: b.hard_tokens, soft: b.soft_tokens };
      return { unit: s.usd ? "usd" : "tokens", hard: null, soft: null };
    }
    function drawChart() {
      const s = sessions.find((x) => x.session === sel.id);
      if (!s) { chartHost.replaceChildren(O.emptyState("No guarded session logged.")); summary.textContent = ""; return; }
      const L = limits(s);
      const key = L.unit === "usd" ? "cum_usd" : "cum_tokens";
      const fmt = L.unit === "usd" ? usd : O.fmtN;
      const tick = (v) => (L.unit === "usd" ? "$" + v.toFixed(3) : O.fmtK(v));
      const burn = (s.burn || []).filter((p) => p[key] != null);
      const used = L.unit === "usd" ? s.usd : s.tokens;
      const W = 640, H = 260, pl = 64, pr = 16, pt = 16, pb = 32;
      const svg = O.sv("svg", { class: "burn", viewBox: `0 0 ${W} ${H}`, role: "img",
        "aria-label": `Budget for session ${s.session}: ${burn.length} requests, ${fmt(used)} used` });
      const n = Math.max(burn.length, 1);
      const top = L.hard != null ? L.hard : Math.max(...burn.map((p) => p[key]), 1);
      const x = (i) => pl + (n === 1 ? 0 : (i / (n - 1)) * (W - pl - pr));
      const val = (p) => (L.hard != null ? Math.max(L.hard - p[key], 0) : p[key]);
      const y = (v) => pt + (1 - v / top) * (H - pt - pb);
      for (let g = 0; g <= 4; g++) {
        const gv = (top * g) / 4;
        svg.append(O.sv("line", { class: "grid-line", x1: pl, x2: W - pr, y1: y(gv), y2: y(gv) }),
          stx({ class: "axis-text", x: pl - 6, y: y(gv) + 4, "text-anchor": "end" }, tick(gv)));
      }
      if (L.hard != null && L.soft != null) {
        const ys = y(L.hard - L.soft);
        svg.append(O.sv("line", { class: "soft", x1: pl, x2: W - pr, y1: ys, y2: ys }),
          stx({ class: "limit-text-soft", x: W - pr, y: ys - 4, "text-anchor": "end" }, "soft limit (warn)"));
      }
      if (L.hard != null) {
        svg.append(O.sv("line", { class: "hard", x1: pl, x2: W - pr, y1: y(0), y2: y(0) }),
          stx({ class: "limit-text-hard", x: W - pr, y: y(0) - 8, "text-anchor": "end" }, "hard limit (block)"));
      }
      if (burn.length) {
        const pts = burn.map((p, i) => [x(i), y(val(p))]);
        const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
        const last = pts[pts.length - 1];
        svg.append(O.sv("path", { class: "area", d: d + ` L${last[0].toFixed(1)} ${y(0)} L${pts[0][0].toFixed(1)} ${y(0)} Z` }),
          O.sv("path", { class: "line", d }));
        if (burn.length <= 60) {
          pts.forEach((p, i) => svg.append(O.sv("circle", { class: "pt", cx: p[0], cy: p[1], r: 2.5, "data-tip": `request ${i + 1}\n${fmt(burn[i][key])} used` })));
        }
      }
      svg.append(stx({ class: "axis-text", x: pl, y: H - 8 }, "request 1"),
        stx({ class: "axis-text", x: W - pr, y: H - 8, "text-anchor": "end" }, `request ${burn.length}`));
      chartHost.replaceChildren(svg,
        el("div", { class: "burn-legend" },
          el("span", {}, L.hard != null ? "line = budget remaining" : "line = cumulative use (no hard budget set)"),
          s.unpriced_calls ? O.pill(`${s.unpriced_calls} call(s) not priced`, "heur", { "data-tip": "No price in the table or no usage logged; USD is a lower bound" }) : null));
      summary.textContent = `${s.session}: ${s.requests} requests, ${fmt(used)} used`
        + (L.hard != null ? ` of ${fmt(L.hard)} (${fmt(Math.max(L.hard - used, 0))} left)` : "") + `, ${O.fmtN(s.tokens)} tokens.`;
    }
    function drawPick() {
      pick.replaceChildren(O.barList(sessions.map((s) => {
        const L = limits(s);
        const used = L.unit === "usd" ? s.usd || 0 : s.tokens;
        const over = L.hard != null && used >= L.hard;
        return { label: s.session, mono: true, value: L.hard != null ? Math.min(used / L.hard, 1) : 0, meta: usd(s.usd),
          tone: over ? "err" : L.soft != null && used >= L.soft ? "warn" : "ok",
          pill: over ? O.pill("hard limit", "err") : null, tip: `${s.session}\n${s.requests} requests\n${usd(s.usd)}`,
          onClick: () => { sel.id = s.session; drawChart(); } };
      }), { format: (v) => O.fmtPct(v, 0), max: 1, empty: "No sessions." }));
    }
    drawChart();
    drawPick();
    const burnCard = card("c-8", "Budget burn-down", "per session · budget remaining after each request", chartHost, el("div", { class: "card-b" }, summary));
    const pickCard = card("c-4", "Sessions", "bar = share of hard budget used · click to chart", pick);

    /* ---- interventions + blocked commands --------------------------------------------------- */
    const actionTone = { block: "err", deny: "err", "limit-reached": "err", inject: "warn", ask: "warn", warn: "warn" };
    const table = (label, rows, cols, empty) => {
      const t = el("table");
      O.sortableTable(t, cols, rows, { label, rowKey: (r) => r.id, sort: { key: "ts", dir: -1 }, empty,
        onRowClick: (r, tr) => openEvent(r, tr) });
      return el("div", { class: "tbl-wrap free" }, t);
    };
    const common = [
      { key: "ts", label: "Time", render: (r) => el("span", { class: "num" }, kit.fmtTime(r.ts)) },
      { key: "session", label: "Session", render: (r) => el("span", { class: "mono trunc", "data-tip": r.session }, r.session || "not logged") },
    ];
    const tool = { key: "tool", label: "Tool", render: (r) => el("span", { class: "case-id" }, r.tool || "not logged") };
    const act = { key: "action", label: "Action", render: (r) => O.pill(r.action, actionTone[r.action] || null) };
    const loops = events.filter((e) => e.kind === "loop");
    const cmds = events.filter((e) => e.kind === "command");
    const budget = events.filter((e) => e.kind === "budget");
    const loopCard = card("c-6", "Loop interventions", "same tool failing again and again · click a row", table("Loop interventions", loops, [
      ...common, tool, act,
      { key: "streak", label: "Repeats", render: (r) => el("span", { class: "num" }, r.streak == null ? "not logged" : String(r.streak)), value: (r) => r.streak || 0 },
    ], "No loop interventions in these sessions."));
    const cmdCard = card("c-6", "Blocked commands", "destructive-command policy · arguments are never stored", table("Blocked commands", cmds, [
      ...common, tool, act,
      { key: "rule", label: "Rule", render: (r) => el("span", { class: "mono" }, r.rule || "not logged") },
    ], "No tool call was blocked."));
    const budCard = budget.length ? card("c-12", "Budget limits", "soft = warning, hard = requests blocked", table("Budget limits", budget, [
      ...common, act, { key: "reason", label: "Reason", render: (r) => el("span", { class: "case-title" }, r.reason || "") },
    ], "")) : null;

    function openEvent(e, opener) {
      const nl = (v) => (v == null || v === "" ? "not logged" : String(v));
      kit.openDrawer({
        opener, lead: O.pill(e.action, actionTone[e.action] || null), title: e.title, tag: O.pill(e.kind, null, { class: "mono" }),
        strip: [{ label: "Action", value: e.action, tone: actionTone[e.action] }, { label: "Session", value: nl(e.session) }, { label: "Agent", value: nl(e.agent) }],
        stats: [["run", e.run_id], ["time", kit.fmtTime(e.ts)], ["request", nl(e.request_id)]],
        main: [
          el("section", { class: "ev" }, el("h4", {}, "Why"), kit.pre(nl(e.reason))),
          el("section", { class: "ev" }, el("h4", {}, "Detail"),
            O.kvList(["tool", "rule", "enforced", "streak", "loop_kind", "threshold"].filter((k) => e[k] != null).map((k) => [k, String(e[k])]))),
        ],
        detail: [el("h4", {}, "Content"), kit.nl("not logged (the guard records tool names, rules and hashes, never arguments)")],
      });
    }

    /* ---- audit chain ----------------------------------------------------------------------- */
    const a = data.audit || {};
    const state = a.ok === true ? ["intact", "ok"] : a.ok === false ? ["BROKEN", "err"] : ["no entries", null];
    const chain = card("c-12", "Audit chain", "append-only JSONL · each entry hashes the one before it",
      el("div", { class: "card-b" },
        el("div", { class: "chain" }, O.pill(state[0], state[1]),
          el("span", {}, `${O.fmtN(a.entries || 0)} entries · ${a.live ? "verified when this page was built" : "snapshot recorded by the run"}`),
          a.ok === false ? el("span", { class: "mono" }, a.error || "") : null),
        O.kvList([["log", a.path || "not logged"], ["head", a.head || "not logged"], ["check it yourself", "glmkit guard verify " + (a.path || "<log>")]])));

    panel.replaceChildren(el("div", { class: "grid guard-grid" }, burnCard, pickCard, loopCard, cmdCard, ...(budCard ? [budCard] : []), chain));
  },
  onShow(data, kit) {
    const usd = (v) => (v == null ? "not logged" : "$" + (Math.abs(v) < 1 ? v.toFixed(4) : v.toFixed(2)));
    kit.setKpis((data.kpis || []).map((t) => (t.format === "usd" ? { ...t, format: usd } : t)));
  },
});
