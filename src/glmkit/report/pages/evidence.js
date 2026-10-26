/* Evidence tab: status by framework (stacked bars), checklist gaps first, links to the evidence runs.
 * A checklist and evidence index, never legal advice: the disclaimer sits at the top of the tab. */
GLMKit.registerPage("evidence", {
  render(panel, data, kit) {
    const { O, el } = kit;
    const deps = data.deployments || [];
    const banner = el("aside", { class: "card heuristic", role: "note" },
      el("div", { class: "card-b" }, el("strong", {}, "Not legal advice. "), data.disclaimer || ""));
    if (!deps.length) {
      panel.replaceChildren(banner, O.emptyState("No evidence pack yet. Run `glmkit evidence build --deployment <file> --results results/ -o evidence/` and rebuild the dashboard."));
      return;
    }
    const STATUS = [
      { key: "gap", label: "gap", tone: "err", token: "--err" },
      { key: "needs-counsel", label: "needs-counsel", tone: "warn", token: "--warn" },
      { key: "met", label: "met", tone: "ok", token: "--ok" },
      { key: "n/a", label: "n/a", tone: null, token: "--edge-strong" },
    ];
    const tone = (s) => (STATUS.find((x) => x.key === s) || {}).tone || null;
    let cur = deps[0];
    let filter = "all";
    const seg = el("div", {});
    const filterSeg = el("div", {});
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "ev-chart");
    const legendBox = el("div", {});
    const runs = el("div", { class: "card-b" });
    const table = el("table");
    const meta = el("div", { class: "ev-meta" });
    const head = el("div", { class: "ev-head" }, deps.length > 1 ? seg : null, meta);
    const chartCard = el("div", { class: "card c-8" },
      el("div", { class: "card-h" }, el("h2", {}, "Checklist status by framework"), el("span", { class: "hint" }, "items per status · hover a bar for the counts")),
      el("div", { class: "card-b" }, legendBox, svg));
    const runCard = el("div", { class: "card c-4" },
      el("div", { class: "card-h" }, el("h2", {}, "Evidence runs"), el("span", { class: "hint" }, "linked by run id")), runs);
    const tableCard = el("div", { class: "card c-12" },
      el("div", { class: "card-h" }, el("h2", {}, "Checklist, gaps first"), el("span", { class: "hint" }, "click a row for citations, dates and evidence"), el("span", { class: "spacer" }), filterSeg),
      el("div", { class: "tbl-wrap free" }, table));
    panel.replaceChildren(banner, head, el("div", { class: "grid" }, chartCard, runCard, tableCard));

    const short = (t) => t.replace(/ \(.*\)$/, "").replace("Governance artifacts", "Governance").replace("US export controls", "US EAR");
    let chart = null;
    function drawChart() {
      const fw = (cur.frameworks || []).filter((f) => f.total);
      const series = STATUS.map((s) => ({ name: s.label, color: s.token, values: fw.map((f) => f[s.key === "n/a" ? "na" : s.key] || 0) }));
      const labels = fw.map((f) => short(f.title));
      if (chart) chart.destroy();
      svg.replaceChildren();
      chart = O.stackedBars(svg, series, { labels, height: 260, format: (v) => String(Math.round(v)), label: "Checklist items by status per framework", legend: legendBox });
    }

    function drawRuns() {
      const idx = cur.evidence_index || [];
      if (!idx.length) { runs.replaceChildren(O.emptyState("No runs linked: claims have no reproducible run behind them yet.")); return; }
      runs.replaceChildren(el("ul", { class: "list-plain" }, idx.map((r) => el("li", { "data-tip": `${r.path}\n${(r.linked_items || []).join(", ") || "not linked to an item"}` },
        kit.statusPill(r.verdict), O.pill(r.component), el("span", { class: "mono trunc" }, r.run_id),
        el("span", { class: "nl" }, (r.linked_items || []).length ? `${r.linked_items.length} item(s)` : "unlinked")))));
    }

    const cite = (c) => el("li", {}, O.pill(c.reliability || "S"),
      /^https?:/.test(c.url || "") ? el("a", { href: c.url, target: "_blank", rel: "noopener" }, c.label) : el("span", {}, c.label),
      el("span", { class: "nl" }, `${c.article || "no article"} · ${c.date || "undated"}`));

    function openItem(it, opener) {
      kit.openDrawer({
        opener, lead: O.pill(it.status, tone(it.status)), title: `${it.id} · ${it.title}`, tag: O.pill(it.framework),
        strip: [
          { label: "Status", value: it.status, tone: tone(it.status) },
          { label: "Interpretive", value: it.interpretive ? "yes" : "no" },
          { label: "Attested", value: it.attested ? "yes" : "no" },
          { label: "Evidence", value: String((it.evidence || []).length) },
        ],
        stats: [["deployment", cur.name], ["pack run", cur.run_id], ["in force", it.in_force || "not logged"]],
        main: [
          el("section", { class: "ev" }, el("h4", {}, "Why this status"), el("div", { class: "well" }, it.reason || "not logged")),
          el("section", { class: "ev" }, el("h4", {}, "Prompts for the reviewer"), el("ul", { class: "list-plain" }, (it.prompts || []).map((p) => el("li", {}, p)))),
          ...(it.flags && it.flags.length ? [el("section", { class: "ev" }, el("h4", {}, "Unverified or caveat"), el("ul", { class: "list-plain" }, it.flags.map((f) => el("li", {}, f))))] : []),
        ],
        detail: [
          el("h4", {}, "Citations"), el("ul", { class: "list-plain" }, (it.citations || []).map(cite)),
          el("h4", {}, "Evidence"),
          (it.evidence || []).length
            ? el("ul", { class: "list-plain" }, it.evidence.map((e) => el("li", {}, O.pill(e.kind), el("span", { class: "mono" }, e.ref), e.label ? el("span", { class: "nl" }, e.label) : null)))
            : kit.nl("none linked"),
          el("p", { class: "nl" }, data.disclaimer || ""),
        ],
      });
    }

    let tbl = null;
    function drawTable() {
      const rows = cur.items.filter((i) => filter === "all" || i.status === filter);
      const cols = [
        { key: "status", label: "Status", render: (r) => O.pill(r.status, tone(r.status)), value: (r) => STATUS.findIndex((s) => s.key === r.status) },
        { key: "id", label: "Item", render: (r) => el("span", { class: "case-id" }, r.id) },
        { key: "title", label: "Title", render: (r) => el("span", { class: "case-title" }, r.title) },
        { key: "framework", label: "Framework", render: (r) => O.pill(r.framework) },
        { key: "reason", label: "Why", render: (r) => el("span", { class: "case-title", "data-tip": r.reason }, r.reason || "not logged"), value: (r) => r.reason || "" },
        { key: "evidence", label: "Evidence", align: "r", render: (r) => el("span", { class: "num" }, String((r.evidence || []).length)), value: (r) => (r.evidence || []).length },
      ];
      tbl = O.sortableTable(table, cols, rows, { sort: { key: "status", dir: 1 }, rowKey: (r) => r.id, label: "Checklist items",
        empty: "No items with this status.", onRowClick: (r, tr) => { tbl.select(r.id); openItem(r, tr); } });
    }

    function drawMeta() {
      meta.replaceChildren(O.pill(cur.path_title || "path not logged"),
        O.pill(`${(cur.hosting || {}).provider || "provider not logged"} · ${(cur.hosting || {}).region || "region not logged"}`),
        O.pill(cur.bom_valid ? "AI-BOM valid" : "AI-BOM not validated", cur.bom_valid ? "ok" : "warn"),
        el("span", { class: "nl" }, `pack run ${cur.run_id} · ${cur.pack_dir || ""}`));
    }

    function show(d) { cur = d; data._cur = d; drawMeta(); drawChart(); drawRuns(); drawTable(); kit.setKpis(d.kpis); }
    if (deps.length > 1) O.segmented(seg, deps.map((d) => ({ value: d.name, label: d.name })), { value: cur.name, label: "Deployment", onChange: (v) => show(deps.find((d) => d.name === v)) });
    O.segmented(filterSeg, [{ value: "all", label: "all" }, ...STATUS.map((s) => ({ value: s.key, label: s.label }))], { value: "all", label: "Filter by status", onChange: (v) => { filter = v; drawTable(); } });
    show(cur);
  },
  onShow(data, kit) { if (data._cur) kit.setKpis(data._cur.kpis); },
});
