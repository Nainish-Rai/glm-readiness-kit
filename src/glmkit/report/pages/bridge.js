/* Bridge tab: fix events by type over time, top fixes, event table with drawer detail; protocol filter (OpenAI chat / Anthropic Messages). */
GLMKit.registerPage("bridge", {
  render(panel, data, kit) {
    const { O, el } = kit;
    const events = data.events || [];

    const views = data.views || { all: data };
    const protocols = data.protocols || [{ id: "all", label: "All" }];
    const chartBody = el("div", { class: "card-b" });
    const topBody = el("div", { class: "card-b" });
    const chartCard = el("div", { class: "card c-8" },
      el("div", { class: "card-h" }, el("h2", {}, "Fix events over time"), el("span", { class: "hint" }, "stacked by fix type · hover a bar for counts")),
      chartBody);
    const topCard = el("div", { class: "card c-4" },
      el("div", { class: "card-h" }, el("h2", {}, "Top fixes"), el("span", { class: "hint" }, "pill counts fixes that could not be applied")),
      topBody);

    function drawView(id) {
      const v = views[id] || views.all;
      const chart = O.sv("svg", {});
      const leg = el("div", { class: "legend" });
      chartBody.replaceChildren(chart, leg);
      if ((v.series || []).length) {
        O.stackedBars(chart, v.series, { labels: v.labels, legend: leg, height: 240, format: O.fmtN, label: "Fix events per time bucket, stacked by fix type" });
      } else {
        chartBody.replaceChildren(O.emptyState("No fix events logged."));
      }
      topBody.replaceChildren(O.barList((v.top || []).map((t) => ({
        label: t.fix_id, value: t.count, mono: true, meta: "",
        pill: t.failed ? O.pill(`${t.failed} failed`, "err") : null, tip: `${t.title}\n${t.count} event(s)`,
      })), { empty: "No fix events yet. Point a client at `glmkit bridge serve` and rebuild." }));
      kit.setKpis(v.kpis);
    }

    const state = { q: "", failOnly: false, protocol: "all" };
    const results = el("span", { class: "results", "aria-live": "polite" });
    const search = O.searchBox({ placeholder: "Search fix, request id, summary", label: "Search fix events", onInput: (v) => { state.q = v; refresh(); } });
    const seg = el("div");
    const protoSeg = protocols.length > 2
      ? O.segmented(seg, protocols.map((p) => ({ value: p.id, label: p.label })), {
          label: "Protocol", value: "all",
          onChange: (v) => { state.protocol = v; drawView(v); refresh(); },
        })
      : null;
    const toolbar = el("div", { class: "toolbar" }, search.label, protoSeg ? seg : null,
      O.toggleChip("Failed fixes only", { onChange: (on) => { state.failOnly = on; refresh(); } }), results);
    const table = el("table");
    const tableCard = el("div", { class: "card c-12" },
      el("div", { class: "card-h" }, el("h2", {}, "Fix events"), el("span", { class: "hint" }, "newest first · click a row for the before/after summary")),
      toolbar, el("div", { class: "tbl-wrap free" }, table));
    panel.replaceChildren(el("div", { class: "grid" }, chartCard, topCard, tableCard));

    drawView("all");

    function openEvent(row, opener) {
      const rec = (data.items || {})[row.id];
      if (!rec) return;
      const e = rec.event;
      const kv = (pairs) => O.kvList(pairs.map(([k, v]) => [k, v == null || v === "" ? "not logged" : String(v)]));
      kit.openDrawer({
        opener,
        lead: O.pill(e.ok ? "applied" : "not fixed", e.ok ? "ok" : "err"),
        title: rec.title,
        tag: O.pill(e.fix_id, null, { class: "mono" }),
        strip: [
          { label: "Outcome", value: e.ok ? "applied" : "not fixed", tone: e.ok ? "ok" : "err" },
          { label: "Upstream", value: e.upstream || "not logged" },
          { label: "Request", value: e.request_id || "not logged" },
        ],
        stats: [["protocol", e.details && e.details.protocol === "anthropic" ? "Anthropic Messages" : "OpenAI chat"], ["session", rec.run.run_id], ["started", kit.fmtTime(rec.run.started_at)], ["flavor", rec.run.flavor || "not logged"], ["event time", kit.fmtTime(e.ts)]],
        main: [
          el("section", { class: "ev" }, el("h4", {}, "Before"), kit.pre(e.before || "not logged")),
          el("section", { class: "ev" }, el("h4", {}, "After"), kit.pre(e.after || "not logged")),
          el("section", { class: "ev" }, el("h4", {}, "Details"), Object.keys(e.details || {}).length ? kit.pre(kit.pretty(e.details)) : kit.nl("none")),
        ],
        detail: [
          el("h4", {}, "Content"),
          e.content ? kit.pre(kit.pretty(e.content)) : kit.nl(rec.run.log_content ? "not logged" : "not logged (session ran without --log-content)"),
        ],
      });
    }

    const t = O.sortableTable(table, [
      { key: "ts", label: "Time", render: (r) => el("span", { class: "num" }, kit.fmtTime(r.ts)) },
      { key: "fix_id", label: "Fix", render: (r) => el("span", { class: "case-id", "data-tip": r.title }, r.fix_id) },
      { key: "ok", label: "Outcome", render: (r) => O.pill(r.ok ? "applied" : "not fixed", r.ok ? "ok" : "err"), value: (r) => (r.ok ? 1 : 0) },
      { key: "request_id", label: "Request", render: (r) => el("span", { class: "mono trunc", "data-tip": r.request_id }, r.request_id || "not logged") },
      { key: "protocol", label: "Protocol", render: (r) => O.pill(r.protocol === "anthropic" ? "Anthropic" : "OpenAI") },
      { key: "upstream", label: "Upstream", render: (r) => (r.upstream ? O.pill(r.upstream) : kit.nl()) },
      { key: "after", label: "Change", render: (r) => el("span", { class: "case-title", "data-tip": `${r.before}\n→ ${r.after}` }, `${r.before} → ${r.after}`) },
    ], [], { label: "Bridge fix events", rowKey: (r) => r.id, sort: { key: "ts", dir: -1 },
      empty: events.length ? "No events match. Clear the search or turn off the filter." : "No fix events yet.",
      onRowClick: (r, tr) => openEvent(r, tr), onRender: (rows) => (results.textContent = `${rows.length} of ${events.length} events`) });
    function refresh() {
      const q = state.q.trim().toLowerCase();
      t.setRows(events.filter((e) => (state.protocol === "all" || e.protocol === state.protocol) && (!state.failOnly || !e.ok) && (!q || [e.fix_id, e.request_id, e.before, e.after, e.upstream].some((x) => (x || "").toLowerCase().includes(q)))));
    }
    refresh();
  },
});
