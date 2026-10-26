/* Observatory components: framework-free, data-agnostic ES module.
 * Requires tokens.css and components.css on the page. Colours are passed as token names
 * ("--cat-1", "--kind-shell", "--ok") or any CSS colour value; token names become var(--x).
 * Every animation checks reduced() (prefers-reduced-motion) and renders the final state instead.
 * components.iife.js is generated from this file by build.py (window.Observatory, for file://).
 */

const SVG_NS = "http://www.w3.org/2000/svg";
const MQ = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;

/** True when the viewer asks for reduced motion. Read live, so a settings change applies on the next render. */
export const reduced = () => !!(MQ && MQ.matches);

/* ---------------------------------------------------------------- DOM helpers */

/**
 * Create an HTML element.
 * @param {string} tag
 * @param {Object} [attrs] `class`, `style` (string), `html` (innerHTML), `on<event>` handlers, other attributes.
 *   null/false values are skipped; `true` sets an empty attribute.
 * @param {...(Node|string|Array)} kids children; arrays are flattened, null/false skipped.
 * @returns {HTMLElement}
 */
export function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "style") n.style.cssText = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) if (c != null && c !== false) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}

/**
 * Create an SVG element. Attributes with null/undefined values are skipped.
 * @param {string} tag
 * @param {Object} [attrs]
 * @returns {SVGElement}
 */
export function sv(tag, attrs = {}) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  return n;
}

/**
 * Resolve a colour argument: "--token" becomes "var(--token)", anything else passes through.
 * @param {string} c
 * @returns {string}
 */
export const cv = (c) => (typeof c === "string" && c.startsWith("--") ? `var(${c})` : c);

/** Inline animation-delay for item i of a staggered entry; empty under reduced motion. */
export const stagger = (i, step = 30, cap = 400) => (reduced() ? "" : `animation-delay:${Math.min(i * step, cap)}ms;`);

/** Call fn when node's width changes (rAF-batched). Returns a disconnect function. */
function onWidth(node, fn) {
  if (typeof ResizeObserver !== "function") return () => {};
  let w = node.clientWidth, raf = 0;
  const ro = new ResizeObserver(() => {
    const nw = node.clientWidth;
    if (nw === w) return;
    w = nw; cancelAnimationFrame(raf); raf = requestAnimationFrame(fn);
  });
  ro.observe(node);
  return () => ro.disconnect();
}

/* ---------------------------------------------------------------- formatting */

/** Integer with thousands separators; null shows "not logged". @param {?number} n */
export const fmtN = (n) => (n == null ? "not logged" : Math.round(n).toLocaleString("en-US"));
/** Compact number: 1.2k, 34M, 1.05B. @param {?number} n */
export const fmtK = (n) => {
  if (n == null) return "not logged";
  const a = Math.abs(n);
  return a >= 1e9 ? (n / 1e9).toFixed(a >= 1e10 ? 1 : 2) + "B" : a >= 1e6 ? (n / 1e6).toFixed(a >= 1e8 ? 0 : 1) + "M" : a >= 1e3 ? (n / 1e3).toFixed(a >= 1e5 ? 0 : 1) + "k" : String(Math.round(n));
};
/** Duration from seconds: 420ms, 3.21s, 4m 05s, 2h 10m, 1d 3h. @param {?number} s */
export const fmtDur = (s) => {
  if (s == null) return "not logged";
  if (s < 1) return Math.round(s * 1000) + "ms";
  if (s < 60) return s.toFixed(s < 10 ? 2 : 1) + "s";
  if (s < 3600) return Math.floor(s / 60) + "m " + String(Math.round(s % 60)).padStart(2, "0") + "s";
  if (s < 86400) return Math.floor(s / 3600) + "h " + String(Math.round((s % 3600) / 60)).padStart(2, "0") + "m";
  return Math.floor(s / 86400) + "d " + Math.round((s % 86400) / 3600) + "h";
};
/** Ratio to percent string. @param {?number} r 0..1 @param {number} [d] decimals */
export const fmtPct = (r, d = 1) => (r == null ? "not logged" : (r * 100).toFixed(d) + "%");

/** Round a maximum up to a readable axis ceiling (1, 1.2, 1.5, 2, 2.5 ... x 10^n). */
export function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

/* ---------------------------------------------------------------- tone */

/**
 * Map a score to a tone. Higher is better.
 * @param {number} v
 * @param {[number, number]} [th] [ok threshold, warn threshold]; default [85, 70]
 * @returns {"ok"|"warn"|"err"}
 */
export const toneOf = (v, th = [85, 70]) => (v >= th[0] ? "ok" : v >= th[1] ? "warn" : "err");
/** "ok" -> "var(--ok)". */
export const toneVar = (t) => `var(--${t})`;

/**
 * Status pill.
 * @param {string} text
 * @param {"ok"|"warn"|"err"|"heur"} [tone] heur renders dashed (inferred / heuristic values)
 * @param {Object} [attrs] extra attributes, e.g. { "data-tip": "..." }
 */
export const pill = (text, tone, attrs = {}) => el("span", { ...attrs, class: "pill" + (tone ? " " + tone : "") + (attrs.class ? " " + attrs.class : "") }, text);

/** Coloured dot. @param {string} color token name or CSS colour */
export const dot = (color) => el("span", { class: "dot", style: `background:${cv(color)}`, "aria-hidden": "true" });

/** Empty state line. @param {string} text */
export const emptyState = (text) => el("div", { class: "empty" }, text);

/**
 * Health dot + score. Tone parity: ok 85+, warn 70-84, err under 70, grey when score is null.
 * @param {?number} score
 * @param {{ thresholds?: [number, number], tip?: string }} [opts]
 */
export function healthDot(score, opts = {}) {
  const c = score == null ? "var(--kind-other)" : toneVar(toneOf(score, opts.thresholds));
  return el("span", { class: "health num", "data-tip": opts.tip || null }, el("span", { class: "dot", style: `background:${c}` }), score == null ? "–" : String(score));
}

/* ---------------------------------------------------------------- counters */

/**
 * Digit-roll number ticker. Digits roll into place (900ms, 45ms stagger); under reduced motion the final text is set.
 * @param {HTMLElement} node target; its contents are replaced
 * @param {?number} value
 * @param {(v:?number)=>string} [format]
 */
export function ticker(node, value, format = fmtN) {
  const final = format(value);
  if (reduced() || value == null || !isFinite(value)) { node.textContent = final; return; }
  node.textContent = "";
  const wrap = el("span", { class: "ticker", role: "img", "aria-label": final }), cols = [];
  for (const ch of final) {
    if (/\d/.test(ch)) {
      const col = el("span", { class: "col", "aria-hidden": "true" }, Array.from({ length: 10 }, (_, i) => el("span", {}, String(i))));
      wrap.append(col); cols.push([col, +ch]);
    } else wrap.append(el("span", { "aria-hidden": "true" }, ch));
  }
  node.append(wrap);
  requestAnimationFrame(() => requestAnimationFrame(() => cols.forEach(([c, d], i) => {
    c.style.transitionDelay = i * 45 + "ms"; c.style.transform = `translateY(${-d * 1.15}em)`;
  })));
}

/**
 * Count up from 0 with an exponential ease (800ms). Reduced motion sets the final text.
 * @param {HTMLElement} node
 * @param {?number} value
 * @param {(v:?number)=>string} [format]
 */
