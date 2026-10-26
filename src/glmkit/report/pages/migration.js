/* Migration tab: paired verdict per task type, cost vs quality, item drill-down with side-by-side outputs. */
GLMKit.registerPage("migration", {
  render(panel, data, kit) {
    const { O, el } = kit;
    const cands = data.candidates || [];
    const state = { cand: cands[0], tt: "ALL", q: "", regOnly: false, all: false };
    const CAP = 60;
    const pts = (v) => (v == null ? "not logged" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}`);
    const ratio = (v) => (v == null ? "not logged" : `${v.toFixed(2)}×`);
    const usd = (v) => (v == null ? "not logged" : v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`);
    const vtext = (v) => ({ non_inferior: "non-inferior", inferior: "inferior", inconclusive: "inconclusive" })[v] || "not logged";
    const vtone = (v) => ({ non_inferior: "ok", inferior: "err", inconclusive: "warn" })[v] || null;
    const row = (cand, tt) => (data.comparisons || []).find((c) => c.target === cand && c.task_type === tt);

    if (!cands.length) {
      panel.replaceChildren(el("div", { class: "card" }, O.emptyState("This run has only the baseline target. Add --targets to compare.")));
      return;
    }

    /* ---- candidate selector + verdict table ---- */
    const selHost = el("div");
    const sel = el("div", { class: "mig-sel" }, el("span", { class: "lbl" }, "Candidate vs " + data.baseline), selHost);
    O.segmented(selHost, cands.map((c) => ({ value: c, label: c })), { value: state.cand, label: "Candidate target", onChange: (v) => { state.cand = v; refreshAll(); } });
    const vtable = el("table");
    const bound = Math.max(0.1, ...(data.comparisons || []).flatMap((c) => (c.quality.ci || []).filter((x) => x != null && isFinite(x)).map(Math.abs)), data.margin * 2);
    const R = Math.ceil(bound * 20) / 20;
    const pos = (x) => Math.max(0, Math.min(100, ((x + R) / (2 * R)) * 100));
    const ciBar = (r) => {
      const q = r.quality;
      if (!q.ci || q.ci[0] == null || !isFinite(q.ci[0])) return kit.nl();
      const lo = pos(q.ci[0]), hi = pos(q.ci[1]);
      return el("div", { class: "ci", role: "img", "aria-label": `${pts(q.delta)} points, 95% CI ${pts(q.ci[0])} to ${pts(q.ci[1])}`,
        "data-tip": `Δ ${pts(q.delta)} pts\n95% CI ${pts(q.ci[0])} to ${pts(q.ci[1])}\nmargin −${(data.margin * 100).toFixed(0)} pts` },
        el("div", { class: "ci-axis" }), el("div", { class: "ci-zero", style: `left:${pos(0)}%` }),
        el("div", { class: "ci-margin", style: `left:${pos(-data.margin)}%` }),
        el("div", { class: `ci-span ${vtone(q.verdict) || ""}`, style: `left:${lo}%;width:${Math.max(0.5, hi - lo)}%` }),
        el("div", { class: "ci-dot", style: `left:${pos(q.delta)}%` }));
    };
    const vcols = [
      { key: "task_type", label: "Task type", render: (r) => el("span", {}, el("span", { class: "case-id" }, r.task_type === "ALL" ? "all task types" : r.task_type), el("span", { class: "case-title" }, `${r.n} paired items`)), value: (r) => (r.task_type === "ALL" ? "~" : r.task_type) },
      { key: "delta", label: "Δ quality (pts)", align: "r", render: (r) => el("span", { class: "num" }, pts(r.quality.delta)), value: (r) => r.quality.delta ?? -9 },
      { key: "ci", label: "95% CI vs margin", render: ciBar, sortable: false },
      { key: "verdict", label: "Verdict", render: (r) => O.pill(vtext(r.quality.verdict), vtone(r.quality.verdict)), value: (r) => ({ inferior: 0, inconclusive: 1, non_inferior: 2 })[r.quality.verdict] ?? 3 },
      { key: "p", label: "McNemar p (Holm)", align: "r", render: (r) => (r.mcnemar ? el("span", { class: "num", "data-tip": `baseline-only passes ${r.mcnemar.b_baseline_only}, candidate-only passes ${r.mcnemar.c_candidate_only}` }, r.mcnemar.p.toFixed(3) + (r.mcnemar.p_holm != null ? ` (${r.mcnemar.p_holm.toFixed(3)})` : "")) : kit.nl("n/a")), value: (r) => (r.mcnemar ? r.mcnemar.p : 9) },
      { key: "cost", label: "Cost ×baseline", align: "r", render: (r) => (r.cost && r.cost.ratio != null ? el("span", { class: "num", "data-tip": `95% CI ${ratio(r.cost.ratio_ci[0])} to ${ratio(r.cost.ratio_ci[1])}\nlist price × tokens: an estimate` }, ratio(r.cost.ratio)) : kit.nl()), value: (r) => (r.cost && r.cost.ratio != null ? r.cost.ratio : 99) },
      { key: "lat", label: "p50 latency Δ", align: "r", render: (r) => (r.latency ? el("span", { class: "num", "data-tip": `95% CI ${Math.round(r.latency.median_diff_ci[0])} to ${Math.round(r.latency.median_diff_ci[1])} ms` }, `${r.latency.median_diff >= 0 ? "+" : ""}${Math.round(r.latency.median_diff)} ms`) : kit.nl()), value: (r) => (r.latency ? r.latency.median_diff : 1e9) },
      { key: "rec", label: "Recommendation", render: (r) => el("span", { class: "rec" }, r.recommendation), sortable: false },
    ];
    const vt = O.sortableTable(vtable, vcols, [], { label: "Verdict by task type", rowKey: (r) => r.task_type, sort: { key: "task_type", dir: 1 },
      empty: "No comparisons." });
    const legend = el("div", { class: "ci-legend" },
      el("span", {}, "bar: 95% clustered bootstrap CI of candidate − baseline; tick: the difference"),
      el("span", {}, el("i"), `non-inferiority margin −${(data.margin * 100).toFixed(0)} pts`),
      el("span", {}, "task-type rows are unadjusted; p-values carry Holm"));
    const verdictCard = el("div", { class: "card" },
      el("div", { class: "card-h" }, el("h2", {}, "Verdict by task type"), el("span", { class: "hint" }, "non-inferior = CI lower bound above the margin · filter the items below by task type")),
      sel, el("div", { class: "tbl-wrap free" }, vtable), legend);

    /* ---- cost vs quality ---- */
    const scatterHost = el("div");
    const cqCard = el("div", { class: "card" },
      el("div", { class: "card-h" }, el("h2", {}, "Cost vs quality"), el("span", { class: "hint" }, "per target and effort level · x = list-price cost per 1,000 requests (estimate) · ring = baseline")),
      el("div", { class: "card-b" }, scatterHost));
    function drawScatter() {
      const pts_ = (data.cost_quality || []).filter((p) => p.cost_per_1000_usd != null && p.quality != null);
      const missing = (data.cost_quality || []).filter((p) => p.cost_per_1000_usd == null || p.quality == null);
      const W = 560, H = 280, m = { l: 52, r: 24, t: 16, b: 40 };
      if (!pts_.length) { scatterHost.replaceChildren(O.emptyState("No target has both a quality score and a price entry (cost: not logged).")); return; }
      const xs = pts_.map((p) => p.cost_per_1000_usd), ys = pts_.map((p) => p.quality);
      const xMax = O.niceMax(Math.max(...xs) * 1.1), yMin = Math.max(0, Math.floor((Math.min(...ys) - 0.05) * 20) / 20), yMax = Math.min(1, Math.ceil((Math.max(...ys) + 0.02) * 20) / 20);
      const X = (v) => m.l + (v / xMax) * (W - m.l - m.r), Y = (v) => H - m.b - ((v - yMin) / (yMax - yMin || 1)) * (H - m.t - m.b);
      const svg = O.sv("svg", { class: "scatter", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Quality against cost per 1,000 requests for each target" });
      for (let i = 0; i <= 4; i++) {
        const yv = yMin + ((yMax - yMin) * i) / 4, xv = (xMax * i) / 4;
        svg.append(O.sv("line", { class: "grid-l", x1: m.l, x2: W - m.r, y1: Y(yv), y2: Y(yv) }),
          O.sv("text", { x: m.l - 6, y: Y(yv) + 3, "text-anchor": "end" }, (yv * 100).toFixed(0) + "%"),
          O.sv("text", { x: X(xv), y: H - m.b + 14, "text-anchor": "middle" }, usd(xv)));
      }
      svg.append(O.sv("line", { class: "axis-l", x1: m.l, x2: m.l, y1: m.t, y2: H - m.b }), O.sv("line", { class: "axis-l", x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b }),
        O.sv("text", { x: (m.l + W - m.r) / 2, y: H - 6, "text-anchor": "middle" }, "cost per 1,000 requests (USD, list price)"),
        O.sv("text", { x: 12, y: (m.t + H - m.b) / 2, "text-anchor": "middle", transform: `rotate(-90 12 ${(m.t + H - m.b) / 2})` }, "mean item score"));
      pts_.forEach((p, i) => {
        const g = O.sv("g", { "data-tip": `${p.target}\nquality ${(p.quality * 100).toFixed(1)}%${p.pass_rate != null ? " · pass rate " + (p.pass_rate * 100).toFixed(1) + "%" : ""}\n${usd(p.cost_per_1000_usd)} per 1,000 requests` });
        if (p.baseline) g.append(O.sv("circle", { class: "ring", cx: X(p.cost_per_1000_usd), cy: Y(p.quality), r: 9 }));
        g.append(O.sv("circle", { cx: X(p.cost_per_1000_usd), cy: Y(p.quality), r: 5, fill: O.cv("--cat-" + ((i % 8) + 1)) }),
          O.sv("text", { class: "lbl-pt", x: X(p.cost_per_1000_usd) + 10, y: Y(p.quality) - 8 }, p.target));
        svg.append(g);
      });
      scatterHost.replaceChildren(svg, ...(missing.length ? [el("div", { class: "hint" }, "Not plotted (cost or quality not logged): " + missing.map((p) => p.target).join(", "))] : []));
    }

    /* ---- targets table ---- */
    const ttable = el("table");
    const tcols = [
      { key: "target", label: "Target", render: (r) => el("span", { class: "case-id" }, r.target + (r.target === data.baseline ? " (baseline)" : "")) },
      { key: "pass", label: "Pass rate", align: "r", render: (r) => (r.s.pass_rate == null ? kit.nl("judge only") : el("span", { class: "num", "data-tip": `Wilson 95% ${(r.s.pass_rate_wilson[0] * 100).toFixed(1)}% to ${(r.s.pass_rate_wilson[1] * 100).toFixed(1)}%` }, (r.s.pass_rate * 100).toFixed(1) + "%")), value: (r) => r.s.pass_rate ?? -1 },
      { key: "c1k", label: "$ / 1k requests", align: "r", render: (r) => el("span", { class: "num" }, usd(r.s.cost_per_1000_usd)), value: (r) => r.s.cost_per_1000_usd ?? 1e9 },
      { key: "spd", label: "Solves / $100", align: "r", render: (r) => (r.s.solves_per_100_usd == null ? kit.nl() : el("span", { class: "num" }, Math.round(r.s.solves_per_100_usd).toLocaleString())), value: (r) => r.s.solves_per_100_usd ?? -1 },
      { key: "p50", label: "p50 / p95 latency", align: "r", render: (r) => el("span", { class: "num" }, `${kit.fmtMs(r.s.latency_p50_ms)} / ${kit.fmtMs(r.s.latency_p95_ms)}`), value: (r) => r.s.latency_p50_ms ?? 1e9 },
      { key: "out", label: "Output tok (med / p95)", align: "r", render: (r) => el("span", { class: "num" }, r.s.output_tokens_median == null ? "not logged" : `${Math.round(r.s.output_tokens_median)} / ${Math.round(r.s.output_tokens_p95)}`), value: (r) => r.s.output_tokens_median ?? 0 },
      { key: "rsn", label: "Reasoning tok (med)", align: "r", render: (r) => el("span", { class: "num" }, r.s.reasoning_tokens_median == null ? "not logged" : String(Math.round(r.s.reasoning_tokens_median))), value: (r) => r.s.reasoning_tokens_median ?? 0 },
      { key: "trunc", label: "Truncated", align: "r", render: (r) => el("span", { class: "num" }, r.s.truncated_share == null ? "not logged" : (r.s.truncated_share * 100).toFixed(1) + "%"), value: (r) => r.s.truncated_share ?? 0 },
      { key: "thr", label: "Throttled / errors", align: "r", render: (r) => el("span", { class: "num", "data-tip": "throttled calls are retried and never counted as failures" }, `${r.s.throttled_retries} / ${r.s.call_errors}`), value: (r) => r.s.call_errors },
    ];
    const tgtRows = (data.targets || []).map((t) => ({ target: t.label, s: (data.target_summary || {})[t.label] || {} }));
    O.sortableTable(ttable, tcols, tgtRows, { label: "Per-target summary", rowKey: (r) => r.target });
    const tgtCard = el("div", { class: "card" },
      el("div", { class: "card-h" }, el("h2", {}, "Targets"), el("span", { class: "hint" }, "absolute numbers per target · cost is recomputed from the price table, not the source")),
      el("div", { class: "tbl-wrap free" }, ttable));

    /* ---- items ---- */
    const results = el("span", { class: "results", "aria-live": "polite" });
    const search = O.searchBox({ placeholder: "Search item id", label: "Search items", onInput: (v) => { state.q = v; itemsRefresh(); } });
    const ttHost = el("div");
    const types = ["ALL", ...Object.keys((data.set || {}).task_types || {})];
    const ttSeg = O.segmented(ttHost, types.map((t) => ({ value: t, label: t === "ALL" ? "all" : t })), { value: state.tt, label: "Task type", onChange: (v) => { state.tt = v; itemsRefresh(); } });
    const regChip = O.toggleChip("Regressions only", { onChange: (on) => { state.regOnly = on; itemsRefresh(); } });
    const allChip = O.toggleChip("Show all", { onChange: (on) => { state.all = on; itemsRefresh(); } });
    const itable = el("table");
    const cell = (r, who) => r.targets[who] || null;
    const icols = [
      { key: "id", label: "Item", render: (r) => el("span", {}, el("span", { class: "case-id" }, r.id), el("span", { class: "case-title" }, r.task_type + (r.judged ? " · judge" : ""))), value: (r) => r.id },
      { key: "base", label: "Baseline", align: "r", render: (r) => { const c = cell(r, data.baseline); return c && c.score != null ? el("span", { class: "num" }, (c.score * 100).toFixed(0) + "%") : kit.nl(); }, value: (r) => cell(r, data.baseline)?.score ?? -1 },
      { key: "cand", label: "Candidate", align: "r", render: (r) => { const c = cell(r, state.cand); return c && c.score != null ? el("span", { class: "num" }, (c.score * 100).toFixed(0) + "%") : kit.nl(); }, value: (r) => cell(r, state.cand)?.score ?? -1 },
      { key: "delta", label: "Δ (pts)", align: "r", render: (r) => { const c = cell(r, state.cand); return c && c.delta != null ? el("span", { class: "num" }, pts(c.delta)) : kit.nl(); }, value: (r) => cell(r, state.cand)?.delta ?? 9 },
      { key: "status", label: "Candidate result", render: (r) => { const c = cell(r, state.cand); return c ? kit.statusPill(c.status) : kit.nl(); }, value: (r) => kit.rank(cell(r, state.cand)?.status) },
      { key: "flags", label: "Flags", render: (r) => { const f = flags(r); return el("span", { class: "flag-row" }, f.length ? f.map((x) => O.pill(x, x === "critical" ? "err" : "warn")) : kit.nl("none")); }, sortable: false },
    ];
    function flags(r) {
      const out = [];
      if (r.critical) out.push("critical");
      for (const rep of (cell(r, state.cand)?.replays || [])) for (const f of (rep.grade?.flags || [])) if (!out.includes(f)) out.push(f);
      return out;
    }
    const it = O.sortableTable(itable, icols, [], { label: "Items", rowKey: (r) => r.id, sort: { key: "delta", dir: 1 },
      empty: "No items match. Clear the search or the filters.", onRowClick: (r, tr) => openItemDrawer(r, tr),
      onRender: (rows) => (results.textContent = `${rows.length} of ${(data.items || []).length} items`) });
    function itemsRefresh() {
      ttSeg.set(state.tt);
      const q = state.q.trim().toLowerCase();
      const rows = (data.items || []).filter((r) => (state.tt === "ALL" || r.task_type === state.tt)
        && (!q || r.id.toLowerCase().includes(q))
        && (!state.regOnly || ((cell(r, state.cand)?.delta ?? 0) < 0)));
      const capped = !state.all && rows.length > CAP;
      it.setRows(capped ? rows.slice().sort((a, b) => (cell(a, state.cand)?.delta ?? 9) - (cell(b, state.cand)?.delta ?? 9)).slice(0, CAP) : rows);
      if (capped) results.textContent = `worst ${CAP} of ${rows.length} items (turn on "Show all" for the rest)`;
    }
    const itemsCard = el("div", { class: "card" },
      el("div", { class: "card-h" }, el("h2", {}, "Items"), el("span", { class: "hint" }, "sorted by worst candidate delta first · click an item for side-by-side outputs")),
      el("div", { class: "toolbar" }, search.label, ttHost, regChip, allChip, results),
      el("div", { class: "tbl-wrap free" }, itable));

    function openItemDrawer(r, opener) {
      const block = (who) => {
        const c = cell(r, who);
        if (!c) return el("section", { class: "ev" }, el("h4", {}, who), kit.nl());
        const reps = c.replays || [];
        return el("section", { class: "ev", "aria-label": who },
          el("h4", {}, who, who === data.baseline ? O.pill("baseline") : null, kit.statusPill(c.status), c.score != null ? el("span", { class: "num" }, (c.score * 100).toFixed(0) + "%") : null),
          ...reps.flatMap((rep) => [
            kit.pre(rep.output || "(empty output)"),
            el("div", { class: "meta" }, `replay ${rep.k + 1}: ${rep.detail || "not logged"}`),
            el("div", { class: "meta" }, `${kit.fmtMs(rep.latency_ms)} · ${rep.usage && rep.usage.output_tokens_total != null ? rep.usage.output_tokens_total + " output tok" : "tokens not logged"} · ${usd(rep.cost_usd)}${rep.throttled_retries ? " · retried " + rep.throttled_retries + "× on throttle" : ""}`),
            ...(rep.grade && rep.grade.judge ? [el("div", { class: "meta" }, `judge orders ${rep.grade.judge.orders.join(" / ")}${rep.grade.judge.consistent ? "" : " (inconsistent: counted as tie)"} · ${rep.grade.judge.reasons.join(" | ")}`)] : []),
          ]));
      };
      const order = [data.baseline, state.cand, ...(data.targets || []).map((t) => t.label).filter((l) => l !== data.baseline && l !== state.cand)];
      const cc = cell(r, state.cand);
      kit.openDrawer({
        opener,
        lead: cc ? kit.statusPill(cc.status) : null,
        title: `${r.id} · ${r.task_type}`,
        tag: r.critical ? O.pill("critical", "err") : null,
        strip: [
          { label: "Baseline", value: cell(r, data.baseline)?.score != null ? (cell(r, data.baseline).score * 100).toFixed(0) + "%" : "not logged" },
          { label: "Candidate", value: cc && cc.score != null ? (cc.score * 100).toFixed(0) + "%" : "not logged", tone: cc && cc.status === "fail" ? "err" : null },
          { label: "Δ points", value: cc && cc.delta != null ? pts(cc.delta) : "not logged", tone: cc && cc.delta < 0 ? "warn" : null },
        ],
        stats: [["run", data.run.run_id], ["baseline", data.baseline], ["candidate", state.cand]],
        main: [el("div", { class: "sbs" }, ...order.map(block))],
        detail: [
          el("h4", {}, "Prompt"), kit.pre(r.prompt || "not logged"),
          el("h4", {}, "Expected"), kit.pre(r.expected == null ? "not logged (judge reference is the recorded answer)" : (typeof r.expected === "string" ? r.expected : JSON.stringify(r.expected, null, 2))),
        ],
      });
    }

    /* ---- judge audit + run details ---- */
    const judgeCards = [];
    if (data.judge) {
      const j = data.judge;
      const per = Object.entries(j.per_target || {});
      judgeCards.push(el("div", { class: "card" },
        el("div", { class: "card-h" }, el("h2", {}, "Judge audit"), el("span", { class: "hint" }, "pairwise, both position orders · a win counts only when both agree")),
        el("div", { class: "mig-kv" }, O.kvList([
          ["judge", `${j.judge_model} (${j.judge_family})`],
          ["position swap", j.swap ? "on" : "off"],
          ["judge calls", `${j.calls} made · ${j.cache_hits} from cache`],
          ...per.flatMap(([t, p]) => [
            [t + " judged", `${p.judged} items`],
            [t + " position-inconsistent", `${p.position_inconsistent} (${(p.position_inconsistency_rate * 100).toFixed(1)}%) · scored as tie and flagged`],
            [t + " position bias", `first slot ${p.position_bias_first} · second slot ${p.position_bias_second}`],
            [t + " longer answer won", `${p.longer_won} consistent wins`],
            [t + " by length", Object.entries(p.win_score_by_length).map(([k, v]) => `${k}: ${v.n ? (v.mean_score * 100).toFixed(0) + "% (n=" + v.n + ")" : "n=0"}`).join(" · ")],
          ]),
        ])),
        el("div", { class: "mig-note" }, ...(j.warnings || []).map((w) => el("p", {}, O.pill("warning", "warn"), " " + w)), el("p", {}, j.note))));
    }
    const b = data.budget || {};
    const notes = [
      ...(data.warnings || []).map((w) => el("p", {}, O.pill("note", "warn"), " " + w)),
      el("p", {}, `Method: ${data.method.unit}. ${data.method.ci}. ${data.method.verdict}.`),
      el("p", {}, `Price table ${data.price_table.version}: ` + data.price_table.entries.slice(0, 6).map((e) => `${e.model}${e.provider !== "*" ? "@" + e.provider : ""} in $${e.input} / cached $${e.cached_input} / out $${e.output} per 1M (${e.as_of})${e.mock ? " MOCK, illustrative" : ""}`).join("; ")),
    ];
    const runCard = el("div", { class: "card heuristic" },
      el("div", { class: "card-h" }, el("h2", {}, "Run details"), O.pill("cost = estimate", "heur"), el("span", { class: "hint" }, "what was run, what it cost, what to distrust")),
      el("div", { class: "mig-kv" }, O.kvList([
        ["run", `${data.run.run_id} (${data.run.verdict})`],
        ["set", `${data.set.name} · ${data.set.items} items · fingerprint ${data.set.fingerprint}`],
        ["seed / bootstrap", `${data.run.seed} / ${data.run.n_boot} resamples`],
        ["spend", `${usd(b.spent_usd)}${b.cap_usd != null ? " of cap " + usd(b.cap_usd) : " (no cap)"}${b.exhausted ? " · cap reached, " + b.calls_skipped + " calls skipped" : ""}`],
        ...(data.other_runs.length ? [["earlier eval runs", data.other_runs.map((r) => `${r.run_id} (${r.verdict})`).join(", ")]] : []),
      ])),
      el("div", { class: "mig-note" }, ...notes));

    panel.replaceChildren(el("div", { class: "stack" }, verdictCard,
      el("div", { class: "grid" }, el("div", { class: "c-7" }, cqCard), el("div", { class: "c-5" }, tgtCard)),
      itemsCard, ...judgeCards, runCard));

    /* ---- refresh ---- */
    function kpis() {
      const r = row(state.cand, "ALL");
      if (!r) return kit.setKpis([]);
      const q = r.quality, tone = vtone(q.verdict) === "ok" ? null : vtone(q.verdict);
      kit.setKpis([
        { label: "Quality delta", value: q.delta, format: (x) => (x == null ? "not logged" : pts(x) + " pts"), tone,
          sub: `${state.cand} vs ${data.baseline} · 95% CI ${pts(q.ci[0])} to ${pts(q.ci[1])}` },
        { label: "Cost delta", value: r.cost ? r.cost.ratio : null, format: (x) => ratio(x), sub: r.cost ? "× baseline · list price estimate" : "no price entry" },
        { label: "p50 latency delta", value: r.latency ? r.latency.median_diff : null, format: (x) => (x == null ? "not logged" : `${x >= 0 ? "+" : ""}${Math.round(x)} ms`), sub: r.latency ? `baseline p50 ${kit.fmtMs(r.latency.median_baseline)}` : "not logged" },
        { label: "Verdict", value: 1, format: () => vtext(q.verdict), tone, sub: `margin −${(data.margin * 100).toFixed(0)} pts · n=${q.n}` },
      ]);
    }
    function refreshAll() {
      vt.setRows((data.comparisons || []).filter((c) => c.target === state.cand));
      itemsRefresh();
      kpis();
    }
    this.refresh = refreshAll;
    drawScatter();
    refreshAll();
  },
  onShow(data, kit) {
    if (this.refresh) this.refresh();
  },
});
