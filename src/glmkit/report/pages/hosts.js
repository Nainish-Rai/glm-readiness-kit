/* Hosts tab: host-quality verdicts, latency range bars (log scale), tokens/s, fingerprint gates and canaries.
 * Estimated values (p-values, percent-of-reference) use the heuristic score card; measured ones do not. */
GLMKit.registerPage("hosts", {
  render(panel, data, kit) {
    const { O, el } = kit;
    const hosts = data.hosts || [];
    const COND = { no_tools: { label: "no tools", color: "--cat-1" }, tools_1: { label: "1 tool", color: "--cat-2" }, tools_60: { label: "60 tools", color: "--cat-3" } };
    const EFFORT_COLOR = { low: "--cat-1", high: "--cat-2", max: "--cat-3" };
    const VERDICT_TONE = { pass: "ok", warn: "warn", fail: "err", incomplete: null };
    const GATE_TONE = { pass: "ok", flag: "err", not_evaluated: null };
    const GATE_LABEL = { pass: "pass", flag: "flag", not_evaluated: "not evaluated" };
    const num = (v, d = 3) => (v == null ? kit.nl() : Number(v).toFixed(d));
    const fmtP = (p) => (p == null ? "not logged" : p < 0.0001 ? "<0.0001" : p.toFixed(4));
    const verdictPill = (v) => O.pill(v || "incomplete", VERDICT_TONE[v] ?? null);
    const hostCell = (h) => el("span", {}, el("span", { class: "host-name" }, h.key),
      h.defects.length ? el("span", { class: "host-defects" }, "planted: " + h.defects.join(", ")) : null);
    const gatePills = (h) => el("span", { class: "gate-pills" }, h.gates.map((g) =>
      O.pill(g.id, GATE_TONE[g.status] ?? null, { "data-tip": `${g.id} ${GATE_LABEL[g.status]}\n${g.message || ""}` })));
    const penalty = (h) => { const d = Object.values(h.tools.tests || {}).map((t) => t.delta).filter((x) => x != null); return d.length ? Math.max(...d) : null; };
    const decode = (h, e) => ((h.efforts[e] || {}).decode_tps || {}).p50 ?? null;

    /* ---- verdict table */
    const table = el("table");
    const cols = [
      { key: "key", label: "Host", render: hostCell, value: (h) => h.key },
      { key: "verdict", label: "Host-quality verdict", render: (h) => verdictPill(h.verdict), value: (h) => ({ fail: 0, warn: 1, incomplete: 2, pass: 3 })[h.verdict] ?? 9 },
      { key: "gates", label: "Gates", render: gatePills, value: (h) => h.gates.filter((g) => g.status === "flag").length },
      { key: "penalty", label: "Tool TTFT penalty", render: (h) => (penalty(h) == null ? kit.nl() : el("span", { class: "num" }, O.fmtDur(penalty(h)))), value: (h) => penalty(h) ?? -1 },
      { key: "p", label: "Action p-value", render: (h) => (h.action && h.action.p_value != null ? el("span", { class: "num" }, fmtP(h.action.p_value)) : kit.nl()), value: (h) => (h.action && h.action.p_value) ?? 2 },
      { key: "canary", label: "Canaries vs ref", render: (h) => (h.canary && h.canary.ratio != null ? el("span", { class: "num" }, O.fmtPct(h.canary.ratio, 0)) : kit.nl()), value: (h) => (h.canary && h.canary.ratio) ?? -1 },
      { key: "tps", label: "Decode tok/s (high)", render: (h) => (decode(h, "high") == null ? kit.nl() : el("span", { class: "num" }, O.fmtN(Math.round(decode(h, "high"))))), value: (h) => decode(h, "high") ?? -1 },
    ];
    panel.replaceChildren(el("div", { class: "card" },
      el("div", { class: "card-h" }, el("h2", {}, "Host quality"), el("span", { class: "hint" }, "latest run per host · separate from the correctness verdict · click a row for gates and evidence")),
      el("div", { class: "tbl-wrap free" }, table)));
    O.sortableTable(table, cols, hosts, { label: "Hosts", rowKey: (h) => h.key,
      empty: "No host runs yet. Run `glmkit conform host --target mock` and rebuild.",
      onRowClick: (h, tr) => openHost(h, tr) });

    /* ---- latency + throughput */
    const latHost = el("div");
    const latSeg = el("div");
    const tpsList = el("div");
    const tpsSeg = el("div");
    const latCard = el("div", { class: "card c-7" },
      el("div", { class: "card-h" }, el("h2", {}, "Time to first token"), latSeg, el("span", { class: "hint" }, "bar to p50, lighter to p95, tick at p95 · log scale")),
      el("div", { class: "card-b" }, latHost));
    const tpsCard = el("div", { class: "card c-5" },
      el("div", { class: "card-h" }, el("h2", {}, "Decode tokens/s"), tpsSeg, el("span", { class: "hint" }, "median per host · completion tokens over time after the first token")),
      el("div", { class: "card-b" }, tpsList));
    const note = (h, c) => `${c.requests} requests, ${c.throttled} throttled (excluded), ${c.errors} failed`;
    function paintLatency(mode) {
      const rows = [];
      for (const h of hosts) {
        if (mode === "tools") {
          for (const [k, meta] of Object.entries(COND)) {
            const c = (h.tools.conditions || {})[k];
            if (!c || !c.ttft_s.n) continue;
            rows.push({ label: `${h.key} · ${meta.label}`, p50: c.ttft_s.p50, p95: c.ttft_s.p95, n: c.ttft_s.n, color: meta.color,
              tip: `${h.key} · ${meta.label}\nn ${c.ttft_s.n} · p50 ${O.fmtDur(c.ttft_s.p50)} · p90 ${O.fmtDur(c.ttft_s.p90)} · p95 ${O.fmtDur(c.ttft_s.p95)}\n${note(h, c)}` });
          }
        } else {
          for (const e of ["low", "high", "max"]) {
            const c = (h.efforts || {})[e];
            if (!c || !c.ttft_s.n) continue;
            rows.push({ label: `${h.key} · ${e}`, p50: c.ttft_s.p50, p95: c.ttft_s.p95, n: c.ttft_s.n, color: EFFORT_COLOR[e],
              tip: `${h.key} · effort ${e}\nn ${c.ttft_s.n} · p50 ${O.fmtDur(c.ttft_s.p50)} · p90 ${O.fmtDur(c.ttft_s.p90)} · p95 ${O.fmtDur(c.ttft_s.p95)}\n${note(h, c)}` });
          }
        }
      }
      O.latencyRows(latHost, rows, { log: true, empty: "No latency logged." });
    }
    function paintTps(effort) {
      const rows = hosts.map((h) => {
        const c = (h.efforts || {})[effort];
        const v = decode(h, effort);
        return v == null ? null : { label: h.key, value: v, mono: true, color: EFFORT_COLOR[effort],
          tip: `${h.key} · effort ${effort}\ndecode p50 ${Math.round(v)} tok/s · end-to-end p50 ${Math.round((c.e2e_tps || {}).p50 || 0)} tok/s\n${note(h, c)}` };
      }).filter(Boolean);
      tpsList.replaceChildren(O.barList(rows, { format: (v) => O.fmtN(Math.round(v)) + " tok/s", empty: "No throughput logged." }));
    }
    O.segmented(latSeg, [{ value: "tools", label: "Tools vs none" }, { value: "effort", label: "By effort" }], { label: "Latency view", onChange: paintLatency });
    O.segmented(tpsSeg, [{ value: "low", label: "low" }, { value: "high", label: "high" }, { value: "max", label: "max" }], { value: "high", label: "Reasoning effort", onChange: paintTps });
    paintLatency("tools");
    paintTps("high");

    /* ---- fingerprint score cards, one group per host */
    const fpBody = el("div");
    for (const h of hosts) {
      const cards = [];
      const a = h.action, t = h.text, g = h.greedy, id = h.identity;
      cards.push(O.scoreCard({ label: "Action fingerprint p-value", value: a ? a.p_value : null, format: (x) => (x == null ? "not logged" : fmtP(x)),
        foot: a && a.p_value != null ? `MMD² ${num(a.mmd2, 4)} · ${a.n_x} vs ${a.n_y} samples · seed ${a.seed} · flag below ${a.alpha}` : (a ? `no reference · ${a.n} samples kept` : "not logged") }));
      cards.push(O.scoreCard({ label: "Text fingerprint p-value", value: t ? t.p_value : null, format: (x) => (x == null ? "not logged" : fmtP(x)),
        foot: t && t.p_value != null ? `MMD² ${num(t.mmd2, 4)} · ${t.n_x} vs ${t.n_y} samples · seed ${t.seed} · flag below ${t.alpha}` : (t ? `no reference · ${t.n} samples kept` : "not logged") }));
      cards.push(O.scoreCard({ label: "Greedy self-agreement", value: g ? g.rate : null, format: (x) => (x == null ? "not logged" : O.fmtPct(x, 1)),
        ring: g && g.rate != null ? g.rate : null, foot: g ? `${g.agree} of ${g.pairs} pairs agree` + (g.ref_rate != null ? ` · reference ${O.fmtPct(g.ref_rate, 1)}` : " · noise floor, no reference") : "not logged" }));
      cards.push(O.scoreCard({ label: "Model field honesty", heuristic: false, value: id ? id.problems.length : null,
        format: (x) => (x == null ? "not logged" : x === 0 ? "honest" : `${Math.round(x)} problem${x === 1 ? "" : "s"}`),
        foot: id ? (id.problems.length ? id.problems.join("; ") : "response model equals the request; unknown model refused") : "not logged" }));
      fpBody.append(el("div", { class: "host-group" }, el("h3", {}, h.key, verdictPill(h.verdict)), el("div", { class: "score-cards" }, cards)));
    }
    const fpCard = el("div", { class: "card" },
      el("div", { class: "card-h" }, el("h2", {}, "Fingerprint vs reference"), el("span", { class: "hint" }, "estimated · permutation tests with fixed seeds · needs a reference (glmkit conform reference record)")),
      el("div", { class: "card-b" }, hosts.length ? fpBody : O.emptyState("No host runs.")));

    /* ---- canaries */
    const cnBody = el("div", { class: "score-cards" }, hosts.map((h) => {
      const c = h.canary;
      if (!c) return O.scoreCard({ label: `${h.key} · canaries`, value: null });
      const fam = Object.entries(c.by_family || {}).map(([k, v]) => `${k} ${v.correct}/${v.n}`).join(" · ");
      if (c.ratio == null) return O.scoreCard({ label: `${h.key} · canary accuracy`, value: c.accuracy, format: (x) => O.fmtPct(x, 0),
        foot: `${c.correct}/${c.n} correct (no reference, absolute) · ${fam}` });
      return O.scoreCard({ label: `${h.key} · percent of reference`, value: c.ratio, format: (x) => O.fmtPct(x, 0), ring: Math.min(c.ratio, 1),
        ringTip: `95% CI ${O.fmtPct(c.lo, 0)} to ${O.fmtPct(c.hi, 0)}`,
        foot: `95% CI ${O.fmtPct(c.lo, 0)} to ${O.fmtPct(c.hi, 0)} · ${c.correct}/${c.n} correct · ${fam}` });
    }));
    const cnCard = el("div", { class: "card" },
      el("div", { class: "card-h" }, el("h2", {}, "Capability canaries"), el("span", { class: "hint" }, "tool-call slice, math slice, needle at 128k · flagged when the 95% CI sits entirely below 100%")),
      el("div", { class: "card-b" }, hosts.length ? cnBody : O.emptyState("No host runs.")));

    panel.append(el("div", { class: "grid" }, latCard, tpsCard), fpCard, cnCard);

    /* ---- drawer */
    function openHost(h, opener) {
      const gateRows = h.gates.map((g) => el("div", { class: "gate" },
        O.pill(GATE_LABEL[g.status], GATE_TONE[g.status] ?? null),
        el("span", { class: "gid" }, g.id + (g.severity === "soft" ? " (informational)" : "")),
        g.ref ? el("button", { type: "button", class: "chip", onclick: (e) => { const rec = (data.items || {})[g.ref]; if (rec) kit.openItem(rec.item, rec.run, e.currentTarget); } }, "Open evidence") : el("span"),
        el("span", { class: "msg" }, g.message || "")));
      const r = h.reference;
      kit.openDrawer({ opener, title: `Host ${h.key}`, lead: verdictPill(h.verdict), tag: O.pill(h.run_id, null, { class: "mono" }),
        strip: [{ label: "Verdict", value: h.verdict, tone: VERDICT_TONE[h.verdict] || null },
          { label: "Flagged gates", value: String(h.gates.filter((g) => g.status === "flag").length) },
          { label: "Not evaluated", value: String(h.gates.filter((g) => g.status === "not_evaluated").length) }],
        main: [el("section", { class: "ev" }, el("h4", {}, "Gates"), h.reason ? el("div", { class: "url" }, h.reason) : null, ...gateRows)],
        detail: [el("h4", {}, "Run"), O.kvList([
          ["profile", h.profile || kit.nl()], ["model", h.model || kit.nl()], ["endpoint class", h.endpoint_class || kit.nl()],
          ["started", kit.fmtTime(h.started_at)], ["planted defects", h.defects.length ? h.defects.join(", ") : "none"],
          ["reference", r ? `${r.id} (${r.label || "unlabelled"})` : kit.nl("none loaded")],
          ["reference recorded", r && r.recorded_at ? kit.fmtTime(r.recorded_at) : kit.nl()]])] });
    }
  },
});