export function countUp(node, value, format = fmtN) {
  if (reduced() || value == null || !isFinite(value)) { node.textContent = format(value); return; }
  const t0 = performance.now(), dur = 800, ease = (t) => 1 - Math.pow(2, -10 * t);
  const step = (now) => {
    const p = Math.min(1, (now - t0) / dur);
    node.textContent = format(p < 1 ? value * ease(p) : value);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ---------------------------------------------------------------- KPI strip */

/**
 * Sparkline: area + line + end dot. The line draws in (700ms) unless reduced motion.
 * @param {number[]} values at least 2
 * @param {{ width?: number, height?: number, color?: string, label?: string }} [opts] color defaults to "--accent"
 * @returns {SVGSVGElement}
 */
export function sparkline(values, opts = {}) {
  const w = opts.width || 72, h = opts.height || 24, color = cv(opts.color || "--accent");
  const max = Math.max(...values, 1), min = Math.min(0, ...values), span = max - min || 1, n = Math.max(values.length - 1, 1);
  const pts = values.map((v, i) => [i * (w / n), h - ((v - min) / span) * (h - 2) - 1]);
  const s = sv("svg", { class: "spark", viewBox: `0 0 ${w} ${h}`, width: w, height: h, role: opts.label ? "img" : null, "aria-label": opts.label || null, "aria-hidden": opts.label ? null : "true" });
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  s.append(sv("path", { d: d + ` L${w} ${h} L0 ${h} Z`, fill: `color-mix(in oklab, ${color} 14%, transparent)` }));
  const line = sv("path", { d, fill: "none", stroke: color, "stroke-width": 1.25, "stroke-linejoin": "round" });
  s.append(line);
  const last = pts[pts.length - 1];
  s.append(sv("circle", { cx: last[0], cy: last[1], r: 2, fill: color }));
  if (!reduced() && line.animate) {
    const L = 200;
    line.style.strokeDasharray = L;
    line.animate([{ strokeDashoffset: L }, { strokeDashoffset: 0 }], { duration: 700, easing: "cubic-bezier(.22,1,.36,1)", fill: "forwards" });
  }
  return s;
}

/**
 * One stat tile.
 * @param {{ label: string, value: ?number, format?: Function, unit?: string, sub?: string,
 *   spark?: number[], sparkColor?: string, tone?: "warn"|"err" }} t
 * @returns {HTMLElement} .kpi
 */
export function kpiTile(t) {
  const val = el("span");
  const node = el("div", { class: "kpi" + (t.tone ? " is-" + t.tone : "") },
    el("div", { class: "kpi-label" }, t.label),
    el("div", { class: "kpi-value" }, val, t.unit ? el("span", { class: "unit" }, t.unit) : null),
    t.sub ? el("div", { class: "kpi-sub" }, t.sub) : null);
  if (t.spark && t.spark.length > 1) node.append(sparkline(t.spark, { color: t.sparkColor }));
  ticker(val, t.value, t.format || fmtN);
  return node;
}

/**
 * KPI strip: fills container with tiles (5 columns, 2 under 900px).
 * @param {HTMLElement} container
 * @param {Array} tiles see kpiTile
 */
export function kpiStrip(container, tiles) {
  container.classList.add("kpis");
  container.replaceChildren(...tiles.map(kpiTile));
  return container;
}

/* ---------------------------------------------------------------- controls */

/**
 * Segmented control (single choice, aria-pressed).
 * @param {HTMLElement} container becomes .seg[role=group]
 * @param {Array<{ value: string, label: string, color?: string }>} options color adds a dot
 * @param {{ value?: string, label?: string, onChange?: (value:string)=>void }} [opts]
 * @returns {{ value: string, set(value:string): void }}
 */
export function segmented(container, options, opts = {}) {
  container.classList.add("seg");
  container.setAttribute("role", "group");
  if (opts.label) container.setAttribute("aria-label", opts.label);
  let cur = opts.value ?? options[0]?.value;
  const btns = options.map((o) => el("button", { type: "button", "aria-pressed": "false", onclick: () => api.set(o.value, true) }, o.color ? dot(o.color) : null, o.label));
  container.replaceChildren(...btns);
  const paint = () => options.forEach((o, i) => btns[i].setAttribute("aria-pressed", String(o.value === cur)));
  const api = {
    get value() { return cur; },
    set(v, user) { cur = v; paint(); if (user && opts.onChange) opts.onChange(v); },
  };
  paint();
  return api;
}

/**
 * Toggle chip (aria-pressed).
 * @param {string|Node} label
 * @param {{ pressed?: boolean, onChange?: (pressed:boolean)=>void }} [opts]
 * @returns {HTMLButtonElement}
 */
export function toggleChip(label, opts = {}) {
  const b = el("button", { type: "button", class: "chip", "aria-pressed": String(!!opts.pressed) }, label);
  b.addEventListener("click", () => { const on = b.getAttribute("aria-pressed") !== "true"; b.setAttribute("aria-pressed", String(on)); if (opts.onChange) opts.onChange(on); });
  return b;
}

/**
 * Legend of toggle buttons (series on/off).
 * @param {HTMLElement} container becomes .legend
 * @param {Array<{ id?: string, label: string, color: string, pressed?: boolean }>} items
 * @param {{ onToggle?: (id:string, pressed:boolean)=>void }} [opts] without onToggle the legend is static
 */
export function legend(container, items, opts = {}) {
  container.classList.add("legend");
  container.replaceChildren(...items.map((it) => {
    const b = el("button", { type: "button", "aria-pressed": String(it.pressed !== false), tabindex: opts.onToggle ? null : "-1" }, dot(it.color), it.label);
    if (opts.onToggle) b.addEventListener("click", () => { const on = b.getAttribute("aria-pressed") !== "true"; b.setAttribute("aria-pressed", String(on)); opts.onToggle(it.id ?? it.label, on); });
    return b;
  }));
  return container;
}

/**
 * Search field with icon and optional key hint.
 * @param {{ placeholder?: string, label?: string, kbd?: string, id?: string, className?: string, onInput?: (q:string)=>void }} [opts]
 * @returns {{ label: HTMLLabelElement, input: HTMLInputElement, kbd: ?HTMLElement }}
 */
export function searchBox(opts = {}) {
  const input = el("input", { type: "search", placeholder: opts.placeholder || "Search", autocomplete: "off", id: opts.id || null });
  const icon = sv("svg", { viewBox: "0 0 16 16", fill: "none", "stroke-width": 1.6, "aria-hidden": "true" });
  icon.append(sv("circle", { cx: 7, cy: 7, r: 5 }), sv("path", { d: "m11 11 3.5 3.5" }));
  const kbd = opts.kbd ? el("kbd", { "aria-hidden": "true" }, opts.kbd) : null;
  const label = el("label", { class: "search" + (opts.className ? " " + opts.className : "") }, el("span", { class: "sr" }, opts.label || opts.placeholder || "Search"), icon, input, kbd);
  if (opts.onInput) input.addEventListener("input", () => opts.onInput(input.value));
  return { label, input, kbd };
}

/**
 * Tab bar with counts. Arrow keys, Home and End move between tabs; panels get hidden.
 * @param {HTMLElement} nav becomes .tabs[role=tablist]
 * @param {Array<{ id: string, label: string, count?: number, panel?: HTMLElement|string }>} items panel: element or element id
 * @param {{ value?: string, label?: string, onChange?: (id:string)=>void }} [opts]
 * @returns {{ value: string, select(id:string): void, setCount(id:string, n:?number): void }}
 */
export function tabs(nav, items, opts = {}) {
  nav.classList.add("tabs");
  nav.setAttribute("role", "tablist");
  if (opts.label) nav.setAttribute("aria-label", opts.label);
  let cur = opts.value ?? items[0]?.id;
  const panelOf = (it) => (typeof it.panel === "string" ? document.getElementById(it.panel) : it.panel) || null;
  const btns = new Map(), counts = new Map();
  for (const it of items) {
    const p = panelOf(it), id = "tab-" + it.id;
    const count = el("span", { class: "count" }, it.count != null ? fmtN(it.count) : null);
    const b = el("button", { type: "button", role: "tab", id, "aria-controls": p && p.id ? p.id : null, onclick: () => api.select(it.id, true) }, it.label, count);
    if (p) { p.setAttribute("role", "tabpanel"); p.setAttribute("aria-labelledby", id); }
    btns.set(it.id, b); counts.set(it.id, count);
  }
  nav.replaceChildren(...btns.values());
  nav.addEventListener("keydown", (e) => {
    const ids = items.map((i) => i.id), at = ids.indexOf(cur);
    const to = e.key === "ArrowRight" ? ids[(at + 1) % ids.length] : e.key === "ArrowLeft" ? ids[(at - 1 + ids.length) % ids.length] : e.key === "Home" ? ids[0] : e.key === "End" ? ids[ids.length - 1] : null;
    if (to == null) return;
    e.preventDefault(); api.select(to, true); btns.get(to).focus();
  });
  const paint = () => {
    for (const it of items) {
      const on = it.id === cur, b = btns.get(it.id), p = panelOf(it);
      b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1;
      if (p) p.hidden = !on;
    }
  };
  const api = {
    get value() { return cur; },
    select(id, user) { if (!btns.has(id)) return; cur = id; paint(); if (user && opts.onChange) opts.onChange(id); },
    setCount(id, n) { const c = counts.get(id); if (c) c.textContent = n == null ? "" : fmtN(n); },
  };
  paint();
  return api;
}

/* ---------------------------------------------------------------- bar list */

/**
 * Ranked bar list: label left, value right, a tinted fill behind sized to value / max.
 * @param {Array<{ label: string|Node, value: number, meta?: string, pill?: string|Node, tip?: string,
 *   tone?: "ok"|"warn"|"err", color?: string, dotColor?: string, onClick?: Function }>} rows
 *   tone tints the fill; color (token or CSS) tints it with any colour at 20%; dotColor adds a leading dot
 * @param {{ format?: (v:number)=>string, max?: number, mono?: boolean, empty?: string }} [opts]
 * @returns {HTMLElement} .barlist
 */
export function barList(rows, opts = {}) {
  const f = opts.format || fmtN;
  const max = opts.max || Math.max(...rows.map((r) => r.value), 1);
  const box = el("div", { class: "barlist" });
  rows.forEach((r, i) => {
    const fillStyle = `width:${Math.max(0, Math.min(1, r.value / max)) * 100}%;` + (r.color ? `background:color-mix(in oklab, ${cv(r.color)} 20%, transparent);` : "");
    box.append(el("div", { class: "bl-row fade-in" + (r.tone ? " " + r.tone : ""), style: stagger(i, 30), "data-tip": r.tip || null,
      role: r.onClick ? "button" : null, tabindex: r.onClick ? "0" : null, onclick: r.onClick || null,
      onkeydown: r.onClick ? (e) => { if (e.key === "Enter") r.onClick(e); } : null },
    el("span", { class: "bl-fill grow-x", style: fillStyle }),
    el("span", { class: "bl-name" + (r.mono || opts.mono ? " mono" : "") }, r.dotColor ? dot(r.dotColor) : null, el("span", { class: "bl-text" }, r.label),
      r.meta ? el("span", { class: "bl-meta" }, r.meta) : null, typeof r.pill === "string" ? pill(r.pill) : r.pill || null),
    el("span", { class: "bl-val num" }, f(r.value))));
  });
  if (!rows.length) box.append(emptyState(opts.empty || "Nothing logged."));
  return box;
}

/* ---------------------------------------------------------------- stacked bars */

/**
 * Stacked bar chart (SVG) with dashed gridlines, hover tooltips per column and an optional toggle legend.
 * Re-renders on width change.
 * @param {SVGSVGElement} svg
 * @param {Array<{ name: string, color: string, values: number[] }>} series stacked bottom-up in array order
 * @param {{ labels: string[], height?: number, format?: (v:number)=>string, legend?: HTMLElement,
 *   label?: string, hidden?: string[] }} opts labels: one per column
 * @returns {{ update(series?: Array, labels?: string[]): void, destroy(): void }}
 */
export function stackedBars(svg, series, opts = {}) {
  const o = { height: 240, format: fmtK, padL: 48, padB: 26, padT: 8, barMax: 56, ...opts };
  const hidden = new Set(o.hidden || []);
  let S = series, labels = o.labels || [];
  svg.classList.add("chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", o.label || "Stacked bar chart");
  function draw(animate = true) {
    svg.replaceChildren();
    const W = svg.clientWidth || 800, H = o.height, plotH = H - o.padT - o.padB;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("height", H);
    const vis = S.filter((s) => !hidden.has(s.name));
    const n = labels.length || Math.max(0, ...S.map((s) => s.values.length));
    const totals = Array.from({ length: n }, (_, i) => vis.reduce((a, s) => a + (s.values[i] || 0), 0));
    const nice = niceMax(Math.max(...totals, 1));
    for (let i = 0; i <= 4; i++) {
      const y = o.padT + plotH * (1 - i / 4);
      svg.append(sv("line", { x1: o.padL, x2: W, y1: y, y2: y, stroke: "var(--edge)", "stroke-dasharray": i ? "2 3" : null }));
      const t = sv("text", { x: o.padL - 8, y: y + 3, "text-anchor": "end" }); t.textContent = o.format(nice * i / 4); svg.append(t);
    }
    const bw = (W - o.padL) / Math.max(n, 1), barW = Math.min(o.barMax, bw * 0.62), every = Math.max(1, Math.ceil(44 / bw));
    for (let i = 0; i < n; i++) {
      let y = H - o.padB; const x = o.padL + i * bw + (bw - barW) / 2;
      const g = sv("g", { class: animate && !reduced() ? "grow-y" : null, style: animate ? stagger(i, 40) : null });
      const lines = [(labels[i] ?? i) + "  " + o.format(totals[i])];
      for (const s of vis) {
        const v = s.values[i] || 0; if (!v) continue;
        const h = (v / nice) * plotH; y -= h;
        g.append(sv("rect", { x, y, width: barW, height: Math.max(h - 1, 0.5), fill: cv(s.color), rx: 2 }));
        lines.push(`${s.name}  ${o.format(v)}`);
      }
      svg.append(g, sv("rect", { x: o.padL + i * bw, y: o.padT, width: bw, height: plotH, fill: "transparent", "data-tip": lines.join("\n") }));
      if (i % every === 0) { const t = sv("text", { x: x + barW / 2, y: H - 8, "text-anchor": "middle" }); t.textContent = labels[i] ?? ""; svg.append(t); }
    }
  }
  function paintLegend() {
    if (!o.legend) return;
    legend(o.legend, S.map((s) => ({ id: s.name, label: s.name, color: s.color, pressed: !hidden.has(s.name) })),
      { onToggle: (id, on) => { on ? hidden.delete(id) : hidden.add(id); draw(); } });
  }
  draw(); paintLegend();
  const off = onWidth(svg, () => draw(false));
  return {
    update(nextSeries, nextLabels) { if (nextSeries) S = nextSeries; if (nextLabels) labels = nextLabels; draw(); paintLegend(); },
    destroy: off,
  };
}

/* ---------------------------------------------------------------- composition bar */

/**
 * Context composition bar: one horizontal bar split into parts, with a legend (value and share) below.
 * @param {Array<{ label: string, value: number, color: string }>} parts
 * @param {{ format?: (v:number)=>string, label?: string, kv?: Array<[string, string|Node]> }} [opts]
 *   kv: optional key/value rows rendered under the legend
 * @returns {HTMLElement} .ctxmap
 */
export function compositionBar(parts, opts = {}) {
  const f = opts.format || fmtK;
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  const bar = el("div", { class: "ctx-bar", role: "img", "aria-label": (opts.label || "Composition") + ": " + parts.map((p) => `${p.label} ${((p.value / total) * 100).toFixed(1)}%`).join(", ") });
  parts.forEach((p, i) => {
    const w = (p.value / total) * 100;
    bar.append(el("div", { class: "ctx-seg grow-x", style: `width:${w}%;background:${cv(p.color)};` + stagger(i, 60), "data-tip": `${p.label}\n${fmtN(p.value)} · ${w.toFixed(2)}%` }));
  });
  const leg = el("div", { class: "ctx-legend" }, parts.map((p) => el("div", {},
    el("span", { class: "sw", style: `background:${cv(p.color)}` }), el("span", {}, p.label),
    el("span", { class: "num cv" }, f(p.value)), el("span", { class: "num cp" }, ((p.value / total) * 100).toFixed(1) + "%"))));
  return el("div", { class: "ctxmap" }, bar, leg, opts.kv ? kvList(opts.kv) : null);
}

/* ---------------------------------------------------------------- treemap */

/**
 * Squarified treemap layout (Bruls et al.). Order-preserving for items sorted descending.
 * @param {Array<{ value: number }>} items
 * @param {number} x @param {number} y @param {number} w @param {number} h
 * @returns {Array} the items, each copied with x, y, w, h added
 */
export function squarify(items, x, y, w, h) {
  const out = [], total = items.reduce((a, it) => a + it.value, 0);
  if (!total || w <= 0 || h <= 0) return out;
  const scale = (w * h) / total;
  let rest = items.filter((it) => it.value > 0).map((it) => ({ ...it, a: it.value * scale }));
  const worst = (row, side) => {
    const s = row.reduce((a, r) => a + r.a, 0), mx = Math.max(...row.map((r) => r.a)), mn = Math.min(...row.map((r) => r.a));
    return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
  };
  while (rest.length) {
    const side = Math.min(w, h); let row = [rest[0]], i = 1;
    while (i < rest.length && worst(row.concat(rest[i]), side) <= worst(row, side)) { row.push(rest[i]); i++; }
    rest = rest.slice(i);
    const s = row.reduce((a, r) => a + r.a, 0);
    if (w >= h) { const cw = s / h; let cy = y; row.forEach((r) => { const rh = r.a / cw; out.push({ ...r, x, y: cy, w: cw, h: rh }); cy += rh; }); x += cw; w -= cw; }
    else { const ch = s / w; let cx = x; row.forEach((r) => { const rw = r.a / ch; out.push({ ...r, x: cx, y, w: rw, h: ch }); cx += rw; }); y += ch; h -= ch; }
  }
  return out;
}

/**
 * Ordered treemap: the largest `max` items as tiles, the remainder folded into one hatched "rest" tile.
 * Tiles are focusable; Enter or click calls onSelect. Re-renders on width change.
 * @param {HTMLElement} container becomes .tmap (height from CSS, 300px default)
 * @param {Array<{ id?: string, label: string, value: number, color?: string, sub?: string, tip?: string }>} items
 * @param {{ max?: number, format?: (v:number)=>string, onSelect?: (item, tile:HTMLElement)=>void,
 *   restLabel?: (n:number)=>string, label?: string }} [opts]
 * @returns {{ update(items: Array): void, destroy(): void }}
 */
export function treemap(container, items, opts = {}) {
  const o = { max: 14, format: fmtK, restLabel: (n) => `${n} more`, ...opts };
  container.classList.add("tmap");
  container.setAttribute("role", "list");
  container.setAttribute("aria-label", o.label || "Treemap");
  let data = items;
  function draw(animate = true) {
    container.replaceChildren();
    const sorted = data.filter((d) => d.value > 0).slice().sort((a, b) => b.value - a.value);
    const total = sorted.reduce((a, d) => a + d.value, 0) || 1;
    const top = sorted.slice(0, o.max), rest = sorted.slice(o.max);
    const list = top.map((d) => ({ ...d, item: d }));
    if (rest.length) list.push({ rest: true, value: rest.reduce((a, d) => a + d.value, 0), n: rest.length });
    const W = container.clientWidth || 800, H = container.clientHeight || 300, G = 3;
    squarify(list, 0, 0, W, H).forEach((r, i) => {
      const pct = ((r.value / total) * 100).toFixed(1), small = r.w < 90 || r.h < 44;
      const style = `left:${r.x + G / 2}px;top:${r.y + G / 2}px;width:${Math.max(r.w - G, 1)}px;height:${Math.max(r.h - G, 1)}px;` +
        (animate ? stagger(i, 35) : "animation:none;") + (r.color ? `--tc:${cv(r.color)};` : "");
      if (r.rest) {
        const lab = o.restLabel(r.n);
        container.append(el("div", { class: "tile rest" + (small ? " small" : ""), role: "listitem", style, "data-tip": `${lab}\n${o.format(r.value)} · ${pct}%` },
          el("span", { class: "tn" }, lab), el("span", { class: "tv" }, `${o.format(r.value)} · ${pct}%`)));
        return;
      }
      const pick = o.onSelect ? (e) => o.onSelect(r.item, e.currentTarget) : null;
      container.append(el("div", { class: "tile" + (small ? " small" : ""), role: "listitem", tabindex: o.onSelect ? "0" : null, style,
        "aria-label": `${r.label}, ${o.format(r.value)}, ${pct}%`,
        "data-tip": r.tip || `${r.label}\n${o.format(r.value)} · ${pct}%`,
        onclick: pick, onkeydown: pick ? (e) => { if (e.key === "Enter") pick(e); } : null },
      el("span", { class: "tn" }, r.label), el("span", { class: "tv" }, `${o.format(r.value)} · ${pct}%` + (r.sub ? ` · ${r.sub}` : ""))));
    });
  }
  draw();
  const off = onWidth(container, () => draw(false));
  return { update(next) { data = next; draw(); }, destroy: off };
}

/* ---------------------------------------------------------------- data table */

/**
 * Data table with sortable headers (click, Enter or Space; descending first, toggles on repeat; aria-sort set).
 * @param {HTMLTableElement} table becomes .tbl; wrap it in .tbl-wrap for sticky headers and scroll
 * @param {Array<{ key: string, label: string, render?: (row)=>Node|string, value?: (row)=>any,
 *   align?: "r", sortable?: boolean }>} columns value() is the sort key (default row[key])
 * @param {Array<Object>} rows
 * @param {{ sort?: { key: string, dir: 1|-1 }, rowKey?: (row)=>string, onRowClick?: (row, tr)=>void,
 *   rowClass?: (row)=>string, rowTip?: (row)=>?string, empty?: string, onRender?: (rows:Array)=>void,
 *   onSort?: (sort)=>void, label?: string }} [opts]
 * @returns {{ rows: Array, sort: Object, setRows(rows:Array): void, sortBy(key:string): void, select(id:?string): void }}
 */
export function sortableTable(table, columns, rows, opts = {}) {
  table.classList.add("tbl");
  if (opts.label) table.setAttribute("aria-label", opts.label);
  let data = rows.slice(), sort = { key: null, dir: -1, ...(opts.sort || {}) }, selected = null, shown = data;
  const keyOf = opts.rowKey || ((r) => r.id);
  const valueOf = (c) => c.value || ((r) => r[c.key]);
  function render() {
    const col = columns.find((c) => c.key === sort.key);
    shown = data;
    if (col) {
      const get = valueOf(col);
      shown = data.slice().sort((a, b) => { const x = get(a), y = get(b); return (x == null) - (y == null) || (x < y ? -1 : x > y ? 1 : 0) * sort.dir; });
    }
    const head = el("thead", {}, el("tr", {}, columns.map((c) => {
      const can = c.sortable !== false, on = sort.key === c.key;
      return el("th", { class: (can ? "sortable " : "") + (c.align || ""), scope: "col", tabindex: can ? "0" : null,
        "aria-sort": on ? (sort.dir > 0 ? "ascending" : "descending") : null,
        onclick: can ? () => api.sortBy(c.key) : null,
        onkeydown: can ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); api.sortBy(c.key); } } : null },
      c.label, can ? el("span", { class: "caret", "aria-hidden": "true" }, on && sort.dir > 0 ? "▲" : "▼") : null);
    })));
    const body = el("tbody");
    for (const r of shown) {
      const id = keyOf(r), click = opts.onRowClick;
      const tr = el("tr", { class: ((opts.rowClass && opts.rowClass(r)) || "") + (click ? " clickable" : ""), tabindex: click ? "0" : null,
        "data-key": id, "aria-selected": click ? String(selected === id) : null, "data-tip": opts.rowTip ? opts.rowTip(r) : null });
      if (click) {
        tr.addEventListener("click", () => click(r, tr));
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === tr) click(r, tr); });
      }
      for (const c of columns) { const v = c.render ? c.render(r) : r[c.key]; tr.append(el("td", { class: c.align || null }, v == null ? "" : v)); }
      body.append(tr);
    }
    if (!shown.length) body.append(el("tr", {}, el("td", { colspan: columns.length, class: "empty" }, opts.empty || "No rows match.")));
    table.replaceChildren(head, body);
    if (opts.onRender) opts.onRender(shown);
  }
  const api = {
    get rows() { return shown; },
    get sort() { return { ...sort }; },
    setRows(next) { data = next.slice(); render(); },
    sortBy(k) {
      sort = { key: k, dir: sort.key === k ? -sort.dir : -1 };
      render();
      const th = table.querySelector(`th[aria-sort]`); if (th) th.focus();
      if (opts.onSort) opts.onSort({ ...sort });
    },
    select(id) {
      selected = id;
      for (const tr of table.querySelectorAll("tbody tr[data-key]")) tr.setAttribute("aria-selected", String(tr.dataset.key === String(id)));
    },
  };
  render();
  return api;
}

