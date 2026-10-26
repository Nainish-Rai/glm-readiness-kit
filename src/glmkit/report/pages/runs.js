/* Runs tab: one table row per run (any component), drawer with run detail. */
GLMKit.registerPage("runs", {
  render(panel, data, kit) {
    const { O, el } = kit;
    const rows = data.rows || [];
    const table = el("table");
    const tableCard = el("div", { class: "card c-8" },
      el("div", { class: "card-h" }, el("h2", {}, "Every run"), el("span", { class: "hint" }, "newest first · click a row for its items and config")),
      el("div", { class: "tbl-wrap free" }, table));
    const comp = el("div", { class: "card-b" });
    const aside = el("div", { class: "card c-4" },
      el("div", { class: "card-h" }, el("h2", {}, "Runs by component"), el("span", { class: "hint" }, "pill counts failing runs")),
      comp);
    panel.replaceChildren(el("div", { class: "grid" }, tableCard, aside));
    comp.replaceChildren(O.barList((data.components || []).map((c) => ({
      label: c.name, value: c.runs, meta: `${c.runs} run${c.runs === 1 ? "" : "s"}`,
      pill: c.failing ? O.pill(`${c.failing} failing`, "err") : null, tip: `${c.name}\n${c.runs} runs · ${c.failing} failing`,
    })), { empty: "No runs yet." }));

    const target = (r) => r.target
      ? el("span", { class: "tool-badge" }, el("span", { class: "mono" }, r.target.profile), O.pill(r.target.endpoint_class), r.defects.length ? O.pill("defect", "warn", { "data-tip": r.defects.join(", ") }) : null)
      : kit.nl();
    const t = O.sortableTable(table, [
      { key: "started_at", label: "Started", render: (r) => el("span", { class: "num" }, kit.fmtTime(r.started_at)) },
      { key: "run_id", label: "Run", render: (r) => el("span", { class: "mono trunc", "data-tip": r.run_id }, r.run_id) },
      { key: "component", label: "Component", render: (r) => O.pill(r.component) },
      { key: "target", label: "Target", render: target, value: (r) => (r.target ? r.target.profile : "") },
      { key: "items", label: "Items", align: "r", render: (r) => O.splitBar(r.summary.passed, r.summary.failed + r.summary.errored, { label: true }), value: (r) => r.summary.total },
      { key: "verdict", label: "Verdict", render: (r) => kit.statusPill(r.verdict), value: (r) => kit.rank(r.verdict) },
    ], rows, { sort: { key: "started_at", dir: -1 }, label: "Runs", empty: "No runs found. Run `glmkit conform run --target mock` and rebuild.",
      onRowClick: (r, tr) => openRun(r, tr) });

    function openRun(r, opener) {
      t.select(r.id);
      const s = r.summary;
      kit.openDrawer({
        opener, onClose: () => t.select(null),
        lead: kit.statusPill(r.verdict), title: r.run_id, tag: O.pill(r.component),
        strip: [
          { label: "Items", value: String(s.total) }, { label: "Passed", value: String(s.passed), tone: "ok" },
          { label: "Failed", value: String(s.failed), tone: s.failed ? "err" : null }, { label: "Soft", value: String(s.soft_failed), tone: s.soft_failed ? "warn" : null },
          { label: "Skipped", value: String(s.skipped) }, { label: "Duration", value: r.duration_s == null ? "not logged" : O.fmtDur(r.duration_s) },
        ],
        stats: [["started", kit.fmtTime(r.started_at)], ["kit", r.kit_version], ["file", r.path],
          ...(r.target ? [["target", r.target.profile], ["model", r.target.model], ["protocol", r.target.protocol], ["class", r.target.endpoint_class]] : [])],
        main: [
          el("section", { class: "ev" }, el("h4", {}, "Target"), r.target ? O.kvList(Object.entries(r.target).map(([k, v]) => [k, v == null ? "not logged" : String(v)])) : kit.nl()),
          el("section", { class: "ev" }, el("h4", {}, "Config"), kit.pre(kit.pretty(r.config))),
          el("section", { class: "ev" }, el("h4", {}, "Environment"), O.kvList(Object.entries(r.environment || {}))),
          el("section", { class: "ev" }, el("h4", {}, "Artifacts"), r.artifacts.length ? O.kvList(r.artifacts.map((a) => [a.kind, a.path])) : kit.nl("none")),
        ],
        detail: [el("h4", {}, "Items"), el("ul", { class: "list-plain" }, r.items.map((i) => el("li", { "data-tip": i.reason || null }, kit.statusPill(i.status), el("span", { class: "mono" }, i.id))))],
      });
    }
  },
});
