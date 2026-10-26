/* Scorecard tab: provider x category pass rates (Wilson 95% CI), hard-gate failures first, schema-shape sensitivity.
   Every cell opens the worst item of that provider and category in the shared evidence drawer. */
GLMKit.registerPage("scorecard", {
  render(panel, data, kit) {
    const { O, el } = kit;
    const providers = data.providers || [];
    const cats = data.categories || [];
    const TH = [95, 80]; // heat thresholds, in percent
    const open = (ref, opener) => {
      const rec = (data.items || {})[ref];
      if (rec) kit.openItem(rec.item, rec.run, opener);
    };
    const pct = (r) => (r == null ? null : `${(r * 100).toFixed(r === 0 || r === 1 ? 0 : 1)}%`);
    const tone = (r) => (r == null ? null : r * 100 >= TH[0] ? "ok" : r * 100 >= TH[1] ? "warn" : "err");
    const ci = (c) => (c.rate == null ? "" : `${(c.ci_low * 100).toFixed(0)}–${(c.ci_high * 100).toFixed(0)}%`);
    const provLabel = (p) => (p.defects.length ? `${p.profile} +${p.defects.length} defect${p.defects.length === 1 ? "" : "s"}` : p.profile);
    const provName = (p) => el("span", { class: "sc-name" },
      el("span", { class: "mono", "data-tip": p.key }, provLabel(p)),
      el("span", { class: "t-meta" }, [p.model, p.protocol, p.endpoint_class].filter(Boolean).join(" · ")));

    /* 1 · hard gates */
    const gateBody = el("div", { class: "tbl-wrap free" });
    if ((data.hard_gates || []).length) {
      const table = el("table");
      gateBody.replaceChildren(table);
      O.sortableTable(table, [
        { key: "provider", label: "Provider", render: (r) => { const p = providers.find((x) => x.key === r.provider); return el("span", { class: "mono", "data-tip": r.provider }, p ? provLabel(p) : r.provider); }, value: (r) => r.provider },
        { key: "id", label: "Case", render: (r) => el("span", {}, el("span", { class: "case-id" }, r.id), el("span", { class: "case-title", "data-tip": r.title }, r.title)) },
        { key: "category", label: "Category", render: (r) => el("span", { class: "t-meta" }, r.category || "not logged") },
        { key: "reason", label: "Why it failed", render: (r) => el("span", { class: "t-meta", "data-tip": r.reason || null }, r.reason ? r.reason.slice(0, 110) : "not logged") },
      ], data.hard_gates, { label: "Hard-gate failures", rowKey: (r) => r.provider + r.id, empty: "No hard-gate failures.",
        onRowClick: (r, tr) => open(r.ref, tr) });
    } else {
      gateBody.replaceChildren(el("p", { class: "sc-note" }, O.pill("clear", "ok"), " Every critical case passed on every provider."));
    }
    const gates = el("div", { class: "card c-12" },
      el("div", { class: "card-h" }, el("h2", {}, "Hard gates"),
        el("span", { class: "hint" }, "critical cases cannot be averaged away · one failure makes the provider's verdict fail · click a row for the request and response")),
      gateBody);

    /* 2 · provider x category heat strip */
    const strips = el("div", { class: "sc-rows card-b" });
    providers.forEach((p) => {
      const heat = el("div");
      O.heatStrip(heat, cats.map((c) => {
        const cell = p.cells[c.name];
        const score = cell && cell.rate != null ? Math.round(cell.rate * 1000) / 10 : null;
        const tip = cell
          ? `${c.index} · ${c.name}\n${cell.rate == null ? "not scored" : `${pct(cell.rate)} (${cell.passes}/${cell.trials} trials) · CI ${ci(cell)}`}\n${cell.cases} case${cell.cases === 1 ? "" : "s"}${cell.failing ? ` · ${cell.failing} failing` : ""}`
          : `${c.index} · ${c.name}\nnot logged`;
        return { id: c.name, label: `${provLabel(p)}, ${c.name}`, score, tip, ref: cell ? cell.ref : null };
      }), { thresholds: TH, onSelect: (c, btn) => c.ref && open(c.ref, btn) });
      const o = p.overall;
      strips.append(el("div", { class: "sc-row" }, provName(p), heat,
        el("span", { class: "sc-rate" }, o.rate == null ? kit.nl() : pct(o.rate),
          el("small", {}, o.rate == null ? "" : `${o.passes}/${o.trials} · CI ${ci(o)}`), kit.statusPill(p.verdict))));
    });
    const heatCard = el("div", { class: "card c-12" },
      el("div", { class: "card-h" }, el("h2", {}, "Provider × category"),
        el("span", { class: "hint" }, "one cell per category, in the order numbered in the table below · tone is the pass rate · click a cell for its worst case")),
      strips, el("div", { class: "card-b" }, O.heatLegend(TH, "not logged")));

    /* 3 · pass rate per category with CI */
    const table = el("table");
    const catRows = cats.map((c) => ({ ...c, cells: providers.map((p) => p.cells[c.name] || null) }));
    const columns = [
      { key: "index", label: "#", align: "r", render: (r) => el("span", { class: "num" }, String(r.index)) },
      { key: "name", label: "Category", render: (r) => el("span", { class: "case-id" }, r.name) },
      ...providers.map((p, i) => ({
        key: "p" + i, label: provLabel(p), align: "r",
        value: (r) => (r.cells[i] && r.cells[i].rate != null ? r.cells[i].rate : -1),
        render: (r) => {
          const c = r.cells[i];
          if (!c || c.rate == null) return kit.nl();
          return el("button", { type: "button", class: "cell-btn", "aria-label": `${r.name} on ${provLabel(p)}: ${pct(c.rate)}, CI ${ci(c)}. Open the worst case`,
            "data-tip": `${r.name} · ${provLabel(p)}\n${c.passes}/${c.trials} trials passed\nWilson 95% CI ${ci(c)}`,
            onclick: (e) => { e.stopPropagation(); open(c.ref, e.currentTarget); } },
          O.pill(pct(c.rate), tone(c.rate)), " ", el("span", { class: "sc-ci" }, ci(c), ` · n=${c.trials}`));
        },
      })),
    ];
    O.sortableTable(table, columns, catRows, { label: "Pass rate by category", rowKey: (r) => r.name, sort: { key: "index", dir: 1 },
      empty: "No scored categories yet.",
      onRowClick: (r, tr) => {
        const cell = r.cells.filter(Boolean).sort((a, b) => kit.rank(a.worst) - kit.rank(b.worst))[0];
        if (cell) open(cell.ref, tr);
      } });
    const rates = el("div", { class: "card c-12" },
      el("div", { class: "card-h" }, el("h2", {}, "Pass rate by category"),
        el("span", { class: "hint" }, "statistical cases count every attempt · Wilson 95% interval · soft failures, skips and throttled calls are not scored")),
      el("div", { class: "tbl-wrap free" }, table));

    /* 4 · schema-shape sensitivity (TOOL-006) */
    const cards = [gates, heatCard, rates];
    if ((data.shapes || []).length) {
      const body = el("div", { class: "sc-rows card-b" });
      data.shapes.forEach((s) => {
        const p = providers.find((x) => x.key === s.provider) || { key: s.provider, profile: s.provider, defects: [] };
        body.append(el("div", { class: "sc-row" }, provName(p),
          el("div", { class: "sc-shapes" }, s.shapes.map((sh) => el("button", { type: "button", class: "sc-shape",
            "aria-label": `${sh.shape} schema shape on ${provLabel(p)}: ${pct(sh.rate)} passed. Open the worst case`,
            "data-tip": `${sh.shape}${sh.shape === "flat" ? " (control)" : ""}\n${pct(sh.rate)} of ${sh.n} runs passed${sh.sensitivity == null ? "" : `\nsensitivity ${(sh.sensitivity * 100).toFixed(0)} pts`}`,
            onclick: (e) => open(sh.ref, e.currentTarget) },
          O.pill(sh.shape, sh.rate === 1 ? "ok" : "err"), sh.sensitivity ? el("span", { class: "sc-ci" }, `${sh.sensitivity > 0 ? "−" : "+"}${Math.abs(sh.sensitivity * 100).toFixed(0)} pts`) : null))),
          el("span", { class: "sc-rate" }, pct(s.control), el("small", {}, "flat control"))));
      });
      cards.push(el("div", { class: "card c-12" },
        el("div", { class: "card-h" }, el("h2", {}, "Schema-shape sensitivity"),
          el("span", { class: "hint" }, "TOOL-006 · same intent as oneOf, anyOf, allOf and $ref shapes · sensitivity is the flat control's pass rate minus the shape's")),
        body));
    }
    panel.replaceChildren(el("div", { class: "grid" }, ...cards));
  },
});