/**
 * Split bar: ok vs failed share, 40px wide.
 * @param {number} ok @param {number} failed
 * @param {{ label?: boolean }} [opts] label: also show the total count before the bar
 * @returns {HTMLElement}
 */
export function splitBar(ok, failed, opts = {}) {
  const t = ok + failed;
  const bar = el("span", { class: "split", role: "img", "aria-label": `${fmtN(ok)} ok, ${fmtN(failed)} failed` },
    el("i", { style: `width:${t ? (ok / t) * 100 : 0}%` }), el("b", { style: `width:${t ? (failed / t) * 100 : 0}%` }));
  if (!opts.label) return bar;
  return el("span", { class: "split-cell", "data-tip": `${fmtN(ok)} ok · ${fmtN(failed)} failed` }, el("span", { class: "num" + (t ? "" : " zero") }, fmtN(t)), t ? bar : null);
}

/* ---------------------------------------------------------------- drawer */

/**
 * Right-side drawer with scrim. Closes on Escape (unless a child handled it), Close button or scrim click,
 * then returns focus to the element that opened it. Tab is kept inside while open.
 * Builds its own markup unless opts.element (an .drawer) and opts.scrim are given.
 * @param {{ onClose?: ()=>void, onOpen?: ()=>void, title?: string, element?: HTMLElement, scrim?: HTMLElement,
 *   mount?: HTMLElement, initialFocus?: ()=>?HTMLElement }} [opts]
 * @returns {{ el: HTMLElement, scrim: HTMLElement, header: HTMLElement, lead: HTMLElement, titleEl: HTMLElement,
 *   tag: HTMLElement, body: HTMLElement, isOpen: boolean, open(opener?: Element): void, close(): void, setTitle(t:string): void }}
 */
