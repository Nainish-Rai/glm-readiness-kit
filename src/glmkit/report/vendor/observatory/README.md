# Observatory design system

A dark-only, dependency-free design system and component library for data dashboards: tokens, component CSS and
framework-free JavaScript. It is lifted from the Harness Observatory dashboard template. Type is Geist and Geist Mono.

- `gallery.html` renders every component with small fictional data and a one-line usage snippet.
- `layout.html` is a full dashboard shell (header, tabs, KPI strip, overview grid, list view, drawer, scores)
  marked with `<!-- region: name -->` comments so you can gut it and refill it.

Both open straight from disk (`file://`).

## Install

Copy three files, and a fourth if pages will be opened from disk:

| File | What it is |
|---|---|
| `tokens.css` | The `:root` token layer: primitives, semantic roles, motion presets, reduced-motion block |
| `components.css` | Component styles. Reads tokens only, holds no literal colours |
| `components.js` | ES module exporting every component function |
| `components.iife.js` | Generated classic-script build that sets `window.Observatory`. Use it for pages opened over `file://`, where Chrome refuses ES modules |

```html
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap">
<link rel="stylesheet" href="tokens.css">
<link rel="stylesheet" href="components.css">

<!-- served over http(s): -->
<script type="module">
  import { kpiStrip, tooltip } from "./components.js";
  tooltip();
  kpiStrip(document.getElementById("kpis"), [{ label: "Tool calls", value: 48213, sub: "312 sessions" }]);
</script>

<!-- or opened from disk: -->
<script src="components.iife.js"></script>
<script>const { kpiStrip, tooltip } = window.Observatory;</script>
```

After you edit `components.js` or `components.css`, run `python3 -I build.py`. It regenerates
`components.iife.js` and the token contract (see Checks). `python3 -I build.py --check` exits 1 if either is stale.

## Token layers

| Layer | Examples | Rule |
|---|---|---|
| Primitives | `--gray-0..10`, `--indigo-4/6`, `--green-6`, `--amber-6`, `--red-6`, `--cat-1..8`, `--space-*`, `--radius-*` | The only place a literal colour may appear. Only the semantic layer reads them |
| Semantic roles | `--surface-chrome/canvas/card/raised/overlay/well`, `--edge(-strong)`, `--fg-emph/body/meta/faint`, `--accent(-fill/-tint)`, `--ok/warn/err(-tint)`, `--kind-turn/llm/shell/read/edit/agent/web/mcp/other`, `--tool-claude/codex`, `--scrim`, `--shadow-pop`, type scale `--text-*`, `--font-sans/mono` | What components read |
| Motion presets | `--ease-out`, `--ease-draw`, `--ease-sheet`, `--dur-micro..count` | Durations by job. All become 0 under `prefers-reduced-motion` |

One exception: `--cat-1..8` also serve as the categorical palette for chart series, as they did in the template.
To re-theme, change the primitives. To change what a colour means, change the semantic layer. Components never add
a colour; add a semantic role first.

In JavaScript, every colour option takes a token name (`"--cat-1"`, `"--ok"`) or any CSS colour value.
Token names become `var(--x)`.

## Components

`components.css` class → `components.js` function. Every function has a JSDoc block in `components.js` that
documents the data shape it takes.

| Component | CSS | Function |
|---|---|---|
| DOM helpers | | `el(tag, attrs, ...kids)`, `sv(tag, attrs)`, `cv(color)`, `stagger(i, step, cap)`, `reduced()` |
| Formatting | | `fmtN(n)`, `fmtK(n)`, `fmtDur(seconds)`, `fmtPct(ratio, d)`, `niceMax(v)`; null shows "not logged" |
| Tone | `.pill.ok/.warn/.err/.heur`, `.dot`, `.health` | `toneOf(v, [ok, warn])`, `toneVar(tone)`, `pill(text, tone, attrs)`, `dot(color)`, `healthDot(score, { thresholds, tip })` |
| KPI strip / stat tile | `.kpis > .kpi`, `.is-warn`, `.is-err` | `kpiStrip(container, tiles)`, `kpiTile({ label, value, format, unit, sub, spark, sparkColor, tone })` |
| Number ticker | `.ticker` | `ticker(node, value, format)`, `countUp(node, value, format)` |
| Sparkline | `.spark` | `sparkline(values, { width, height, color, label })` → `<svg>` |
| Segmented control, chips, legend | `.seg`, `.chip[aria-pressed]`, `.legend` | `segmented(container, options, { value, label, onChange })`, `toggleChip(label, { pressed, onChange })`, `legend(container, items, { onToggle })` |
| Search field | `.search` | `searchBox({ placeholder, label, kbd, onInput })` → `{ label, input, kbd }` |
| Tabs | `.tabs`, `.count` | `tabs(nav, [{ id, label, count, panel }], { value, onChange })` → `{ select, setCount }` |
| Card | `.card`, `.card-h`, `.card-b`, `.card.heuristic` | markup only |
| App shell | `.top`, `.top-in`, `.brand`, `.brand-mark`, `.window`, `.main`, `.grid`, `.c-4..c-12`, `.stack`, `.toolbar`, `.view` | markup only (see `layout.html`) |
| Bar list | `.barlist > .bl-row` | `barList(rows, { format, max, mono, empty })` → element |
| Stacked bar chart | `.chart` | `stackedBars(svg, [{ name, color, values }], { labels, height, format, legend })` → `{ update, destroy }` |
| Context composition bar | `.ctxmap`, `.ctx-bar` | `compositionBar([{ label, value, color }], { format, label, kv })` → element |
| Ordered treemap | `.tmap > .tile`, `.tile.rest` | `squarify(items, x, y, w, h)`, `treemap(container, items, { max, format, onSelect, restLabel })` → `{ update, destroy }` |
| Data table | `.tbl`, `.tbl-wrap`, `th[aria-sort]` | `sortableTable(table, columns, rows, { sort, rowKey, onRowClick, onRender, empty })` → `{ setRows, sortBy, select }` |
| Split bar | `.split` | `splitBar(ok, failed, { label })` |
| Drawer + scrim | `.drawer`, `.scrim`, `.dr-*` | `drawer({ onClose, onOpen, title, element, scrim, initialFocus })` → `{ open(opener), close, header, lead, tag, body, setTitle, isOpen }` |
| Stat strip | `.statstrip`, `.dr-stats` | `statStrip([{ label, value, tone }])`, `statLine([[label, value]])` |
| Waterfall | `.wf`, `.wf-axis`, `.wf-row`, `.wf-bar`, `.wf-legend` | `waterfall(container, spans, { controls, detail, summary, keyScope, onSelect, foldAbove, kindColor })` → `{ setSpans, select, nextError, toggle, collapseAll, zoom, highlightErrors, search, focus, destroy }`; `buildScale(spans, total)`, `waterfallLegend(kinds)`, `kindToken(kind)` |
| Span detail | `.detail`, `.kv`, `.well`, `.kids` | `kvList([[key, value]])` (the waterfall renders its own detail into `opts.detail`) |
| Latency range bar | `.lat-row` | `latencyRows(container, [{ label, p50, p95, n, color }], { format, log })` |
| Error group accordion | `.eg`, `.eg-h`, `.eg-b`, `.er` | `errorGroups(container, groups, { open, onSession, dayLabels })` |
| Heat strip | `.heat`, `.heat-legend` | `heatStrip(container, [{ id, label, score, tip }], { thresholds, onSelect })`, `heatLegend(thresholds, nullLabel)` |
| Score card + ring | `.score-cards`, `.score`, `.ring` | `scoreCard({ label, value, format, ring, ringTip, foot, heuristic })`, `scoreRing(ratio, { thresholds, color, tip })` |
| Histogram | `.chart` | `histogram(svg, values, { bins, domain, height, tone, unit })` → `{ update, destroy }` |
| Tooltip | `.tip` | `tooltip(root)`: shows any `[data-tip]` (newlines kept) near the pointer or under a focused element |
| Empty state | `.empty` | `emptyState(text)` |
| Fade / grow | `.fade-in`, `.grow-x`, `.grow-y` | add the class; stagger with `stagger(i)` |

Waterfall span shape: `{ name, kind, start, end, ok, retry, tokens, error, pending, prompt: { type, text }, action: { label, onClick } }`.
Times are seconds from trace start. `kind: "turn"` opens a group, and every other span joins the latest turn that
started at or before it. The kinds that have colours are `llm`, `shell`, `read`, `edit`, `agent`, `web` and `mcp`;
any other kind uses `--kind-other`.

## Behaviour contracts

| Component | Event | What happens |
|---|---|---|
| Table row (`onRowClick`) | Click or Enter | Calls `onRowClick(row, tr)`. In the layout this opens the drawer and `select(id)` sets `aria-selected=true` |
| Drawer | Escape, Close, scrim click | Closes; focus returns to the opener (row, tile, heat cell or chip). Escape that a child already handled (`defaultPrevented`) does not close it. Tab stays inside while open |
| Drawer | `open()` while already open | Keeps the first opener, so focus still returns to the right place after in-drawer navigation |
| Waterfall | ↑ / ↓ | Moves the selection through visible rows; the detail panel follows |
| Waterfall | ← on a child | Selects its turn, then folds it; → on a folded turn unfolds it |
| Waterfall | E or "Next error" | Unfolds the turn that holds the next failed span and selects it; "Error n of m" shows |
| Waterfall | Zoom ⤢ on a turn | Rescales the axis to that turn; "← Full trace" restores it |
| Waterfall | Highlight errors | Fades every non-error, non-turn row to 25% |
| Waterfall | Search, Cmd/Ctrl+F | Unfolds turns that hold a match, dims the rest, shows the match count; Escape in the field clears it without closing the drawer |
| Waterfall | More than `foldAbove` (12) turns | Every turn starts folded and shows child marks |
| Sortable header | Click, Enter or Space | Sorts descending first and toggles on repeat; `aria-sort` is set |
| Tabs | Click, ← → Home End | Selects the tab and hides the other panels |
| Segmented filter (layout) | Click | Every view, KPI and count re-renders for that choice |
| Error group | Click header | Toggles; a collapsed body is `inert` |
| `/` (layout) | Keypress outside an input | Opens the list view and focuses search |

## Tone parity

| Primitive | ok | warn | err | heuristic / muted |
|---|---|---|---|---|
| Pill | yes | yes | yes | yes (dashed) |
| Health dot | 85+ | 70–84 | under 70 | grey (no score) |
| Waterfall bar | kind tint | n/a (Slowest uses a warn pill) | red fill and edge | folded turn at 55% |
| KPI value | default | `is-warn` | `is-err` | n/a |
| Heat cell | yes | yes | yes | grey |
| Score ring | 0.9+ | 0.75+ | below | n/a |

## Motion presets

| Job | Token | Value | Reduced motion |
|---|---|---|---|
| hover / press, tooltip | `--dur-micro` | 100ms | 0 |
| collapse | `--dur-collapse` | 150ms, `--ease-out` | 0 |
| exit (drawer) | `--dur-exit` | 160ms, `--ease-sheet` | 0 |
| enter (drawer, fade-in, tiles, waterfall bars) | `--dur-enter` | 220ms, `--ease-sheet` / `--ease-out` | 0 |
| chart draw | `--dur-draw` | 550ms, `--ease-draw` | 0 |
| counters | `--dur-count` | ticker roll 900ms (`--dur-count` × 1.125), 45ms stagger; countUp 800ms | final value, no roll |

Under `prefers-reduced-motion: reduce`, every duration token is 0 and entry animations are switched off in CSS,
so staggered delays never hold content hidden. Every script animation (ticker, countUp, sparkline draw, staggers)
checks `reduced()` and renders the final state.

## Checks

```sh
python3 -I check_tokens.py tokens.css components.css gallery.html
python3 -I build.py --check
```

`check_tokens.py` flags literal colours outside the primitive block, `var()` names that no custom property defines,
missing reduced-motion handling, and fixed `transition`/`animation` durations that reduced motion cannot zero. It checks each file on its own. `components.css` reads its tokens from
`tokens.css`, so it carries a token contract: a comment between `@contract-start` and `@contract-end` that declares
every token it reads. `build.py` regenerates that list from the file's `var()` calls and fails if `tokens.css` does
not define one of them. A typo in a `var()` name is still caught, because the contract is generated from what the
file actually uses and then checked against `tokens.css`. `gallery.html` and `layout.html` contain no `var()` at all;
they pass token names to `components.js`.

`layout.html` has no motion or style block of its own, so the checker reports one `reduced-motion` finding for it.
The page inherits the reduced-motion block from `tokens.css` and the `reduced()` guards in `components.js`.

## Sources

The patterns follow observability products, chiefly Langfuse, Arize Phoenix, Sentry and Datadog (with Braintrust,
Honeycomb and SigNoz noted in the original registry), and the public demos on affectui.com, uiarc.dev and
kobra.systems. Paid components (for example the Context Map treemap and the Trace Waterfall details) were rebuilt
clean-room from what those public demos show. No paid source was copied. The number ticker, segmented control and
stats band are modelled on MIT-licensed public components; no code from them is included.
