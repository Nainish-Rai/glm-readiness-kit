/* GLMKit dashboard runtime (inlined into index.html before the page scripts).
 * Exposes window.GLMKit: registerPage(), the shared drawer, evidence and check renderers, and formatters.
 * Page scripts call GLMKit.registerPage(id, { render(panel, data, kit), onShow?(data, kit) }).
 * Colours are token names only; animation comes from Observatory components, which honour reduced().
 */
(() => {
"use strict";
const O = window.Observatory;
const { el } = O;
const DATA = window.GLMKIT_DATA || {};
const SITE = DATA.__site || { pages: [] };
const IMPLS = {};

const TONE = { pass: "ok", fail: "err", soft_fail: "warn", error: "err", throttled: "warn", skip: null, warn: "warn", incomplete: null };
const LABEL = { pass: "pass", fail: "fail", soft_fail: "soft fail", error: "error", throttled: "throttled", skip: "skipped", warn: "warn", incomplete: "incomplete" };
const RANK = { fail: 0, error: 1, soft_fail: 2, throttled: 3, warn: 3, pass: 4, skip: 5, incomplete: 5 };

const nl = (text) => el("span", { class: "nl" }, text || "not logged");
const statusPill = (s) => (s ? O.pill(LABEL[s] || s, TONE[s] || null) : nl());
const fmtMs = (ms) => (ms == null ? "not logged" : O.fmtDur(ms / 1000));
const fmtTime = (iso) => {
  if (!iso) return "not logged";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
};
const pretty = (v) => {
  if (v == null) return "";
  if (typeof v === "string") {
    try { return JSON.stringify(JSON.parse(v), null, 2); } catch (e) { return v; }
  }
  return JSON.stringify(v, null, 2);
};
const pre = (text) => el("pre", { class: "well ev-pre" }, text);
const FORMATS = { n: O.fmtN, k: O.fmtK, pct: (x) => O.fmtPct(x, 1), ms: fmtMs, s: O.fmtDur };

/* ---- drawer -------------------------------------------------------------------------------- */
const $ = (id) => document.getElementById(id);
let onDrawerClose = null;
const dr = O.drawer({ element: $("drawer"), scrim: $("scrim"), initialFocus: () => $("drMain"),
  onClose: () => { if (onDrawerClose) { const f = onDrawerClose; onDrawerClose = null; f(); } } });

/**
 * Open the shared drawer.
 * @param {{ title: string, lead?: Node, tag?: Node, strip?: Array, stats?: Array<[string,string]>,
 *   main?: Node[], detail?: Node[], opener?: Element, onClose?: Function }} o
 */
function openDrawer(o) {
  dr.lead.replaceChildren(...(o.lead ? [o.lead] : []));
  dr.setTitle(o.title || "");
  dr.tag.replaceChildren(...(o.tag ? [o.tag] : []));
  // statStrip rolls each character; a plain space collapses ("notlogged"), so keep words together with U+00A0
  const strip = (o.strip || []).map((x) => ({ ...x, value: String(x.value).replace(/ /g, "\u00a0") }));
  $("drStrip").replaceChildren(...(strip.length ? [O.statStrip(strip)] : []));
  $("drStats").replaceChildren(...(o.stats ? [O.statLine(o.stats)] : []));
  $("drMain").replaceChildren(...(o.main || []));
  $("drDetail").replaceChildren(...(o.detail || []));
  $("drMain").scrollTop = 0;
  onDrawerClose = o.onClose || null;
  dr.open(o.opener);
}

/* ---- evidence ------------------------------------------------------------------------------ */
function eventsText(events, truncated) {
  const lines = events.slice(0, 400).map((e) => (e.event ? `event: ${e.event}\n` : "") + "data: " + (typeof e.data === "string" ? e.data : JSON.stringify(e.data)) + (e.t_ms != null ? `   (+${Math.round(e.t_ms)}ms)` : ""));
  if (events.length > 400 || truncated) lines.push(`… ${events.length} events recorded${truncated ? " (capped)" : ""}`);
  return lines.join("\n");
}

/** Evidence blocks for one exchange (request, response, normalized view). */
function exchangeView(ex, i) {
  const req = ex.request || {}, res = ex.response, n = ex.normalized || {}, t = ex.timing || {};
  const headers = Object.entries(req.headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n");
  const out = [];
  out.push(el("section", { class: "ev", "aria-label": `Request ${i + 1}` },
    el("h4", {}, `Request ${i + 1}`, O.pill(ex.label || "main"), O.pill(ex.mode), O.pill(ex.protocol)),
    el("div", { class: "url" }, `${req.method || "POST"} ${req.url || ""}`),
    pre((headers ? headers + "\n\n" : "") + pretty(req.body))));
  const status = res ? res.status : null;
  const tone = status == null ? "err" : status >= 500 ? "err" : status >= 400 ? "warn" : "ok";
  const body = !res ? (ex.transport_error || "no response") : res.events ? eventsText(res.events, res.events_truncated) : (res.body_json != null ? pretty(res.body_json) : (res.body_text || ""));
  out.push(el("section", { class: "ev", "aria-label": `Response ${i + 1}` },
    el("h4", {}, "Response", O.pill(status == null ? "no response" : `HTTP ${status}`, tone),
      res && res.content_type ? el("span", { class: "url" }, res.content_type) : null,
      el("span", { class: "url" }, `ttft ${fmtMs(t.ttft_ms)} · total ${fmtMs(t.total_ms)}`)),
    ex.transport_error ? O.pill(ex.transport_error, "err") : null,
    pre(body + (res && res.truncated ? "\n… (body truncated)" : ""))));
  const u = n.usage || {};
  out.push(el("section", { class: "ev", "aria-label": "Normalized response" },
    el("h4", {}, "Normalized"),
    O.kvList([
      ["finish", n.finish_reason_norm || nl()],
      ["error code", n.error_code ? `${n.error_code} (${n.error_code_type || "?"})` : nl("none")],
      ["error", n.error_message ? String(n.error_message).slice(0, 400) : nl("none")],
      ["model", n.model || nl()],
      ["content", n.content != null ? String(n.content).slice(0, 600) : nl("none")],
      ["reasoning", n.reasoning != null ? String(n.reasoning).slice(0, 600) : nl("none")],
      ["tool calls", (n.tool_calls || []).length ? n.tool_calls.map((c) => `${c.name}(${c.arguments})${c.parse_error ? " ✕ " + c.parse_error : ""}`).join("\n") : nl("none")],
      ["usage", u.prompt_tokens == null ? nl() : `prompt ${u.prompt_tokens} · completion ${u.completion_tokens} · cached ${u.cached_tokens == null ? "not logged" : u.cached_tokens}`],
    ])));
  return out;
}

/** Check list for the detail column. */
function checksView(checks) {
  if (!checks || !checks.length) return [O.emptyState("No checks recorded.")];
  const rows = [];
  for (const c of checks) {
    const tip = [c.expected != null ? "expected " + JSON.stringify(c.expected) : null, c.observed != null ? "observed " + JSON.stringify(c.observed).slice(0, 300) : null].filter(Boolean).join("\n");
    rows.push(statusPill(c.status), el("span", { class: "name", "data-tip": tip || null }, c.name + (c.attempt != null && c.attempt > 0 ? ` #${c.attempt}` : "") + (c.soft ? " (soft)" : "")));
    rows.push(el("span", { class: "msg" }, c.message || ""));
  }
  return [el("h4", {}, "Checks"), el("div", { class: "chk" }, rows)];
}

function sourceView(source) {
  if (!source || !Object.keys(source).length) return [];
  const links = [...(source.urls || []), ...(source.spec || [])];
  const items = [];
  for (const i of source.issues || []) items.push(el("li", {}, O.pill("issue"), el("span", { class: "mono" }, i)));
  for (const u of links) items.push(el("li", {}, O.pill("link"), /^https?:/.test(u) ? el("a", { href: u, target: "_blank", rel: "noopener" }, u) : el("span", {}, u)));
  if (source.case_file) items.push(el("li", {}, O.pill("case"), el("span", { class: "mono" }, source.case_file)));
  return [el("h4", {}, "Source"), el("ul", { class: "list-plain" }, items)];
}

/**
 * Open the drawer for one ItemResult (any component). `run` is { run_id, started_at, target, defects }.
 */
function openItem(item, run, opener, extra) {
  const m = item.metrics || {};
  const ev = item.evidence || [];
  openDrawer({
    opener,
    lead: statusPill(item.status),
    title: `${item.group || item.id} ${item.title ? "· " + item.title : ""}`,
    tag: O.pill(item.id, null, { class: "mono", "data-tip": item.id }),
    strip: [
      { label: "Status", value: LABEL[item.status] || item.status, tone: TONE[item.status] || null },
      { label: "HTTP", value: m.http_status == null ? "not logged" : String(m.http_status), tone: m.http_status >= 500 ? "err" : null },
      { label: "Error code", value: m.error_code == null ? "none" : String(m.error_code) },
      { label: "TTFT", value: fmtMs(m.ttft_ms) },
      { label: "Total", value: fmtMs(m.total_ms) },
      { label: "Attempts", value: String(m.attempts == null ? "not logged" : m.attempts) },
    ],
    stats: [
      ["target", item.target || "not logged"], ["severity", item.severity || "not logged"], ["category", item.category || "not logged"],
      ...Object.entries(item.labels || {}).map(([k, v]) => [k, String(v)]),
      ...(run ? [["run", run.run_id], ["started", fmtTime(run.started_at)]] : []),
      ...(run && run.defects && run.defects.length ? [["mock defects", run.defects.join(", ")]] : []),
    ],
    main: ev.length ? ev.flatMap(exchangeView) : [O.emptyState(item.reason || "No evidence recorded for this item.")],
    detail: [
      ...(item.reason ? [el("h4", {}, "Summary"), el("div", { class: "well" }, item.reason)] : []),
      ...checksView(item.checks),
      ...sourceView(item.source),
      ...(extra || []),
    ],
  });
}

/* ---- pages, tabs, KPIs ---------------------------------------------------------------------- */
function kpis(tiles) {
  const host = $("kpis");
  if (!tiles || !tiles.length) { host.hidden = true; return; }
  host.hidden = false;
  O.kpiStrip(host, tiles.map((t) => ({ ...t, format: typeof t.format === "string" ? FORMATS[t.format] : t.format })));
}

const kit = {
  O, el, data: DATA, site: SITE,
  registerPage(id, impl) { IMPLS[id] = impl; },
  openDrawer, openItem, exchangeView, checksView, sourceView, statusPill, nl, fmtMs, fmtTime, pretty, pre,
  tone: (s) => TONE[s] || null, label: (s) => LABEL[s] || s, rank: (s) => (s in RANK ? RANK[s] : 9),
  setKpis: kpis,
};
window.GLMKit = kit;

function boot() {
  O.tooltip();
  $("window").textContent = SITE.window || "";
  $("foot").textContent = `glmkit ${SITE.kit_version || ""} · built ${fmtTime(SITE.generated_at)} · ${SITE.run_count || 0} run(s) from ${SITE.results || "results"} · values a run did not record show as "not logged".`;
  const pages = (SITE.pages || []).filter((p) => IMPLS[p.id]);
  const rendered = {};
  const show = (id) => {
    const p = pages.find((x) => x.id === id);
    if (!p) return;
    const data = DATA[id] || {};
    if (!rendered[id]) {
      rendered[id] = true;
      try { IMPLS[id].render($("view-" + id), data, kit); }
      catch (e) { $("view-" + id).replaceChildren(O.emptyState("This tab failed to render: " + e.message)); throw e; }
    }
    kpis(data.kpis);
    if (IMPLS[id].onShow) IMPLS[id].onShow(data, kit);
  };
  if (!pages.length) {
    $("kpis").hidden = true;
    return;
  }
  const bar = O.tabs($("tabs"), pages.map((p) => ({ id: p.id, label: p.title, count: p.count, panel: "view-" + p.id })), {
    label: "Views",
    onChange: (id) => { show(id); try { history.replaceState(null, "", "#" + id); } catch (e) { /* file:// may refuse */ } window.scrollTo({ top: 0, behavior: O.reduced() ? "auto" : "smooth" }); },
  });
  const want = (location.hash || "").slice(1);
  const first = pages.some((p) => p.id === want) ? want : pages[0].id;
  bar.select(first);
  show(first);
}
kit.boot = boot;
})();