export function drawer(opts = {}) {
  let root = opts.element, scrim = opts.scrim;
  if (!root) {
    const id = "dr-" + Math.random().toString(36).slice(2, 8);
    scrim = el("div", { class: "scrim" });
    root = el("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-hidden": "true", "aria-labelledby": id },
      el("div", { class: "dr-h" }, el("div", { class: "dr-title" }, el("span", { class: "dr-lead" }), el("h3", { id }, opts.title || ""), el("span", { class: "dr-tag" }),
        el("button", { type: "button", class: "chip dr-close", "aria-label": "Close" }, "Close ", el("span", { class: "mono t-faint", "aria-hidden": "true" }, "Esc")))),
      el("div", { class: "dr-body" }));
    (opts.mount || document.body).append(scrim, root);
  }
  const q = (s) => root.querySelector(s);
  let isOpen = false, opener = null;
  const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';
  function onKey(e) {
    if (!isOpen) return;
    if (e.key === "Escape") { if (e.defaultPrevented) return; e.preventDefault(); api.close(); return; }
    if (e.key === "Tab") {
      const f = [...root.querySelectorAll(FOCUSABLE)].filter((n) => !n.hidden && n.offsetParent !== null);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (!root.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }
  document.addEventListener("keydown", onKey);
  const closeBtn = q(".dr-close");
  if (closeBtn) closeBtn.addEventListener("click", () => api.close());
  if (scrim) scrim.addEventListener("click", () => api.close());
  const api = {
    el: root, scrim, header: q(".dr-h"), lead: q(".dr-lead"), titleEl: q("h3"), tag: q(".dr-tag"), body: q(".dr-body"),
    get isOpen() { return isOpen; },
    setTitle(t) { if (api.titleEl) api.titleEl.textContent = t; },
    open(from) {
      if (!isOpen) opener = from || document.activeElement;
      isOpen = true;
      if (scrim) scrim.classList.add("open");
      root.classList.add("open"); root.setAttribute("aria-hidden", "false");
      if (opts.onOpen) opts.onOpen();
      setTimeout(() => { const t = (opts.initialFocus && opts.initialFocus()) || closeBtn; if (t && isOpen) t.focus(); }, 30);
    },
    close() {
      if (!isOpen) return;
      isOpen = false;
      if (scrim) scrim.classList.remove("open");
      root.classList.remove("open"); root.setAttribute("aria-hidden", "true");
      if (opts.onClose) opts.onClose();
      if (opener && document.contains(opener) && opener.focus) opener.focus();
      opener = null;
    },
  };
  return api;
}

/**
 * Stat strip (six cells; three per row under 700px). String values roll their digits like ticker().
 * @param {Array<{ label: string, value: string|number, tone?: "ok"|"warn"|"err" }>} items
 * @returns {HTMLElement} .statstrip
 */
export function statStrip(items) {
  return el("div", { class: "statstrip" }, items.map((it) => {
    const v = el("div", { class: "sv" + (it.tone ? " " + it.tone : "") });
    ticker(v, 1, () => String(it.value));
    return el("div", {}, el("div", { class: "sl" }, it.label), v);
  }));
}

/**
 * Inline label/value line (drawer meta row).
 * @param {Array<[string, string]>} pairs
 * @returns {HTMLElement} .dr-stats
 */
export const statLine = (pairs) => el("div", { class: "dr-stats" }, pairs.map(([k, v]) => el("span", {}, k + " ", el("b", {}, v))));

/**
 * Key/value list (span detail).
 * @param {Array<[string, string|number|Node]>} pairs
 * @returns {HTMLDListElement} .kv
 */
export const kvList = (pairs) => el("dl", { class: "kv" }, pairs.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v instanceof Node ? v : String(v))]));

