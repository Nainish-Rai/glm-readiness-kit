/* Errors tab: failures grouped by Z.ai error-taxonomy category, token-accounting ratio per corpus, taxonomy table. */
GLMKit.registerPage("errors", {
  render(panel, data, kit) {
    const { O, el } = kit;
    const open = (ref, opener) => {
      const rec = (data.items || {})[ref];
      if (rec) kit.openItem(rec.item, rec.run, opener);
    };

    // ---- failures by category (error-group accordion)
    const groupsBox = el("div", { class: "card-b" });
    const failCard = el("div", { class: "card c-7" },
      el("div", { class: "card-h" }, el("h2", {}, "Failures by category"),
        el("span", { class: "hint" }, "taxonomy category › code and HTTP status · click a case to open its evidence")),
      groupsBox);
    O.errorGroups(groupsBox, data.groups || [], {
      onSession: (ref, btn) => open(ref, btn),
      empty: "No failing items in the latest runs. Plant a defect with `glmkit conform run --target mock --mock-defect NAME`.",
    });

    // ---- token accounting ratio per corpus
    const ratioBox = el("div", { class: "card-b stack" });
    const ratioCard = el("div", { class: "card c-5" },
      el("div", { class: "card-h" }, el("h2", {}, "Token accounting"),
        el("span", { class: "hint" }, "reported ÷ reference tokens per corpus · flag above 1.3× (ANTH-004)")),
      ratioBox);
    const ratios = data.ratios || [];
    if (!ratios.length) ratioBox.append(O.emptyState("No ANTH-004 run yet. Run `glmkit conform run --target mock-anthropic --cases ANTH-004`."));
    ratios.forEach((r) => {
      const rows = r.rows.map((x) => ({
        label: x.corpus, value: x.ratio == null ? 0 : x.ratio, mono: true,
        meta: x.tokens == null ? "not logged" : `${x.tokens} vs ${x.reference == null ? "not logged" : x.reference} tokens`,
        tone: x.ratio != null && x.ratio > r.limit ? "err" : null,
        pill: x.ratio == null ? O.pill("no reference", "heur") : x.ratio > r.limit ? O.pill("over limit", "err") : null,
        tip: `${x.corpus}\nreported ${x.tokens ?? "not logged"} tokens · reference ${x.reference ?? "not logged"}\nratio ${x.ratio ?? "not logged"} · ${x.bytes} bytes`,
      }));
      const max = Math.max(r.limit * 1.25, ...rows.map((x) => x.value));
      ratioBox.append(el("div", { class: "ratio-set" },
        el("h3", { class: "ratio-h" }, kit.statusPill(r.status), el("span", { class: "mono" }, r.column)),
        O.barList(rows, { max, format: (v) => (v ? v.toFixed(2) + "×" : "not logged") }),
        el("div", { class: "hint" }, `limit ${r.limit}× of the reference (the host's count_tokens)`)));
    });

    // ---- taxonomy reference
    const table = el("table");
    const taxCard = el("div", { class: "card c-12" },
      el("div", { class: "card-h" }, el("h2", {}, "Error taxonomy"),
        el("span", { class: "hint" }, "Z.ai business codes › category, retry, suggested HTTP status (glmkit.zai_errors, reused by the bridge)")),
      el("div", { class: "tbl-wrap free" }, table));
    const cats = data.categories || {};
    O.sortableTable(table, [
      { key: "code", label: "Code", render: (r) => el("span", { class: "mono" }, r.code) },
      { key: "category", label: "Category", render: (r) => el("span", { "data-tip": (cats[r.category] || {}).meaning || null }, r.title) },
      { key: "retry", label: "Retry", render: (r) => O.pill(r.retry_strategy === "none" ? "no" : r.retry_strategy.replace(/_/g, " "), r.retryable ? "warn" : null), value: (r) => (r.retryable ? 1 : 0) },
      { key: "upstream_http", label: "Z.ai HTTP", align: "r", render: (r) => el("span", { class: "num" }, String(r.upstream_http)) },
      { key: "suggested_http", label: "Suggested HTTP", align: "r", render: (r) => el("span", { class: "num" }, String(r.suggested_http)) },
      { key: "meaning", label: "Meaning", render: (r) => el("span", { class: "t-meta" }, r.meaning) },
    ], data.taxonomy || [], { label: "Error taxonomy", rowKey: (r) => r.code, sort: { key: "code", dir: 1 } });

    panel.replaceChildren(el("div", { class: "grid" }, failCard, ratioCard, taxCard));
  },
});
