/* Conformance tab: case x target matrix; a cell opens that item's evidence in the drawer. */
GLMKit.registerPage("conformance", {
  render(panel, data, kit) {
    const { O, el } = kit;
    const cols = data.columns || [];
    const all = data.rows || [];
    const state = { q: "", failOnly: false };
    const results = el("span", { class: "results", "aria-live": "polite" });
    const search = O.searchBox({ placeholder: "Search case id, title, category", label: "Search cases", onInput: (v) => { state.q = v; refresh(); } });
    const toolbar = el("div", { class: "toolbar" }, search.label,
      O.toggleChip("Failures only", { onChange: (on) => { state.failOnly = on; refresh(); } }), results);
    const table = el("table");
    panel.replaceChildren(el("div", { class: "card" },
      el("div", { class: "card-h" }, el("h2", {}, "Case × target"), el("span", { class: "hint" }, "latest run per cell · a column per target profile (+ planted mock defects) · click a cell for evidence")),
      toolbar, el("div", { class: "tbl-wrap free" }, table)));

    const open = (ref, opener) => {
      const rec = (data.items || {})[ref];
      if (rec) kit.openItem(rec.item, rec.run, opener);
    };
    const cell = (row, col) => {
      const c = row.cells[col.key];
      if (!c) return kit.nl();
      return el("button", { type: "button", class: "cell-btn", "aria-label": `${row.id} on ${col.key}: ${kit.label(c.status)}. Open evidence`,
        "data-tip": `${row.id} · ${col.key}\n${kit.label(c.status)}${c.reason ? "\n" + c.reason : ""}`,
        onclick: (e) => { e.stopPropagation(); open(c.ref, e.currentTarget); } }, kit.statusPill(c.status));
    };
    const worst = (row) => Math.min(...Object.values(row.cells).map((c) => kit.rank(c.status)), 9);
    const columns = [
      { key: "id", label: "Case", render: (r) => el("span", {}, el("span", { class: "case-id" }, r.id), el("span", { class: "case-title", "data-tip": r.title }, r.title)) },
      { key: "severity", label: "Severity", render: (r) => (r.severity ? O.pill(r.severity, r.severity === "critical" ? "err" : r.severity === "major" ? "warn" : null) : kit.nl()),
        value: (r) => ({ critical: 0, major: 1, minor: 2, info: 3 })[r.severity] ?? 9 },
      { key: "category", label: "Category", render: (r) => el("span", { class: "t-meta" }, r.category || "not logged") },
      ...cols.map((col) => ({ key: "col:" + col.key, label: col.key, render: (r) => cell(r, col), value: (r) => (r.cells[col.key] ? kit.rank(r.cells[col.key].status) : 9) })),
    ];
    const t = O.sortableTable(table, columns, [], { label: "Conformance matrix", rowKey: (r) => r.id,
      empty: cols.length ? "No cases match. Clear the search or turn off the filter." : "No conformance runs yet. Run `glmkit conform run --target mock` and rebuild.",
      onRowClick: (r, tr) => {
        const c = Object.values(r.cells).sort((a, b) => kit.rank(a.status) - kit.rank(b.status))[0];
        if (c) open(c.ref, tr);
      },
      onRender: (rows) => (results.textContent = `${rows.length} of ${all.length} rows`) });
    function refresh() {
      const q = state.q.trim().toLowerCase();
      t.setRows(all.filter((r) => (!state.failOnly || worst(r) <= 1) && (!q || [r.id, r.title, r.category].some((x) => (x || "").toLowerCase().includes(q)))));
    }
    refresh();
  },
});