/* ---------------------------------------------------------------- waterfall */

const KIND = { turn: "--kind-turn", llm: "--kind-llm", shell: "--kind-shell", read: "--kind-read", edit: "--kind-edit", agent: "--kind-agent", web: "--kind-web", mcp: "--kind-mcp" };
const KIND_LABEL = { llm: "LLM call", shell: "Shell", read: "Read", edit: "Edit", agent: "Subagent", web: "Web", mcp: "MCP" };
/** Span kind to colour token: turn, llm, shell, read, edit, agent, web, mcp; anything else is --kind-other. */
export const kindToken = (kind) => KIND[kind] || "--kind-other";

/**
 * Time scale with idle-gap compression: busy intervals share the width in proportion to their length,
 * and each idle gap longer than max(120s, 3% of total) collapses to a fixed-width hatched marker.
 * @param {Array<{ kind: string, start: number, end: number }>} spans seconds from trace start; turns count as instants
 * @param {number} total trace length in seconds
 * @param {{ gapMin?: number }} [opts]
 * @returns {{ X: (t:number)=>number, segs: Array<{ t0: number, t1: number, x0: number, x1: number, gap?: boolean }> }} X maps seconds to 0..100 %
 */
export function buildScale(spans, total, opts = {}) {
  const iv = spans.filter((x) => x.kind !== "turn").map((x) => [x.start, Math.max(x.end, x.start)])
    .concat(spans.filter((x) => x.kind === "turn").map((x) => [x.start, x.start]));
  iv.sort((a, b) => a[0] - b[0]);
  const gapMin = opts.gapMin ?? Math.max(120, total * 0.03);
  const merged = [];
  for (const [a, b] of iv) { const last = merged[merged.length - 1]; if (last && a - last[1] <= gapMin) last[1] = Math.max(last[1], b); else merged.push([a, b]); }
  if (!merged.length) merged.push([0, Math.max(total, 1)]);
  merged[0][0] = Math.min(merged[0][0], 0);
  merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], total);
  const nGaps = merged.length - 1, gapW = nGaps ? Math.min(2.4, 30 / nGaps) : 0;
  const activeTotal = merged.reduce((a, [x, y]) => a + Math.max(y - x, 0.001), 0);
  const avail = 100 - nGaps * gapW;
  const segs = []; let x = 0;
  merged.forEach(([a, b], i) => {
    const w = (Math.max(b - a, 0.001) / activeTotal) * avail;
    segs.push({ t0: a, t1: b, x0: x, x1: x + w }); x += w;
    if (i < merged.length - 1) { segs.push({ gap: true, t0: b, t1: merged[i + 1][0], x0: x, x1: x + gapW }); x += gapW; }
  });
  const X = (t) => { for (const s of segs) if (t <= s.t1) return s.x0 + ((t - s.t0) / (s.t1 - s.t0 || 1)) * (s.x1 - s.x0); return 100; };
  return { X, segs };
}

/**
 * Waterfall legend: turn, the span kinds, failed, retry, idle gap.
 * @param {string[]} [kinds] default llm, shell, read, edit, agent, web, mcp
 * @returns {HTMLElement} .wf-legend
 */
export function waterfallLegend(kinds = ["llm", "shell", "read", "edit", "agent", "web", "mcp"]) {
  return el("div", { class: "wf-legend", "aria-label": "Legend" },
    el("span", {}, el("i", { class: "lg-turn" }), "Turn, prompt to next prompt"),
    kinds.map((k) => el("span", {}, el("i", { style: `background:var(${kindToken(k)})` }), KIND_LABEL[k] || k)),
    el("span", {}, el("i", { class: "lg-err" }, "✕"), "Failed"), el("span", {}, el("i", { class: "lg-retry" }, "↻"), "Retry"),
    el("span", {}, el("i", { class: "lg-gap" }), "Idle, compressed"));
}

/**
 * Trace waterfall. Spans of kind "turn" open a group; every other span joins the latest turn that started
 * at or before it. Turns fold (more than foldAbove turns start folded; folded turns show child marks).
 * Keyboard (focus on the waterfall): ↑/↓ move, ← on a child selects its turn then folds it, → unfolds,
 * E (anywhere in keyScope, outside inputs) jumps to the next error, Cmd/Ctrl+F focuses span search,
 * Escape in search clears it without closing a drawer. Zoom ⤢ rescales to one turn; "← Full trace" restores.
 * @param {HTMLElement} container becomes the scrolling .wf
 * @param {Array<{ id?: string, name: string, kind: string, start: number, end?: number, ok?: boolean,
 *   retry?: boolean, tokens?: number, error?: string, pending?: boolean, prompt?: { type?: string, text?: string },
 *   action?: { label: string, onClick: Function } }>} spans times in seconds from trace start
 * @param {{ controls?: HTMLElement, legend?: boolean, detail?: HTMLElement, onSelect?: (span, info)=>void,
 *   summary?: ()=>Node, kindColor?: (kind:string)=>string, foldAbove?: number, keyScope?: HTMLElement,
 *   slowest?: (span)=>boolean }} [opts]
 *   controls: element that receives the toolbar (highlight errors, next error, collapse all, search, note);
 *   detail: element that receives the span detail panel; summary: what detail shows with nothing selected
 * @returns {{ setSpans(spans: Array): void, select(i:number): void, nextError(): void, toggle(turn:number): void,
 *   collapseAll(fold:boolean): void, zoom(turn:?number): void, highlightErrors(on:boolean): void,
 *   search(q:string): void, focus(): void, destroy(): void }}
 */
