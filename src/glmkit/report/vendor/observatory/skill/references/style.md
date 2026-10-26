# Style: the Observatory look

What makes a screen read as Observatory. Values live in `tokens.css`; this file is the reasoning behind them.

## Surfaces

- Dark only (`color-scheme: dark`). Five surface steps: chrome (sticky bar, darkest) → canvas → card →
  raised (tracks, hover) → overlay (pressed segment, menus). A well is the darkest step, inset.
- Hierarchy comes from surface steps and a 1px `--edge` hairline. Shadows only on things that float
  (`--shadow-pop`: tooltip, drawer).
- One accent (indigo) for focus, selection and the primary series. Status colours mean status only.

## Type

- Geist for words, Geist Mono for every number, id and code (`.num` / `.mono`: tabular, slashed zero).
- Scale: 11 meta · 12 numbers · 13 body · 14 panel titles · 16 page title · 28 KPI value.
- Four text emphasis steps: `--fg-emph` (values, titles), `--fg-body`, `--fg-meta` (labels, hints),
  `--fg-faint` (axes, counts in passing).
- Sentence case everywhere. Panel titles are 2–3 words plus a faint hint that says how to read the panel
  ("bar to p50, lighter to p95, tick at p95 · log scale").

## Colour by meaning

- **Tone**: `ok` / `warn` / `err`, each with a 14% tint for fills. Every status-bearing primitive supports
  all three plus a muted state (tone parity table in `components.md`).
- **Kind**: one categorical hue per span kind (turn, llm, shell, read, edit, agent, web, mcp, other).
  Bars use the kind tint with a solid inset edge; failure overrides kind with solid red and a ✕.
- **Source**: one hue per data source (`--tool-*`), shown as a 7px dot before its name.
- **Inferred values** (scores, classifications) get a dashed border and a `heuristic` pill, so measured and
  estimated numbers never look alike.

## Density

- 4px spacing grid; 12–16px card padding; table rows ~34px; bar-list rows ~30px.
- Cards: `--radius-3` (10px); controls and bars `--radius-2` (6px); pills fully round.
- Grid: 12 columns of cards (`.c-8` + `.c-4`, `.c-7` + `.c-5`, `.c-6` pairs), 20px gaps; every card spans 12
  under 1100px. At 390px: 16px gutters and no horizontal page scroll (`main > * { min-width: 0 }`); only tables
  scroll inside their card.

## Charts

- Direct labels over legends where there is room; a legend only when colour encodes more than 3 things.
- Ordered treemap for "where did it go" (largest top-left), range bars on a log scale for latency, stacked
  bars for composition over time, a heat strip for "every item at a glance".
- Every mark has a `data-tip` tooltip with the exact value and its unit. Missing data is written
  `not logged`, never drawn as zero.

## Motion

- Numbers roll in digit by digit (900ms, 45ms stagger); charts draw once on first view (550ms); drawers
  slide on `--ease-sheet` (220ms in, 160ms out); hovers 100ms.
- Motion runs on first render and on filter changes only, never in a loop. Under
  `prefers-reduced-motion: reduce` every duration is 0 and numbers appear at their final value.

## Keyboard

- `/` focuses search; Enter opens a row; Esc closes and returns focus to the opener; ↑↓ move, ←→ fold and
  unfold, `E` jumps to the next error. Every control has a visible `:focus-visible` ring in the accent.
