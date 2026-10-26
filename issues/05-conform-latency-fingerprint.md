# 05 · Conformance: latency + silent-downgrade fingerprinting

Status: done · Type: AFK · Label: ready-for-agent · Branch: issue/05-conform-latency-fingerprint

## What to build

The host-quality part of the verifier. It answers "is this host fast, and is it really serving the model it claims at
full quality?" Users report silent downgrades and quantized hosts. Artificial Analysis shows 57–694 tok/s
across 26 hosts.

- Cases LAT-001/002 and FP-001 … FP-005 from `research/01-conformance.md` §4–§5:
  - TTFT with vs without `tools` (GLM-5#144's flat ~3 s);
  - TTFT, tok/s and completion tok/s per effort level;
  - response `model` field honesty;
  - action-distribution fingerprint, AgentProv-style: tool-choice histograms over a fixed probe set, compared to
    a stored reference with an MMD permutation test;
  - text fingerprint, MET-style;
  - greedy self-agreement noise floor;
  - capability canaries (BFCL slice, needle at 128k, small math slice) reported as % of reference with a 95% CI.
- A `glmkit conform reference record --target <profile>` command that stores a reference fingerprint (from the
  official API or a self-hosted FP8 deployment) and a `--reference` flag that compares any host against it.
- Latency and fingerprint results never affect the correctness verdict. They produce a separate "host quality"
  verdict with explicit statistical gates.
- Mock defects:
  - tool-induced TTFT penalty;
  - "quantized" mode (perturbed tool choice and lower canary accuracy);
  - wrong `model` field.
- Dashboard: a **Hosts** tab with latency range bars per host (log scale), tok/s bar list, a fingerprint verdict
  with p-value, and canary score cards. Estimated values use the heuristic style.

## Acceptance criteria

- [x] Reference record/compare works end to end on the mock: clean mock vs clean reference → no flag; "quantized"
      mock → flagged with p < 0.01 (evidence included, with the seed).
- [x] Probe sets and reference files are versioned data; changing them changes the reference id.
- [x] Latency results report medians and p90 over a configurable N; throttled (429) calls are excluded from
      latency and counted separately.
- [x] Hosts tab renders the mock run for at least 3 mock hosts; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence

Everything below ran on the in-process mock (`--target mock`), fixed seed 20261026 (stored in each check's
`observed`). Paths shortened. Reference recorded once (`FP-002..005`, 6 samples per prompt), then every host
compared against it.

### Reference record / compare (acceptance 1 and 3)

```
$ uv run glmkit conform reference record --target mock --label mock-reference --out refs/mock-reference.json
reference f89dfee1a027911a (mock-reference) -> refs/mock-reference.json
  action samples 240, text samples 120, self-agreement 1.0, canaries 24/24
  probe sets {'action': '5f7b55d27b77', 'text': '7b7cabb70e6c', 'canaries': '24a6af8cc749'}

$ uv run glmkit conform host --target mock --reference refs/mock-reference.json --latency-n 6 --label clean
  PASS  LAT-001  soft   largest median TTFT penalty with tools 1 ms (limit 1 s)
  PASS  LAT-002  soft   recorded low: ttft 2 ms, high: ttft 2 ms, max: ttft 2 ms
  PASS  FP-001   hard   response model matches the request; unknown model refused
  PASS  FP-002   hard   action distribution consistent with the reference: MMD^2=0.0000, permutation p=1.0000 (1999 perms, seed 20261026, n=240 vs 240)
  PASS  FP-003   hard   text distribution consistent with the reference: MMD^2=-0.0000, permutation p=1.0000 (1999 perms, seed 20261026, n=120 vs 120)
  PASS  FP-004   soft   self-agreement 1.000 vs reference 1.000 (margin 0.1)
  PASS  FP-005   hard   100% of reference (95% CI 92% to 108%), 24/24 correct
host-quality verdict: pass  (every evaluated gate passed)

$ uv run glmkit conform host --target mock --reference refs/mock-reference.json --latency-n 6 --label quantized-host -D quantized
  PASS  LAT-001  soft   largest median TTFT penalty with tools 1 ms (limit 1 s)
  PASS  FP-001   hard   response model matches the request; unknown model refused
  FLAG  FP-002   hard   action distribution differs from the reference: MMD^2=0.0046, permutation p=0.0005 (1999 perms, seed 20261026, n=240 vs 240)
  FLAG  FP-003   hard   text distribution differs from the reference: MMD^2=0.0014, permutation p=0.0005 (1999 perms, seed 20261026, n=120 vs 120)
  FLAG  FP-004   soft   greedy self-agreement dropped: self-agreement 0.562 vs reference 1.000 (margin 0.1)
  FLAG  FP-005   hard   canary score below reference: 62% of reference (95% CI 45% to 86%), 15/24 correct
host-quality verdict: fail  (fingerprint or identity gate flagged: FP-002, FP-003, FP-005)   [exit code 1]
```

p = 0.0005 is the floor of a 1999-permutation test (1/2000). The clean mock's p = 1.0 is because the clean mock is
deterministic; the false-positive rate on genuinely random same-distribution data is covered by
`tests/test_hostq.py::test_mmd_same_distribution_rarely_rejects`.

### Defects (check fails with, passes without)

```
$ uv run glmkit conform host --target mock --reference refs/mock-reference.json --latency-n 6 --label slow-tools-host -D tool-ttft-penalty
  FLAG  LAT-001  soft   TTFT penalty with tools above 1 s: tools_1 +1502 ms (p=0.008); tools_60 +1505 ms (p=0.006)
host-quality verdict: warn  (latency gate flagged: LAT-001)        # clean run above: LAT-001 PASS, penalty 1 ms

$ uv run glmkit conform host --target mock --reference refs/mock-reference.json --label wrong-model -D wrong-model-field --cases FP-001
  FLAG  FP-001   hard   asked for 'glm-5.3', response model is 'glm-4.7'; unknown model got HTTP 200 (answered by 'glm-4.7')
host-quality verdict: fail  (fingerprint or identity gate flagged: FP-001)   # clean run above: FP-001 PASS

$ uv run glmkit conform host --target mock --latency-n 6 --label no-ref --cases FP-002,FP-005
  N/A   FP-002   hard   no reference loaded: 240 action samples recorded, nothing to compare
  N/A   FP-005   hard   no reference: 24/24 canaries correct (absolute only)
host-quality verdict: incomplete  (no reference loaded: fingerprint gates not evaluated ...)
```

Correctness verdict is untouched (the LAT/FP cases are `opt_in`, the host run writes component `hostq`):

```
$ uv run glmkit conform run --target mock
verdict: pass  (3 passed, 0 failed, 0 soft, 0 skipped, 0 errors)
```

Each check also has a regression test (pass and fail) in `tests/test_hostq.py`; throttle exclusion
(`test_throttled_calls_are_excluded_from_latency_and_counted`) and versioned ids
(`test_probe_set_id_changes_with_content`, `test_reference_id_tracks_probe_sets_and_content`) too.

### Dashboard

```
$ uv run glmkit report build results/ -o site/        # with `.gate .gid { color: #ff0000; }` planted in hosts.css
site2/index.html:79 raw-color #ff0000
token check: 1 finding(s)                              # exit 1
$ uv run glmkit report build results/ -o site/        # planted colour removed
built site2/index.html  tabs=runs,conformance,hosts  runs=5
token check: 0 finding(s)                              # exit 0

$ CHROME_PATH=".../Google Chrome" NODE_PATH=~/node_modules node tools/site-smoke/smoke.mjs site/index.html --out shots/
ok: true; 1440 and 390 px: errors [], tabs runs/conformance/hosts visible, overflowX 0, hosts drawer opens/closes with focus back
```

Screenshots viewed (`hosts-1440.png`, `hosts-390.png`): three mock hosts (clean, quantized-host, slow-tools-host)
with TTFT log range bars, tok/s bar list, fingerprint score cards and canary cards.

### Notes

- Added dependency: `numpy` (`uv add numpy`; lockfile will need a merge-time resolve).
- Shared-file edits: none besides `pyproject.toml`/`uv.lock` (numpy). No contract changes.
- Not verified: behaviour against the real Z.ai API (issue 16); the mock is deterministic, so clean-vs-reference
  p-values are 1.0; the "Hamming kernel" compares characters, not tokens; TTFT penalty in the mock is 1.5 s, not
  the ~3 s reported upstream.
