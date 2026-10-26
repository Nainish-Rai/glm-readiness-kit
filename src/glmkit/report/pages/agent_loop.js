/* Agent loop tab: cached tokens per turn (stacked bars), degeneracy heat strip by turn, stream-parity table. */
GLMKit.registerPage("agent_loop", {
  render(panel, data, kit) {
    const { O, el } = kit;
    const loops = (data.loops || []).slice().sort((a, b) => kit.rank(a.status) - kit.rank(b.status) || a.item.localeCompare(b.item));
    const trials = data.trials || [];
    const parity = data.parity || [];
    const HEAT = [85, 70];
    const health = (x) => (x.score == null ? null : x.degenerate ? 0 : 100 - Math.round(x.score * 50));
    const pct = (x) => (x == null ? kit.nl() : O.fmtPct(x, 0));
    const num = (x) => (x == null ? kit.nl() : el("span", { class: "num" }, O.fmtN(x)));
    let selected = loops.find((l) => l.group === "CACHE-002" && !l.column.includes("+")) || loops.find((l) => l.group === "CACHE-002") || loops[0] || null;

    /* ---- loops table + stacked bars ---- */
    const loopTable = el("table");
    const loopCard = el("div", { class: "card c-6" },
      el("div", { class: "card-h" }, el("h2", {}, "Loops"), el("span", { class: "hint" }, "agent loops per target · click a row to chart it and open its turns")),
      el("div", { class: "tbl-wrap free" }, loopTable));
    const chartTitle = el("h2", {}, "Cached tokens per turn");
    const chartHint = el("span", { class: "hint" }, "");
    const legendBox = el("div", { class: "al-legend" });
    const svg = O.sv("svg", {});
    const chartBody = el("div", { class: "al-chart" }, svg);
    const detailBtn = el("button", { type: "button", class: "chip", onclick: (e) => selected && openLoop(selected, e.currentTarget) }, "Per-turn detail");
    const chartCard = el("div", { class: "card c-6" },
      el("div", { class: "card-h" }, chartTitle, chartHint),
      legendBox, chartBody, el("div", { class: "al-sel-row" }, detailBtn));

    const seriesFor = (l) => [
      { name: "cached", color: "--ok", values: l.turns.map((t) => t.cached || 0) },
      { name: "uncached", color: "--cat-1", values: l.turns.map((t) => (t.prompt == null ? 0 : Math.max(t.prompt - (t.cached || 0), 0))) },
    ];
    let chart = null;
    function drawChart() {
      if (!selected) {
        chartBody.replaceChildren(O.emptyState("No agent loop in the loaded runs. Run `glmkit conform run --target mock --cases 'CACHE-002'` and rebuild."));
        return;
      }
      chartHint.textContent = `${selected.item} on ${selected.column} · ${selected.turns.length} turns · prompt tokens, cached vs uncached`;
      const labels = selected.turns.map((t) => t.label.replace("turn", "t"));
      if (chart) chart.destroy();
      if (!chartBody.contains(svg)) chartBody.replaceChildren(svg);
      chart = O.stackedBars(svg, seriesFor(selected), { labels, height: 240, label: `Prompt tokens per turn for ${selected.item}` });
    }
    O.legend(legendBox, [{ id: "cached", label: "cached tokens", color: "--ok" }, { id: "uncached", label: "uncached prompt tokens", color: "--cat-1" }]);

    const lt = O.sortableTable(loopTable, [
      { key: "item", label: "Case", render: (l) => el("span", { class: "case-id", "data-tip": l.title }, l.item) },
      { key: "column", label: "Target", render: (l) => el("span", { class: "mono trunc", "data-tip": l.column }, l.column) },
      { key: "turns", label: "Turns", align: "r", render: (l) => el("span", { class: "num" }, String(l.turns.length)), value: (l) => l.turns.length },
      { key: "share", label: "Cached", align: "r", render: (l) => pct(l.cached_share), value: (l) => (l.cached_share == null ? -1 : l.cached_share) },
      { key: "deg", label: "Degenerate", align: "r", render: (l) => (l.degenerate_turns ? O.pill(String(l.degenerate_turns), "err") : el("span", { class: "num" }, "0")), value: (l) => l.degenerate_turns },
      { key: "status", label: "Status", render: (l) => kit.statusPill(l.status), value: (l) => kit.rank(l.status) },
    ], loops, { label: "Agent loops", rowKey: (l) => l.key, empty: "No agent loops in the loaded runs.",
      onRowClick: (l, tr) => { selected = l; drawChart(); openLoop(l, tr); } });

    /* ---- heat strips ---- */
    const heatBox = el("div", { class: "card-b" });
    const heatCard = el("div", { class: "card c-12" },
      el("div", { class: "card-h" }, el("h2", {}, "Degeneracy by turn"), el("span", { class: "hint" }, "one cell per turn or replay trial · green healthy, red = degenerate (over 0.2 of '=' / 'the' words or looped tokens) · grey not scored")),
      heatBox);
    const cells = (turns, name) => turns.map((t) => ({
      label: `${name} ${t.label}`,
      score: health(t),
      tip: `${name} ${t.label}\n${t.score == null ? "not scored" : "degeneracy " + t.score}${t.http && t.http >= 400 ? "\nHTTP " + t.http : ""}`,
    }));
    function heatRow(name, sub, cs, onSelect) {
      const strip = el("div", {});
      O.heatStrip(strip, cs, { thresholds: HEAT, onSelect });
      return el("div", { class: "al-heat-row" }, el("div", { class: "al-name" }, el("span", { class: "mono" }, name), el("span", {}, sub)), strip);
    }
    const rows = [
      ...loops.map((l) => heatRow(l.item, l.column, cells(l.turns, l.item), (c, b) => openLoop(l, b))),
      ...trials.map((t) => heatRow(t.item, `${t.column} · ${t.trials.length} of ${t.attempts == null ? "?" : t.attempts} trials kept as evidence`,
        t.trials.map((x) => ({ label: `${t.item} ${x.label}`, score: health(x),
          tip: `${t.item} ${x.label}\n${x.score == null ? "not scored" : "degeneracy " + x.score}` })),
        (c, b) => openTrial(t, b))),
    ];
    heatBox.replaceChildren(...(rows.length ? rows : [O.emptyState("No loops or replay trials to score.")]),
      el("div", { class: "al-note" }), O.heatLegend(HEAT, "not scored"));

    /* ---- parity table ---- */
    const parityTable = el("table");
    const parityCard = el("div", { class: "card c-12" },
      el("div", { class: "card-h" }, el("h2", {}, "Stream parity"), el("span", { class: "hint" }, "streamed vs non-streamed answers, fragments and usage · click a row for its checks")),
      el("div", { class: "tbl-wrap free" }, parityTable));
    const usageCell = (p) => {
      if (!p.usage) return kit.nl("n/a");
      const a = p.usage.nonstream, b = p.usage.stream;
      return el("span", { class: "num", "data-tip": `non-stream: prompt ${a.prompt} · cached ${a.cached} · completion ${a.completion}\nstream: prompt ${b.prompt} · cached ${b.cached} · completion ${b.completion}` },
        `${a.prompt == null ? "?" : a.prompt} / ${b.prompt == null ? "?" : b.prompt}`);
    };
    O.sortableTable(parityTable, [
      { key: "item", label: "Case", render: (p) => el("span", {}, el("span", { class: "case-id" }, p.item), el("span", { class: "case-title", "data-tip": p.title }, p.title)) },
      { key: "column", label: "Target", render: (p) => el("span", { class: "mono trunc" }, p.column) },
      { key: "check", label: "Check", render: (p) => el("span", { class: "mono" }, p.check) },
      { key: "tools", label: "Tools", render: (p) => (p.tools.length ? el("span", { class: "mono" }, p.tools.join(", ")) : kit.nl("none")), value: (p) => p.tools.join(",") },
      { key: "usage", label: "Prompt tokens non-stream / stream", align: "r", render: usageCell, value: (p) => (p.usage && p.usage.stream.prompt) || 0 },
      { key: "status", label: "Status", render: (p) => kit.statusPill(p.status), value: (p) => kit.rank(p.status) },
      { key: "message", label: "Detail", render: (p) => el("span", { class: "t-meta", "data-tip": p.message }, (p.message || "").slice(0, 110)), value: (p) => p.message },
    ], parity, { label: "Stream parity", rowKey: (p) => p.key, empty: "No stream checks in the loaded runs. Run `glmkit conform run --target mock --cases 'STRM-*'` and rebuild.",
      onRowClick: (p, tr) => kit.openDrawer({ opener: tr, lead: kit.statusPill(p.status), title: `${p.item} · ${p.check}`, tag: O.pill(p.column),
        stats: [["target", p.column], ["finish (non-stream / stream)", p.finish.map((x) => x || "not logged").join(" / ")], ["reassembly splits", p.splits == null ? "n/a" : String(p.splits)]],
        main: [el("section", { class: "ev" }, el("h4", {}, "Result"), el("div", { class: "well" }, p.message || ""))], detail: kit.checksView(p.checks) }) });

    panel.replaceChildren(el("div", { class: "grid" }, loopCard, chartCard, heatCard, parityCard));
    drawChart();

    /* ---- drawers ---- */
    function openLoop(l, opener) {
      const t = el("table", { class: "al-turns" });
      kit.openDrawer({
        opener, lead: kit.statusPill(l.status), title: `${l.item} · ${l.title}`, tag: O.pill(l.column),
        strip: [
          { label: "Turns", value: String(l.turns.length) },
          { label: "Cached share", value: l.cached_share == null ? "not logged" : O.fmtPct(l.cached_share, 0) },
          { label: "Degenerate", value: String(l.degenerate_turns), tone: l.degenerate_turns ? "err" : null },
        ],
        stats: [["run", l.run_id], ["kind", l.kind]],
        main: [el("section", { class: "ev" }, el("h4", {}, "Per turn"), el("div", { class: "tbl-wrap free" }, t))],
        detail: [...(l.reason ? [el("h4", {}, "Summary"), el("div", { class: "well" }, l.reason)] : []), ...kit.checksView(l.checks)],
      });
      O.sortableTable(t, [
        { key: "label", label: "Turn", render: (r) => el("span", { class: "mono" }, r.label), value: (r) => Number(r.label.replace("turn", "")) },
        { key: "http", label: "HTTP", align: "r", render: (r) => (r.http == null ? kit.nl() : el("span", { class: "num" }, String(r.http))), value: (r) => r.http || 0 },
        { key: "prompt", label: "Prompt", align: "r", render: (r) => num(r.prompt), value: (r) => r.prompt || 0 },
        { key: "cached", label: "Cached", align: "r", render: (r) => num(r.cached), value: (r) => r.cached || 0 },
        { key: "completion", label: "Completion", align: "r", render: (r) => num(r.completion), value: (r) => r.completion || 0 },
        { key: "score", label: "Degeneracy", align: "r", render: (r) => (r.score == null ? kit.nl() : O.pill(String(r.score), r.degenerate ? "err" : null)), value: (r) => (r.score == null ? -1 : r.score) },
        { key: "finish", label: "Finish", render: (r) => (r.finish ? el("span", { class: "mono" }, r.finish) : kit.nl()) },
      ], l.turns, { label: "Turns", rowKey: (r) => r.label, sort: { key: "label", dir: 1 }, empty: "No turns recorded." });
    }
    function openTrial(t, opener) {
      kit.openDrawer({
        opener, lead: kit.statusPill(t.status), title: `${t.item} · ${t.title}`, tag: O.pill(t.column),
        strip: [
          { label: "Trials kept", value: String(t.trials.length) },
          { label: "Attempts", value: t.attempts == null ? "not logged" : String(t.attempts) },
          { label: "Pass rate", value: t.pass_rate == null ? "not logged" : O.fmtPct(t.pass_rate, 0) },
        ],
        stats: [["run", t.run_id]],
        main: [el("section", { class: "ev" }, el("h4", {}, "Trials"), O.kvList(t.trials.map((x) => [x.label, x.score == null ? "not scored" : "degeneracy " + x.score])))],
        detail: kit.checksView(t.checks),
      });
    }
  },
});
