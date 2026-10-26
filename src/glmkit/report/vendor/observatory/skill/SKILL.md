---
name: observatory-ui
description: Build dark, dense data dashboards in the Observatory style (Langfuse-like traces, waterfalls, KPI strips, treemaps, error explorers) from a ready token layer, component library and app-shell layout. Use for "build a dashboard", "observability UI", "trace/waterfall view", "make it look like the observatory", or restyling a data-heavy page in this look.
---

# Observatory UI

A dependency-free design system: `assets/` (a link to the repo's `design-system/`) holds `tokens.css`,
`components.css`, `components.js` (ES module) / `components.iife.js` (`window.Observatory`, works from
`file://`), `layout.html` (the full app shell) and `gallery.html` (every component with sample data).

Read `references/style.md` before designing a screen and `references/components.md` before picking a
component. `assets/README.md` has every function signature and data shape.

## Steps

1. **Shape the data.** List the questions the dashboard answers and the fields each needs. A field the data
   lacks is shown as `not logged`; never fill it with a made-up value.
2. **Start from the layout.** Copy `assets/layout.html`, `tokens.css`, `components.css` and the JS build into
   the target. Keep the `<!-- region: … -->` blocks the screen needs and delete the rest.
3. **Map each question to a component** with the table below, one canonical component per job. A job no
   component covers gets a new component in the same file style: token references only, tone parity, a
   reduced-motion path, a behaviour contract row in `components.md`.
4. **Fill and wire.** Feed real data through the component functions; keep the keyboard contract (`/`, Enter,
   Esc with focus return, ↑↓←→, `E`).
5. **Check.** Done when all of these hold:
   - `python3 -I assets/check_tokens.py <your files>` → `0 finding(s)` (or the repo's own lint), after you
     have seen it fail once on a planted raw colour.
   - The page renders with no console errors at 1440 and 390 px, with no horizontal page scroll at 390.
   - Under `prefers-reduced-motion: reduce` numbers show their final values and nothing animates.
   - Estimated values carry the dashed `heuristic` styling.

## Question → component

| Question | Component |
|---|---|
| The 4–6 numbers that matter | KPI strip: ticker + sparkline + foot line |
| Where did the total go? | Ordered treemap |
| Ranked share by name | Bar list |
| Composition over time | Stacked bar chart |
| One total split into parts | Composition bar |
| Many rows to search, filter, sort | Data table + chips + split bar + health dot |
| What happened inside one run, in order | Drawer: stat strip + waterfall + detail panel |
| Typical vs worst duration per item | Latency range bars (log scale) |
| What failed, grouped | Error group accordion + per-day mini bars |
| Every item at a glance | Heat strip |
| An estimated score | Score card with ring, dashed `heuristic` pill |
| Distribution | Histogram |