export function waterfall(container, spans, opts = {}) {
  const o = { foldAbove: 12, kindColor: kindToken, slowest: (s) => s.kind !== "llm" && s.kind !== "agent", ...opts };
  container.classList.add("wf");
  container.tabIndex = 0;
  if (!container.hasAttribute("aria-label")) container.setAttribute("aria-label", "Trace waterfall. Up and down arrows move, left and right fold and unfold, E jumps to the next error.");
  let data = [], rows = [], sel = -1, collapsed = new Set(), errCursor = -1, focusTurn = null, query = "", hl = false, turnCount = 0;
  const ui = {};
  if (o.controls) {
    ui.hl = el("button", { type: "button", class: "chip", "aria-pressed": "false", onclick: () => api.highlightErrors(!hl) }, "Highlight errors");
    ui.next = el("button", { type: "button", class: "chip", onclick: () => api.nextError() }, "Next error ", el("span", { class: "mono t-faint", "aria-hidden": "true" }, "E"));
    ui.fold = el("button", { type: "button", class: "chip", onclick: () => api.collapseAll(collapsed.size < turnCount) }, "Collapse all");
    ui.search = searchBox({ placeholder: "Find spans", label: "Search spans", kbd: "⌘F", className: "wf-search", onInput: (v) => api.search(v) });
    ui.search.input.addEventListener("keydown", (e) => { if (e.key === "Escape" && e.target.value) { e.stopPropagation(); e.preventDefault(); e.target.value = ""; api.search(""); } });
    ui.note = el("span", { class: "results", "aria-live": "polite" });
    o.controls.replaceChildren(el("div", { class: "dr-tools" }, ui.hl, ui.next, ui.fold, ui.search.label, ui.note), o.legend === false ? null : waterfallLegend());
  }
  const note = (t) => { if (ui.note) ui.note.textContent = t; };
  const kindVar = (x) => `var(${o.kindColor(x.kind)})`;
  const labelOf = (x, gi) => x.kind === "turn" ? `Turn ${gi + 1}` : x.kind === "llm" ? "LLM · " + x.name : x.kind === "agent" ? "Subagent · " + x.name : x.name;

  function render() {
    const turns = data.filter((s) => s.kind === "turn").sort((a, b) => a.start - b.start);
    const others = data.filter((s) => s.kind !== "turn").sort((a, b) => a.start - b.start);
    turnCount = turns.length;
    const groups = turns.map((t) => ({ t, kids: [] })), orphans = [];
    for (const x of others) { let g = null; for (let i = groups.length - 1; i >= 0; i--) if (groups[i].t.start <= x.start + 0.001) { g = groups[i]; break; } (g ? g.kids : orphans).push(x); }
    let scaleSpans = data, t0 = 0, total = Math.max(1, ...data.map((x) => x.end));
    if (focusTurn != null && groups[focusTurn]) { const g = groups[focusTurn]; scaleSpans = [g.t, ...g.kids]; t0 = g.t.start; total = Math.max(g.t.end, ...g.kids.map((k) => k.end)); }
    const sc = buildScale(scaleSpans.map((x) => ({ ...x, start: x.start - t0, end: x.end - t0 })), total - t0);
    const X = (t) => Math.max(0, Math.min(100, sc.X(t - t0)));
    const cand = others.filter(o.slowest);
    const slowest = cand.length ? cand.reduce((a, b) => (b.end - b.start > a.end - a.start ? b : a)) : null;
    const list = [];
    if (focusTurn == null) orphans.forEach((x) => list.push({ x, child: false, gi: -1 }));
    groups.forEach((g, gi) => {
      if (focusTurn != null && gi !== focusTurn) return;
      list.push({ x: g.t, turn: true, gi, kids: g.kids });
      g.kids.forEach((x) => list.push({ x, child: true, gi }));
    });
    const axisLab = focusTurn != null
      ? el("div", { class: "lab" }, el("button", { type: "button", class: "linkbtn", onclick: () => api.zoom(null) }, "← Full trace"), ` Turn ${focusTurn + 1}`)
      : el("div", { class: "lab" }, `${turns.length} turns · ${others.length} spans`);
    const ticks = el("div", { class: "wf-ticks" });
    for (const s of sc.segs) {
      if (s.gap) ticks.append(el("div", { class: "wf-gapmark", style: `left:${s.x0}%;width:${s.x1 - s.x0}%`, "data-tip": `${fmtDur(s.t1 - s.t0)} idle, compressed` }));
      else if (s.x1 - s.x0 > 7) ticks.append(el("div", { class: "wf-tick", style: `left:${s.x0}%` }, "+" + fmtDur(s.t0 + t0).replace(/ \d+s$/, "")));
    }
    const body = el("div", { class: "wf-rows", role: "treegrid", "aria-label": "Spans" });
    const gaps = sc.segs.filter((g) => g.gap);
    const q = query.trim().toLowerCase();
    rows = [];
    list.forEach((r, i) => {
      const x = r.x, bad = x.kind !== "turn" && !x.ok, nBad = r.turn ? r.kids.filter((k) => !k.ok).length : 0;
      const label = labelOf(x, r.gi), dur = x.end - x.start;
      const l = X(x.start), w = Math.max(X(x.end) - l, 0), folded = r.turn && collapsed.has(r.gi);
      const bar = el("div", { class: "wf-bar k-bar" + (r.turn ? " turnbar" : "") + (bad ? " err" : "") + (folded ? " folded" : ""),
        style: `--kc:${kindVar(x)};left:${l}%;width:max(${w}%,4px);` + stagger(i, 6, 400) },
      bad ? el("span", { class: "glyph", "aria-hidden": "true" }, "✕") : x.retry ? el("span", { class: "glyph", "aria-hidden": "true" }, "↻") : null,
      el("span", { class: "dl" }, (x.retry ? "retry · " : "") + fmtDur(dur) + (x.tokens ? " · " + fmtK(x.tokens) + " tok" : "") + (x.error ? " · " + x.error.replace(/_/g, " ") : "") + (r.turn && r.kids.length ? ` · ${r.kids.length} spans` : "")));
      const track = el("div", { class: "wf-track" }, gaps.map((g) => el("div", { class: "wf-gapcol", style: `left:${g.x0}%;width:${g.x1 - g.x0}%` })), bar);
      if (folded) {
        const marks = el("div", { class: "wf-marks", "aria-hidden": "true" });
        r.kids.slice(0, 400).forEach((k) => { const kl = X(k.start), kw = Math.max(X(k.end) - kl, 0); marks.append(el("i", { style: `left:${kl}%;width:max(${kw}%,2px);background:${k.ok ? kindVar(k) : "var(--err)"}` })); });
        track.append(marks);
      }
      const lab = el("div", { class: "wf-lab" },
        r.turn ? el("span", { class: "tw" + (folded ? " closed" : ""), "aria-hidden": "true" }, "▾") : null,
        el("span", { class: "kind", style: `background:${kindVar(x)}` }),
        el("span", { class: "nm" }, label, r.turn && x.prompt ? el("span", { class: "pr" }, x.prompt.text || x.prompt.type) : null),
        x === slowest ? pill("slowest", "warn") : null,
        bad ? el("span", { class: "x", "aria-label": "error" }, "✕") : nBad ? el("span", { class: "x", "aria-label": nBad + " errors" }, nBad + "✕") : null,
        r.turn && focusTurn == null ? el("button", { type: "button", class: "wf-focus", tabindex: "-1", "aria-label": `Zoom to turn ${r.gi + 1}`, "data-tip": "Zoom to this turn",
          onclick: (e) => { e.stopPropagation(); api.zoom(r.gi); } }, "⤢") : null);
      const row = el("div", { class: "wf-row" + (r.turn ? " turn" : "") + (r.child ? " child" : "") + (bad ? " err" : ""), role: "row", "aria-selected": "false",
        "aria-expanded": r.turn ? String(!folded) : null,
        "data-tip": `${label}\n+${fmtDur(x.start)} → ${fmtDur(dur)}${x.error ? "\nerror: " + x.error : ""}${x.pending ? "\nno result logged" : ""}`,
        onclick: (e) => { if (r.turn && (e.target.closest(".tw") || e.target.closest(".wf-marks") || e.detail === 2)) api.toggle(r.gi); api.select(i); } }, lab, track);
      if (r.child && collapsed.has(r.gi)) row.hidden = true;
      if (q && x.kind !== "turn" && (label + " " + (x.error || "")).toLowerCase().includes(q)) row.classList.add("match");
      body.append(row); rows.push({ r, row });
    });
    container.replaceChildren(el("div", { class: "wf-axis" }, axisLab, ticks), body);
    container.classList.toggle("searching", !!q);
    container.classList.toggle("hl", hl);
    if (ui.fold) ui.fold.textContent = collapsed.size && collapsed.size >= turns.length ? "Expand all" : "Collapse all";
  }

  function detail(w) {
    if (!o.detail) return;
    const box = o.detail; box.classList.add("detail"); box.replaceChildren();
    if (!w) {
      if (o.summary) box.append(o.summary());
      box.append(el("p", { class: "foot" }, "Select a row (or use the arrow keys) to inspect a span. E jumps to the next error."));
      return;
    }
    const x = w.r.x, isTurn = !!w.r.turn;
    box.append(el("h4", {}, el("span", { class: "dot", style: `background:${kindVar(x)}` }), isTurn ? `Turn ${w.r.gi + 1}` : x.kind === "llm" ? "LLM call" : x.name,
      isTurn ? null : pill(x.ok ? (x.pending ? "no result" : "ok") : "error", x.ok ? "ok" : "err")));
    const kv = [["Kind", isTurn ? "turn (prompt to next prompt)" : KIND_LABEL[x.kind] || x.kind], ["Starts at", "+" + fmtDur(x.start)], ["Duration", fmtDur(x.end - x.start)]];
    if (x.kind === "llm") kv.push(["Model", x.name]);
    if (x.tokens != null) kv.push(["Tokens", fmtN(x.tokens)]);
    if (x.retry) kv.push(["Retry", "yes"]);
    if (x.error) kv.push(["Error type", x.error]);
    if (isTurn) kv.push(["Spans in turn", w.r.kids.length], ["Errors in turn", w.r.kids.filter((k) => !k.ok).length]);
    box.append(kvList(kv));
    if (isTurn && x.prompt) box.append(el("div", { class: "well" }, x.prompt.type ? pill(x.prompt.type, null, { style: "margin-right:6px" }) : null, x.prompt.text || "no text"));
    if (isTurn && focusTurn == null) box.append(el("button", { type: "button", class: "chip", onclick: () => api.zoom(w.r.gi) }, "Zoom to this turn ⤢"));
    if (x.action) box.append(el("button", { type: "button", class: "chip", onclick: x.action.onClick }, x.action.label));
  }

  container.addEventListener("keydown", (e) => {
    const vis = rows.map((w, i) => [w, i]).filter(([w]) => !w.row.hidden).map(([, i]) => i);
    const pos = vis.indexOf(sel);
    if (e.key === "ArrowDown") { e.preventDefault(); api.select(vis[Math.min(pos + 1, vis.length - 1)] ?? vis[0]); }
    else if (e.key === "ArrowUp") { e.preventDefault(); api.select(vis[Math.max(pos - 1, 0)] ?? vis[0]); }
    else if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && rows[sel]) {
      e.preventDefault();
      const w = rows[sel], gi = w.r.gi; if (gi < 0) return;
      const fold = e.key === "ArrowLeft";
      if (fold && w.r.child) api.select(rows.findIndex((x) => x.r.turn && x.r.gi === gi));
      if (collapsed.has(gi) !== fold) api.toggle(gi);
    }
  });
  const scope = o.keyScope || container;
  const onScopeKey = (e) => {
    const inInput = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement && document.activeElement.tagName);
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "f" && ui.search) { e.preventDefault(); ui.search.input.focus(); }
    else if ((e.key === "e" || e.key === "E") && !e.metaKey && !e.ctrlKey && !e.altKey && !inInput) { e.preventDefault(); api.nextError(); }
  };
  scope.addEventListener("keydown", onScopeKey);

  const api = {
    setSpans(next) {
      data = next.map((s) => ({ ...s, end: s.end ?? s.start, ok: s.ok !== false }));
      const nTurns = data.filter((s) => s.kind === "turn").length;
      collapsed = new Set(nTurns > o.foldAbove ? Array.from({ length: nTurns }, (_, i) => i) : []);
      sel = -1; errCursor = -1; focusTurn = null; query = "";
      if (ui.search) { ui.search.input.value = ""; ui.search.kbd.textContent = "⌘F"; }
      note(""); render(); detail(null);
    },
    select(i) {
      if (sel >= 0 && rows[sel]) rows[sel].row.setAttribute("aria-selected", "false");
      sel = i; const w = rows[i]; if (!w) return;
      w.row.setAttribute("aria-selected", "true");
      w.row.scrollIntoView({ block: "nearest" });
      detail(w);
      if (o.onSelect) o.onSelect(w.r.x, { turn: !!w.r.turn, index: i, group: w.r.gi });
    },
    toggle(gi) {
      collapsed.has(gi) ? collapsed.delete(gi) : collapsed.add(gi);
      const keep = rows[sel] && rows[sel].r.x;
      render();
      const ni = keep ? rows.findIndex((w) => w.r.x === keep) : -1;
      sel = -1; if (ni >= 0) api.select(ni);
    },
    nextError() {
      const errs = rows.map((w, i) => [w, i]).filter(([w]) => !w.r.turn && !w.r.x.ok);
      if (!errs.length) { note("No failed spans"); return; }
      errCursor = (errCursor + 1) % errs.length;
      const [w, i] = errs[errCursor];
      if (w.r.child && collapsed.has(w.r.gi)) { const x = w.r.x; collapsed.delete(w.r.gi); render(); sel = -1; api.select(rows.findIndex((v) => v.r.x === x)); }
      else api.select(i);
      note(`Error ${errCursor + 1} of ${errs.length}`);
    },
    collapseAll(fold) {
      const turns = rows.filter((w) => w.r.turn).map((w) => w.r.gi);
      collapsed = new Set(fold ? turns : []);
      sel = -1; render(); detail(null);
    },
    zoom(gi) { focusTurn = gi; if (gi != null) collapsed.delete(gi); sel = -1; errCursor = -1; render(); detail(null); },
    highlightErrors(on) { hl = !!on; container.classList.toggle("hl", hl); if (ui.hl) ui.hl.setAttribute("aria-pressed", String(hl)); },
    search(qs) {
      query = qs || "";
      if (ui.search) { if (ui.search.input.value !== query) ui.search.input.value = query; ui.search.kbd.textContent = query ? "Esc" : "⌘F"; }
      const q = query.trim().toLowerCase();
      if (q) { // unfold every turn that holds a match
        for (const w of rows) if (w.r.child && (labelOf(w.r.x, w.r.gi) + " " + (w.r.x.error || "")).toLowerCase().includes(q)) collapsed.delete(w.r.gi);
      }
      sel = -1; render();
      const n = rows.filter((w) => w.row.classList.contains("match")).length;
      note(q ? `${n} matching span${n === 1 ? "" : "s"}` : "");
    },
    focus() { container.focus(); },
    destroy() { scope.removeEventListener("keydown", onScopeKey); container.replaceChildren(); },
  };
  api.setSpans(spans);
  return api;
}

