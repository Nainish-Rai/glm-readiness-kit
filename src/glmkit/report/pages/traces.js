/* Traces tab: imported sets, replayable share (composition bar), task-type mix (bar list), token totals by source. */
GLMKit.registerPage("traces", {
  render(panel, data, kit) {
    const { O, el } = kit;
    const t = data.totals;

    const setsTable = el("table");
    const sets = el("div", { class: "card c-8" },
      el("div", { class: "card-h" }, el("h2", {}, "Imported sets"), el("span", { class: "hint" }, "newest first · click a row for details")),
      el("div", { class: "tbl-wrap free" }, setsTable));

    const share = el("div", { class: "card c-4" },
      el("div", { class: "card-h" }, el("h2", {}, "Replayable share"), el("span", { class: "hint" }, "prompt content present or not")),
      el("div", { class: "card-b" }, O.compositionBar([
        { label: "Replayable", value: t.replayable, color: "--cat-3" },
        { label: "Baseline-only", value: t.baseline_only, color: "--cat-6" },
      ], { format: O.fmtN, label: "Replayable share of traces" })));

    const mix = el("div", { class: "card c-6" },
      el("div", { class: "card-h" }, el("h2", {}, "Task-type mix"), el("span", { class: "hint" }, "traces per task type")),
      el("div", { class: "card-b" }, O.barList(data.task_types.map((x) => ({
        label: x.name, value: x.count, meta: `${O.fmtN(x.count)} trace${x.count === 1 ? "" : "s"}`,
        pill: x.name === "unlabelled" ? O.pill("unlabelled", "warn") : null, tip: `${x.name}\n${x.count} traces`,
      })), { empty: "No traces yet." })));

    const tokens = el("div", { class: "card c-6" },
      el("div", { class: "card-h" }, el("h2", {}, "Token totals by source"), el("span", { class: "hint" }, "input is total, cache included")),
      el("div", { class: "card-b" }, O.barList(data.tokens_by_source.map((x) => ({
        label: x.source, value: x.input_tokens_total,
        meta: `in ${O.fmtK(x.input_tokens_total)} · cache read ${O.fmtK(x.cache_read_tokens)} · out ${O.fmtK(x.output_tokens_total)}`,
        tip: `${x.source}\ninput (total) ${O.fmtN(x.input_tokens_total)}\ncache read ${O.fmtN(x.cache_read_tokens)}\ncache write ${O.fmtN(x.cache_write_tokens)}\noutput ${O.fmtN(x.output_tokens_total)}\nreasoning ${O.fmtN(x.reasoning_tokens)}`,
      })), { empty: "No token usage logged." })));

    panel.replaceChildren(el("div", { class: "grid" }, sets, share, mix, tokens));

    const tbl = O.sortableTable(setsTable, [
      { key: "started_at", label: "Imported", render: (r) => el("span", { class: "num" }, kit.fmtTime(r.started_at)), value: (r) => r.started_at },
      { key: "name", label: "Set", render: (r) => el("span", { class: "mono trunc", "data-tip": r.name }, r.name) },
      { key: "source", label: "Source", render: (r) => O.pill(r.source) },
      { key: "total", label: "Traces", align: "r", render: (r) => el("span", { class: "num" }, O.fmtN(r.total)) },
      { key: "replayable", label: "Replayable", align: "r", render: (r) => O.splitBar(r.replayable, r.baseline_only, { label: true }), value: (r) => r.replayable },
      { key: "tokens", label: "Input tokens", align: "r", render: (r) => el("span", { class: "num" }, O.fmtK(r.tokens.input_tokens_total)), value: (r) => r.tokens.input_tokens_total },
      { key: "redacted", label: "Redacted", render: (r) => (r.redacted ? O.pill(`${r.redacted} redacted`, "ok") : kit.nl("no")), value: (r) => r.redacted },
    ], data.sets, { sort: { key: "started_at", dir: -1 }, label: "Imported trace sets", empty: "No trace sets imported. Run `glmkit traces import`.",
      onRowClick: (r, tr) => open(r, tr) });

    function open(r, opener) {
      tbl.select(r.id);
      const skipped = Object.entries(r.skipped);
      kit.openDrawer({
        opener, onClose: () => tbl.select(null),
        lead: O.pill(r.source), title: r.name, tag: r.baseline_only ? O.pill(`${r.baseline_only} baseline-only`, "warn") : null,
        strip: [
          { label: "Traces", value: String(r.total) }, { label: "Replayable", value: String(r.replayable), tone: "ok" },
          { label: "Baseline-only", value: String(r.baseline_only), tone: r.baseline_only ? "warn" : null },
          { label: "Redacted", value: String(r.redacted) },
        ],
        stats: [["imported", kit.fmtTime(r.started_at)], ["output", r.output || "not logged"], ["run", r.id],
          ["reported cost", r.cost_usd_reported == null ? "not logged" : `$${r.cost_usd_reported}`]],
        main: [
          el("section", { class: "ev" }, el("h4", {}, "Tokens"), O.kvList(Object.entries(r.tokens).map(([k, v]) => [k, O.fmtN(v)]))),
          el("section", { class: "ev" }, el("h4", {}, "Task types"), O.barList(Object.entries(r.by_task_type).map(([k, v]) => ({ label: k, value: v, meta: String(v) })), { empty: "none" })),
          el("section", { class: "ev" }, el("h4", {}, "Skipped rows"), skipped.length ? O.kvList(skipped.map(([k, v]) => [k, String(v)])) : kit.nl("none")),
          el("section", { class: "ev" }, el("h4", {}, "Redactions"), Object.keys(r.redactions).length ? O.kvList(Object.entries(r.redactions).map(([k, v]) => [k, String(v)])) : kit.nl("redaction off")),
        ],
        detail: [],
      });
    }
  },
});
