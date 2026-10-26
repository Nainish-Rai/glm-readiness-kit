/* Engagement tab: the brief, the step checklist (state comes from files and results), the last demo run's mode,
 * and links to every other tab. */
GLMKit.registerPage("engagement", {
  render(panel, data, kit) {
    const { O, el } = kit;
    const tabs = ((kit.site && kit.site.pages) || []).filter((p) => p.id !== "engagement");
    const hasTab = (id) => tabs.some((p) => p.id === id);
    const go = (id) => { const b = document.getElementById("tab-" + id); if (b) b.click(); };

    const stepRow = (s) => el("li", {},
      O.pill(s.done ? "done" : "pending", s.done ? "ok" : "warn"),
      el("div", { class: "eg-step-t" },
        el("strong", {}, s.title),
        el("span", {}, `${s.week} · `, s.done ? s.evidence : kit.nl("not done")),
        s.done ? null : el("code", {}, s.command),
        s.verdict ? el("span", {}, "latest run verdict: ", kit.statusPill(s.verdict), s.detail ? ` · ${s.detail}` : "") : null),
      s.tab && hasTab(s.tab)
        ? el("button", { type: "button", class: "chip", "aria-label": `Open the ${s.tab} tab`, onclick: () => go(s.tab) }, "open " + (tabs.find((p) => p.id === s.tab) || {}).title)
        : el("span", {}));
    const checklist = el("div", { class: "card c-8" },
      el("div", { class: "card-h" }, el("h2", {}, `Checklist · ${data.customer}`), el("span", { class: "hint" }, "done means the evidence is on disk")),
      el("ul", { class: "eg-steps" }, data.steps.map(stepRow)));

    const lr = data.last_run;
    const mock = lr && lr.targets === "mock";
    const modeCard = el("div", { class: "card c-4" + (mock ? " heuristic" : "") },
      el("div", { class: "card-h" }, el("h2", {}, "Last demo run"), el("span", { class: "hint" }, mock ? "mock targets" : "")),
      lr ? el("div", { class: "eg-note" },
        el("p", {}, "Targets: ", mock ? "mock server with planted quality. These results show how the kit reports, not how GLM performs." : (lr.targets || []).join(", ")),
        ...lr.sets.map((s) => el("p", {}, O.pill(s.mode, s.mode === "fetched" ? "ok" : "warn"), ` ${s.set} · ${s.items} items` + (s.revision ? ` · rev ${s.revision}` : ""))),
        lr.modes.includes("stand-in") ? el("p", {}, "Stand-in items are small synthetic sets bundled with the kit. `glmkit demo fetch all` downloads the licensed sets.") : null)
        : el("div", { class: "eg-note" }, el("p", {}, kit.nl("no demo run"), " Run `glmkit demo run` in this workspace.")));

    const brief = el("div", { class: "card c-8" },
      el("div", { class: "card-h" }, el("h2", {}, "Engagement brief"), el("span", { class: "hint" }, data.brief_todos ? `${data.brief_todos} TODO line(s) left in brief.md` : "complete")),
      el("div", { class: "eg-brief" }, data.brief.length
        ? data.brief.map((b) => el("section", {}, el("h4", {}, b.title), el("p", { class: b.text.includes("TODO") ? "todo" : "" }, b.text || "not logged")))
        : el("p", {}, kit.nl("brief.md is missing"))));

    const links = el("div", { class: "card c-4" },
      el("div", { class: "card-h" }, el("h2", {}, "Components"), el("span", { class: "hint" }, "one tab each")),
      el("div", { class: "eg-links" }, tabs.length
        ? tabs.map((p) => el("button", { type: "button", class: "chip", onclick: () => go(p.id) }, p.title, p.count != null ? ` (${p.count})` : ""))
        : kit.nl("no other tabs")));

    panel.replaceChildren(el("div", { class: "grid" }, checklist, modeCard, brief, links));
  },
});