/* ---------------------------------------------------------------- latency */

/**
 * Latency range bars: solid to p50, lighter to p95, a tick at p95. Log scale by default.
 * @param {HTMLElement} container contents replaced
 * @param {Array<{ label: string, p50: number, p95: number, n?: number, color?: string, dotColor?: string, tip?: string }>} rows
 *   seconds; color defaults to "--accent"
 * @param {{ format?: (s:number)=>string, log?: boolean, empty?: string }} [opts]
 */
export function latencyRows(container, rows, opts = {}) {
  const f = opts.format || fmtDur;
  const maxV = Math.max(...rows.map((r) => r.p95), 0) || 1;
  const L = opts.log === false ? (v) => (v / maxV) * 100 : (v) => (Math.log10(1 + Math.max(v, 0) * 1000) / Math.log10(1 + maxV * 1000)) * 100;
  container.replaceChildren(...rows.map((r, i) => {
    const c = cv(r.color || "--accent");
    return el("div", { class: "lat-row fade-in", style: stagger(i, 20), "data-tip": r.tip || `${r.label}\n${r.n != null ? "n " + fmtN(r.n) + " · " : ""}p50 ${f(r.p50)} · p95 ${f(r.p95)}` },
      el("span", { class: "bl-name" }, r.dotColor ? dot(r.dotColor) : null, el("span", { class: "bl-text" }, r.label)),
      el("div", { class: "lat-track", role: "img", "aria-label": `p50 ${f(r.p50)}, p95 ${f(r.p95)}` },
        el("div", { class: "lat-p50 grow-x", style: `width:${L(r.p50)}%;background:${c}` }),
        el("div", { class: "lat-p95 grow-x", style: `left:${L(r.p50)}%;width:${Math.max(L(r.p95) - L(r.p50), 0)}%;background:${c}` }),
        el("div", { class: "lat-tick", style: `left:calc(${L(r.p95)}% - 1px)` })),
      el("div", { class: "lat-vals num" }, el("span", {}, "p50 ", el("b", {}, f(r.p50))), el("span", {}, "p95 ", el("b", {}, f(r.p95))),
        r.n != null ? el("span", { class: "t-faint" }, "n " + fmtK(r.n)) : null));
  }));
  if (!rows.length) container.append(emptyState(opts.empty || "No latency logged."));
}

/* ---------------------------------------------------------------- error groups */

/**
 * Error group accordion: one header per group (count pill, rate), rows per error type with a per-day spark
 * and session chips. The first group starts open; collapsed bodies are inert.
 * @param {HTMLElement} container contents replaced
 * @param {Array<{ id: string, label: string, color?: string, count: number, rate?: string,
 *   items: Array<{ type: string, count: number, heads?: string, perDay?: number[],
 *   sessions?: Array<string|{ id: string, label?: string, tip?: string }> }> }>} groups
 * @param {{ open?: string[], onSession?: (id:string, button:HTMLElement)=>void, dayLabels?: string[], empty?: string }} [opts]
 */
