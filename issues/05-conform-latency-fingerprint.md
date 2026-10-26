# 05 · Conformance: latency + silent-downgrade fingerprinting

Status: in-progress · Type: AFK · Label: ready-for-agent · Branch: issue/05-conform-latency-fingerprint

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

- [ ] Reference record/compare works end to end on the mock: clean mock vs clean reference → no flag; "quantized"
      mock → flagged with p < 0.01 (evidence included, with the seed).
- [ ] Probe sets and reference files are versioned data; changing them changes the reference id.
- [ ] Latency results report medians and p90 over a configurable N; throttled (429) calls are excluded from
      latency and counted separately.
- [ ] Hosts tab renders the mock run for at least 3 mock hosts; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence
