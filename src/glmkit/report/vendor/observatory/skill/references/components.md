# Component registry

One canonical component per family. Functions are exported by `assets/components.js` (and
`window.Observatory` in `components.iife.js`); signatures and data shapes are in `assets/README.md`, and every
component is shown live in `assets/gallery.html`.
Sources: "R" = observability products (Langfuse, Phoenix, Sentry, Datadog, Braintrust, Honeycomb, SigNoz),
"A" = affectui.com, "U" = uiarc.dev, "K" = kobra.systems. Paid items were rebuilt clean-room from public demos;
no paid source was copied.

| Family | Function / class | Source pattern |
|---|---|---|
| Stat tile / KPI strip | `kpiStrip()`, `kpiTile()`, `ticker()`, `sparkline()` | R: Datadog overview row; A: stat cards, number ticker; U: stats band |
| Bar list | `barList()` | R: Langfuse bar list |
| Stacked bar chart | `stackedBars()` | R: Langfuse model usage |
| Composition bar | `compositionBar()` | A: usage meter; U: budget bar |
| Ordered treemap | `treemap()`, `squarify()` | A: context map (clean-room) |
| Data table | `sortableTable()` | R: Langfuse traces table |
| Split bar (ok/failed) | `splitBar()` | U: diffstat |
| Pill, dot, health dot | `pill()`, `dot()`, `healthDot()` | A: status palette; R: Datadog dashed "inferred" |
| Chip / segmented control / tabs | `toggleChip()`, `segmented()`, `tabs()` | A: segmented control |
| Search box, legend | `searchBox()`, `legend()`, `waterfallLegend()` | R: Langfuse filters |
| Drawer | `drawer()` | R: Braintrust side panel |
| Stat strip / line, key-value list | `statStrip()`, `statLine()`, `kvList()` | A: trace waterfall header |
| Waterfall | `waterfall()`, `buildScale()` | R: Langfuse timeline + gap compression; A: folded child marks, slowest tag; U: orchestrator lanes; K: search, ✕, ↻, hatched waits |
| Latency range bars | `latencyRows()` | R: Langfuse latency percentiles; U: uptime p50/p95 |
| Error groups | `errorGroups()` | R: Sentry issues; U: approvals queue |
| Heat strip | `heatStrip()`, `heatLegend()` | A: fan-out grid; U: eval grid |
| Score card | `scoreCard()`, `scoreRing()` | R: Phoenix thresholds |
| Histogram | `histogram()` | R: Datadog distribution |
| Tooltip | `tooltip()` + `[data-tip]` | A: chart tooltip |
| Empty state | `emptyState()` | |

## Behaviour contracts

| Component | Event | What happens |
|---|---|---|
| Table row | Click or Enter | Opens the drawer for that item; the row gets `aria-selected=true` |
| Drawer | Escape, Close, scrim click | Closes; focus returns to the opener (row, tile, heat cell or error chip) |
| Waterfall | ↑ / ↓ | Moves selection through visible rows; detail panel follows |
| Waterfall | ← on a child | Selects its turn, then folds it; → on a folded turn unfolds it |
| Waterfall | E or "Next error" | Unfolds the turn that holds the next failed span and selects it; "Error n of m" shows |
| Waterfall | Zoom ⤢ on a turn | Rescales the axis to that turn; "← Full trace" restores |
| Waterfall | Highlight errors | Fades every non-error, non-turn row to 25% |
| Sortable header | Click, Enter or Space | Sorts descending first, toggles on repeat; `aria-sort` set |
| Segmented filter (`segmented()`) | Click | Every view, KPI and count re-renders for the selection; keep the choice in localStorage inside try/catch |
| Child-row toggle | Click | Toggles; when off, child rows nest under their parent row (`rowClass`) |
| `/` | Keypress outside an input | Opens the list view and focuses search |

## Tone parity

| Primitive | ok | warn | err | heuristic/muted |
|---|---|---|---|---|
| Pill | yes | yes | yes | yes (dashed) |
| Health dot | yes (85+) | yes (70–84) | yes (<70) | grey (no tool calls) |
| Waterfall bar | kind tint | n/a (Slowest uses a warn pill) | red tint + red edge | folded turn at 55% |
| KPI value | default | `is-warn` | `is-err` | n/a |
| Heat cell | yes | yes | yes | grey |

## Motion presets (tokens in `:root`)

| Job | Token | Value | Reduced motion |
|---|---|---|---|
| hover / press | `--dur-micro` | 100ms | 0 |
| collapse | `--dur-collapse` | 150ms | 0 |
| exit (drawer) | `--dur-exit` | 160ms | 0 |
| enter (drawer, fade-in) | `--dur-enter` | 220ms, `--ease-sheet` | 0 |
| chart draw | `--dur-draw` | 550ms, `--ease-draw` | 0 |
| counters | ticker | 900ms digit roll, 45ms stagger | final value, no roll |

## Checks

`python3 -I assets/check_tokens.py <files>` flags raw colours outside the primitive block, undefined `var()`
tokens, missing reduced-motion handling and fixed transition/animation durations. `python3 -I assets/build.py
--check` regenerates nothing and fails when `components.iife.js` or the token contract in `components.css` is stale.