export function errorGroups(container, groups, opts = {}) {
  const open = new Set(opts.open || (groups[0] ? [groups[0].id] : []));
  container.replaceChildren();
  if (!groups.length) { container.append(emptyState(opts.empty || "No failures in this window.")); return; }
  groups.forEach((g, gi) => {
    const bid = `eg-${gi}-${Math.random().toString(36).slice(2, 6)}`;
    const body = el("div", { class: "eg-b" + (open.has(g.id) ? " open" : ""), id: bid }, el("div", {}, g.items.map((it) => {
      const per = it.perDay || [], max = Math.max(...per, 1);
      const spark = sv("svg", { class: "espark", width: 84, height: 18, viewBox: "0 0 84 18", "aria-hidden": "true" });
      const step = per.length ? 84 / per.length : 12;
      per.forEach((v, i) => { const h = v ? Math.max(2, (v / max) * 16) : 1; spark.append(sv("rect", { class: v ? "on" : null, x: i * step, y: 18 - h, width: Math.max(step - 3, 1), height: h, rx: 1, "data-tip": `${(opts.dayLabels || [])[i] ?? "day " + (i + 1)}: ${v}` })); });
      const sess = (it.sessions || []).map((s) => (typeof s === "string" ? { id: s, label: s.slice(0, 8) } : s));
      return el("div", { class: "er" },
        el("span", { class: "et" }, it.type.replace(/_/g, " ")),
        el("span", { class: "num ec" }, fmtN(it.count)),
        el("span", { class: "bl-meta" }, `${sess.length} session${sess.length === 1 ? "" : "s"}`, it.heads ? el("div", { class: "heads", "data-tip": it.heads }, it.heads) : null),
        el("span", { class: "sess" }, sess.slice(0, 8).map((s) => el("button", { type: "button", "data-tip": s.tip || s.id, onclick: opts.onSession ? (e) => opts.onSession(s.id, e.currentTarget) : null }, s.label || s.id.slice(0, 8))),
          sess.length > 8 ? el("span", { class: "bl-meta" }, `+${sess.length - 8}`) : null),
        spark);
    })));
    body.inert = !open.has(g.id);
    const head = el("button", { type: "button", class: "eg-h", "aria-expanded": String(open.has(g.id)), "aria-controls": bid, onclick: () => {
      const on = !open.has(g.id); on ? open.add(g.id) : open.delete(g.id);
      head.setAttribute("aria-expanded", String(on)); body.classList.toggle("open", on); body.inert = !on;
    } },
    el("span", { class: "tw", "aria-hidden": "true" }, "▶"),
    el("span", { class: "name" }, g.color ? el("span", { class: "tool-badge" }, dot(g.color), g.label) : g.label, " ", el("span", { class: "bl-meta" }, `${g.items.length} error type${g.items.length === 1 ? "" : "s"}`)),
    pill(fmtN(g.count) + " failed", "err"),
    el("span", { class: "num rate" }, g.rate || ""));
    container.append(el("div", { class: "eg" }, head, body));
  });
}

/* ---------------------------------------------------------------- heat strip */

/**
 * Heat strip: one 14px cell per item, tinted by score tone (grey when score is null). Cells are buttons.
 * @param {HTMLElement} container becomes .heat
 * @param {Array<{ id?: string, label: string, score: ?number, tip?: string }>} cells
 * @param {{ thresholds?: [number, number], onSelect?: (cell, button:HTMLElement)=>void }} [opts]
 */
export function heatStrip(container, cells, opts = {}) {
  container.classList.add("heat");
  container.replaceChildren(...cells.map((c, i) => el("button", { type: "button",
    style: `--hc:${c.score == null ? "var(--kind-other)" : toneVar(toneOf(c.score, opts.thresholds))};` + stagger(i, 6, 600),
    "aria-label": c.label + (c.score == null ? ", no score" : `, score ${c.score}`),
    "data-tip": c.tip || `${c.label}\n${c.score == null ? "no score" : "score " + c.score}`,
    onclick: opts.onSelect ? (e) => opts.onSelect(c, e.currentTarget) : null })));
  return container;
}

/**
 * Legend for heatStrip / healthDot tones.
 * @param {[number, number]} [th] default [85, 70]
 * @param {string} [nullLabel]
 */
export function heatLegend(th = [85, 70], nullLabel = "no data") {
  const item = (c, t) => el("span", {}, el("span", { class: "dot", style: `background:${c}` }), t);
  return el("div", { class: "heat-legend" }, item("var(--ok)", `${th[0]}+`), item("var(--warn)", `${th[1]}–${th[0] - 1}`), item("var(--err)", `under ${th[1]}`), item("var(--kind-other)", nullLabel));
}

/* ---------------------------------------------------------------- scores */

/**
 * Score ring (conic progress). Tone from thresholds unless a colour is given.
 * @param {number} ratio 0..1
 * @param {{ thresholds?: [number, number], color?: string, tip?: string }} [opts] thresholds default [0.9, 0.75]
 */
export function scoreRing(ratio, opts = {}) {
  const c = opts.color ? cv(opts.color) : toneVar(toneOf(ratio, opts.thresholds || [0.9, 0.75]));
  return el("span", { class: "ring", role: "img", "aria-label": fmtPct(ratio), style: `--p:${(ratio * 100).toFixed(1)};--c:${c}`, "data-tip": opts.tip ?? fmtPct(ratio) });
}

/**
 * Score card: dashed border and a dashed "heuristic" pill mark an inferred value. The value counts up.
 * @param {{ label: string, value: ?number, format?: Function, ring?: number, ringTip?: string, foot?: string, heuristic?: boolean }} c
 * @returns {HTMLElement} .score
 */
export function scoreCard(c) {
  const v = el("span");
  const node = el("div", { class: "score" },
    el("div", { class: "l" }, el("span", {}, c.label), c.heuristic === false ? null : pill("heuristic", "heur")),
    el("div", { class: "row" }, el("span", { class: "v" }, v), c.ring != null ? scoreRing(c.ring, { tip: c.ringTip }) : null),
    c.foot ? el("div", { class: "f" }, c.foot) : null);
  countUp(v, c.value, c.format || fmtN);
  return node;
}

/**
 * Histogram (SVG): values binned over a domain, bars toned by bin start, count labels on top.
 * @param {SVGSVGElement} svg
 * @param {number[]} values
 * @param {{ bins?: number, domain?: [number, number], height?: number, tone?: (binStart:number)=>string,
 *   label?: string, unit?: string }} [opts] tone returns a token name; default 80+ ok, 70+ warn, else err
 * @returns {{ update(values:number[]): void, destroy(): void }}
 */
export function histogram(svg, values, opts = {}) {
  const o = { bins: 10, domain: [0, 100], height: 300, tone: (b) => (b >= 80 ? "--ok" : b >= 70 ? "--warn" : "--err"), unit: "items", ...opts };
  let data = values;
  svg.classList.add("chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", o.label || "Histogram");
  function draw(animate = true) {
    svg.replaceChildren();
    const W = svg.clientWidth || 500, H = o.height, padB = 22, padT = 14, padL = 8;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("height", H);
    const [d0, d1] = o.domain, size = (d1 - d0) / o.bins;
    const bins = Array.from({ length: o.bins }, () => 0);
    data.forEach((v) => { bins[Math.max(0, Math.min(o.bins - 1, Math.floor((v - d0) / size)))]++; });
    const max = Math.max(...bins, 1), bw = (W - padL) / o.bins;
    bins.forEach((n, i) => {
      const h = (n / max) * (H - padB - padT), b0 = d0 + i * size, x = padL + i * bw;
      svg.append(sv("rect", { class: animate && !reduced() ? "grow-y" : null, style: animate ? stagger(i, 30) : null, x: x + 3, y: H - padB - h, width: Math.max(bw - 6, 1), height: Math.max(h, 0), rx: 3,
        fill: `color-mix(in oklab, ${cv(o.tone(b0))} 70%, transparent)`, "data-tip": `${Math.round(b0)}–${Math.round(b0 + size) - 1}: ${n} ${o.unit}` }));
      const t = sv("text", { x: x + bw / 2, y: H - 6, "text-anchor": "middle" }); t.textContent = Math.round(b0); svg.append(t);
      if (n) { const t2 = sv("text", { x: x + bw / 2, y: H - padB - h - 4, "text-anchor": "middle" }); t2.textContent = n; svg.append(t2); }
    });
  }
  draw();
  const off = onWidth(svg, () => draw(false));
  return { update(next) { data = next; draw(); }, destroy: off };
}

/* ---------------------------------------------------------------- tooltip */

let TIP = null;
/**
 * Install the shared tooltip: any element with [data-tip] shows its text (newlines kept) near the pointer,
 * or under the element on keyboard focus. Idempotent; returns the same handle on repeat calls.
 * @param {Document|HTMLElement} [root]
 * @returns {{ el: HTMLElement, destroy(): void }}
 */
export function tooltip(root = document) {
  if (TIP) return TIP;
  const tip = el("div", { class: "tip", role: "tooltip" });
  document.body.append(tip);
  const hide = () => tip.classList.remove("on");
  const show = (t, x, y) => {
    tip.textContent = t.getAttribute("data-tip");
    const r = tip.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > innerWidth - 8) left = Math.max(8, x - r.width - 14);
    if (top + r.height > innerHeight - 8) top = Math.max(8, y - r.height - 14);
    tip.style.left = left + "px"; tip.style.top = top + "px"; tip.classList.add("on");
  };
  const onMove = (e) => { const t = e.target.closest && e.target.closest("[data-tip]"); if (!t || !t.getAttribute("data-tip")) return hide(); show(t, e.clientX, e.clientY); };
  const onFocus = (e) => { const t = e.target.closest && e.target.closest("[data-tip]"); if (!t || !t.getAttribute("data-tip") || !t.matches(":focus-visible")) return hide(); const r = t.getBoundingClientRect(); show(t, r.left, r.bottom - 8); };
  root.addEventListener("pointermove", onMove);
  root.addEventListener("focusin", onFocus);
  root.addEventListener("focusout", hide);
  addEventListener("scroll", hide, true);
  TIP = { el: tip, destroy() { root.removeEventListener("pointermove", onMove); root.removeEventListener("focusin", onFocus); root.removeEventListener("focusout", hide); removeEventListener("scroll", hide, true); tip.remove(); TIP = null; } };
  return TIP;
}
