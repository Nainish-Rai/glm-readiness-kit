/* Deploy tab. Part 1 is a port of glmkit/deploy/sizing.py (size(), experts_touched()). Keep the two in step:
   tests/test_deploy_sizing.py runs both on the same inputs and asserts equal outputs. Part 1 is pure (no DOM), so
   the file also loads in node; part 2 (the tab) only runs when GLMKit exists. */
(function (root) {
  "use strict";
  const UTIL_GRID = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0];
  const DEFAULTS = {
    model: "glm-5.3", precision: "fp8", kv_dtype: "fp8", gpu: "h200", n_gpus: 8, context: 131072, concurrency: 64,
    avg_input: 8192, avg_output: 1024, prefix_reuse: 0.0, api: "zai", utilisation: null, mtp_accept: 1.0,
    effort_multiplier: 1.0, dcp: 1, include_indexer: false, routing_skew: null, eta: null,
  };

  function expertsTouched(experts, topK, batch, skew) {
    const uniform = experts * (1 - Math.pow(1 - topK / experts, batch));
    return uniform * (1 - skew) + Math.min(uniform, topK) * skew;
  }

  function stepBytes(spec, bytesPerParam, touched) {
    const routedActive = spec.top_k * spec.moe_layers * spec.expert_params;
    const denseActive = spec.active_params - routedActive;
    return (denseActive + touched * spec.moe_layers * spec.expert_params) * bytesPerParam;
  }

  function size(input, payload) {
    const i = Object.assign({}, DEFAULTS);
    for (const k of Object.keys(input || {})) if (k in DEFAULTS && input[k] !== null && input[k] !== undefined) i[k] = input[k];
    const spec = payload.spec, asm = payload.assumptions;
    if (i.model !== spec.id) throw new Error("unknown model " + i.model);
    if (!(i.precision in spec.checkpoint_gb)) throw new Error("unknown precision " + i.precision);
    if (!(i.kv_dtype in payload.kv_dtype_bytes)) throw new Error("unknown kv dtype " + i.kv_dtype);
    if (!(i.gpu in payload.gpus)) throw new Error("unknown gpu " + i.gpu);
    if (!(i.api in payload.apis)) throw new Error("unknown api " + i.api);
    if (i.n_gpus < 1 || i.concurrency < 1 || i.dcp < 1) throw new Error("n_gpus, concurrency and dcp must be >= 1");
    if (!(i.prefix_reuse >= 0 && i.prefix_reuse < 1)) throw new Error("prefix_reuse must be in [0, 1)");
    if (i.context < i.avg_input + i.avg_output) throw new Error("context is smaller than avg_input + avg_output");
    const gpu = payload.gpus[i.gpu], api = payload.apis[i.api];
    const util = i.utilisation === null ? asm.default_utilisation : i.utilisation;
    if (!(util > 0 && util <= 1)) throw new Error("utilisation must be in (0, 1]");
    const eta = i.eta === null ? payload.fitted.eta : i.eta;
    const skew = i.routing_skew === null ? payload.fitted.routing_skew : i.routing_skew;

    const weightsGb = spec.checkpoint_gb[i.precision];
    const weightsPg = weightsGb / i.n_gpus;
    const hbm = gpu.hbm_gb;
    const usable = hbm * asm.gpu_memory_utilization;
    const overhead = asm.runtime_overhead_gb;
    const kvFree = usable - weightsPg - overhead;

    const kvb = payload.kv_dtype_bytes[i.kv_dtype];
    const mla = spec.layers * (spec.kv_lora_rank + spec.qk_rope_head_dim) * kvb;
    const indexer = i.include_indexer ? spec.indexer_full_layers * spec.index_head_dim : 0;
    const perToken = mla + indexer;
    const maxTokens = Math.max(0, kvFree) * 1e9 * i.dcp / perToken;
    const tokensPerSeq = i.avg_input * (1 - i.prefix_reuse) + i.avg_output;
    const maxConc = Math.floor(maxTokens / tokensPerSeq);

    const weightsFit = kvFree > 0;
    const contextFits = weightsFit && maxTokens >= i.context;
    const concFits = weightsFit && maxConc >= i.concurrency;
    const fmt = (n, d) => Number(n).toLocaleString("en-US", { maximumFractionDigits: d === undefined ? 0 : d });
    const reasons = [];
    if (!weightsFit) {
      reasons.push("weights " + fmt(weightsGb) + " GB over " + i.n_gpus + " GPUs = " + fmt(weightsPg, 1) + " GB each; only " +
        fmt(usable - overhead, 1) + " GB of " + hbm + " GB x " + asm.gpu_memory_utilization + " is usable after overhead");
    } else if (!contextFits) {
      reasons.push("KV cache holds " + fmt(maxTokens) + " tokens but context " + fmt(i.context) + " needs one full sequence; " +
        "the MLA cache is replicated on every tensor-parallel rank, so adding GPUs adds no KV capacity " +
        "(use more HBM per GPU, FP8 KV, dcp, or a shorter context)");
    }
    if (weightsFit && !concFits) {
      reasons.push("concurrency " + i.concurrency + " needs " + fmt(i.concurrency * tokensPerSeq) + " KV tokens at " +
        fmt(tokensPerSeq) + " per sequence; capacity is " + fmt(maxTokens) + " (max concurrency " + maxConc + ")");
    }
    const out = {
      inputs: i,
      weights: { total_gb: weightsGb, per_gpu_gb: weightsPg },
      memory: { hbm_gb: hbm, usable_gb: usable, weights_per_gpu_gb: weightsPg, overhead_gb: overhead, kv_free_per_gpu_gb: kvFree, headroom_gb: hbm - usable },
      kv: { bytes_per_token_mla: mla, bytes_per_token_indexer: indexer, bytes_per_token: perToken, max_tokens: maxTokens, tokens_per_seq: tokensPerSeq, max_concurrency: maxConc, full_context_seqs: maxTokens / i.context },
      feasibility: { feasible: weightsFit && contextFits, weights_fit: weightsFit, context_fits: contextFits, concurrency_fits: concFits, reasons },
      moe: null, perf: null, cost: null,
    };
    if (!weightsFit) return out;

    const batch = Math.max(1, Math.min(i.concurrency, maxConc));
    const bpp = weightsGb * 1e9 / spec.total_params;
    const touched = expertsTouched(spec.experts, spec.top_k, batch, skew);
    const uniform = expertsTouched(spec.experts, spec.top_k, batch, 0.0);
    const bytes = stepBytes(spec, bpp, touched);
    const peakBw = i.n_gpus * gpu.hbm_tb_s * 1e12;
    const flops = i.n_gpus * gpu.fp8_tflops * 1e12 * (i.precision === "bf16" ? 0.5 : 1.0);
    const tMem = bytes / (peakBw * eta);
    const tComp = 2 * spec.active_params * batch / (flops * asm.compute_eta);
    const tStep = Math.max(tMem, tComp);
    const tokS = batch * i.mtp_accept / tStep;
    const prefillTokS = flops * asm.prefill_eta / (2 * spec.active_params);
    out.moe = { batch, experts_touched_per_layer: touched, experts_touched_uniform: uniform, touched_fraction: touched / spec.experts, bytes_read_per_step_gb: bytes / 1e9, read_fraction_of_weights: bytes / (weightsGb * 1e9) };
    out.perf = { step_time_ms: tStep * 1e3, memory_bound: tMem >= tComp, tokens_per_step: i.mtp_accept, decode_tok_s: tokS, per_user_tok_s: tokS / batch, prefill_tok_s: prefillTokS, eta, routing_skew: skew };

    const outTok = i.avg_output * i.effort_multiplier;
    const inUncached = i.avg_input * (1 - i.prefix_reuse);
    const inCached = i.avg_input * i.prefix_reuse;
    const nodeUsdS = gpu.usd_hr * i.n_gpus / 3600;
    const reqS = inUncached / prefillTokS + outTok / tokS;
    const selfReq = nodeUsdS * reqS;
    const cachedPrice = api.cached_usd_m !== null ? api.cached_usd_m : api.in_usd_m;
    const apiReq = (inUncached * api.in_usd_m + inCached * cachedPrice + outTok * api.out_usd_m) / 1e6;
    const mTokens = (i.avg_input + outTok) / 1e6;
    const breakeven = selfReq / apiReq;
    out.cost = {
      node_usd_hr: gpu.usd_hr * i.n_gpus, utilisation: util, self_usd_m_full: selfReq / mTokens, self_usd_m: selfReq / util / mTokens,
      api_usd_m: apiReq / mTokens, self_usd_per_m_output: selfReq / util / (outTok / 1e6), api_usd_per_request: apiReq,
      self_usd_per_request: selfReq / util, self_vs_api: (selfReq / util) / apiReq, breakeven_utilisation: breakeven,
      breakeven_reachable: breakeven <= 1.0, curve: UTIL_GRID.map((u) => ({ utilisation: u, self_usd_m: selfReq / u / mTokens })),
    };
    return out;
  }

  const api = { size, expertsTouched, DEFAULTS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.GLMDeploySizing = api;
})(typeof globalThis !== "undefined" ? globalThis : this);

/* ---- part 2: the tab ------------------------------------------------------------------------------ */
(function () {
  "use strict";
  if (typeof GLMKit === "undefined") return;
  const S = globalThis.GLMDeploySizing;

  GLMKit.registerPage("deploy", {
    render(panel, data, kit) {
      const { O, el } = kit;
      const payload = data.payload;
      const state = Object.assign({}, S.DEFAULTS, data.defaults);
      const num = (v, d) => (v === null || v === undefined || Number.isNaN(v) ? "not logged" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d === undefined ? 0 : d }));
      const usd = (v) => (v == null ? "not logged" : "$" + Number(v).toFixed(2));
      const heur = () => O.pill("heuristic", "heur", { "data-tip": "Estimate from a roofline fitted on two published SGLang points; not a measurement of your cluster." });

      /* ---- form ---- */
      const field = (label, input) => el("label", { class: "dp-field" }, el("span", {}, label), input);
      const select = (key, options) => {
        const s = el("select", { "aria-label": key }, options.map(([v, t]) => el("option", { value: v }, t)));
        s.value = String(state[key]);
        s.addEventListener("change", () => { state[key] = s.value; update(); });
        return s;
      };
      const number = (key, attrs, parse) => {
        const i = el("input", Object.assign({ type: "number", "aria-label": key, inputmode: "decimal" }, attrs));
        i.value = state[key] === null ? "" : String(state[key]);
        i.addEventListener("input", () => {
          const raw = i.value.trim();
          state[key] = raw === "" ? (key === "utilisation" ? null : S.DEFAULTS[key]) : (parse || Number)(raw);
          update();
        });
        return i;
      };
      const gpuOpts = Object.entries(payload.gpus).map(([k, g]) => [k, g.title + " · $" + g.usd_hr.toFixed(2) + "/h"]);
      const apiOpts = Object.entries(payload.apis).map(([k, a]) => [k, a.title]);
      const precision = el("div");
      const seg = O.segmented(precision, [{ value: "fp8", label: "FP8" }, { value: "nvfp4", label: "NVFP4" }, { value: "bf16", label: "BF16" }],
        { value: state.precision, label: "Weight precision", onChange: (v) => { state.precision = v; update(); } });
      const includeIdx = el("input", { type: "checkbox", "aria-label": "include indexer" });
      includeIdx.checked = !!state.include_indexer;
      includeIdx.addEventListener("change", () => { state.include_indexer = includeIdx.checked; update(); });
      const form = el("div", { class: "dp-form" },
        field("Weights", precision),
        field("GPU", select("gpu", gpuOpts)),
        el("div", { class: "dp-row" },
          field("GPUs (tensor parallel)", number("n_gpus", { min: 1, step: 1 }, (v) => Math.max(1, Math.round(Number(v))))),
          field("KV dtype", select("kv_dtype", [["fp8", "FP8"], ["bf16", "BF16"]]))),
        el("div", { class: "dp-row" },
          field("Max context (tokens)", number("context", { min: 1024, step: 1024 })),
          field("Concurrency", number("concurrency", { min: 1, step: 1 }, (v) => Math.max(1, Math.round(Number(v)))))),
        el("div", { class: "dp-row" },
          field("Avg input tokens", number("avg_input", { min: 1, step: 512 })),
          field("Avg output tokens", number("avg_output", { min: 1, step: 64 }))),
        el("div", { class: "dp-row" },
          field("Prefix reuse (0 to 0.99)", number("prefix_reuse", { min: 0, max: 0.99, step: 0.05 })),
          field("MTP tokens per step", number("mtp_accept", { min: 1, max: 6, step: 0.05 }))),
        el("div", { class: "dp-row" },
          field("Effort token multiplier", number("effort_multiplier", { min: 0.1, step: 0.1 })),
          field("Utilisation (0 to 1)", number("utilisation", { min: 0.01, max: 1, step: 0.05 }))),
        field("Compare with API", select("api", apiOpts)),
        el("label", { class: "dp-field" }, el("span", {}, "Count the DSA indexer key cache (adds about 2.7 KB per token)"), includeIdx));
      const inputsCard = el("div", { class: "card c-4" },
        el("div", { class: "card-h" }, el("h2", {}, "Hardware and workload"), el("span", { class: "hint" }, "edit any field, everything below recomputes")),
        el("div", { class: "card-b" }, form));

      /* ---- outputs ---- */
      const memBody = el("div", { class: "card-b dp-stack" });
      const memCard = el("div", { class: "card heuristic c-8" },
        el("div", { class: "card-h" }, el("h2", {}, "Memory per GPU"), heur(), el("span", { class: "hint" }, "weights + runtime + KV; KV is replicated on every TP rank")), memBody);
      const perfBody = el("div", { class: "card-b dp-stack" });
      const perfCard = el("div", { class: "card heuristic c-6" },
        el("div", { class: "card-h" }, el("h2", {}, "Decode and MoE bandwidth"), heur(), el("span", { class: "hint" }, "touched experts set bytes per step")), perfBody);
      const costBody = el("div", { class: "card-b dp-stack" });
      const costCard = el("div", { class: "card heuristic c-6" },
        el("div", { class: "card-h" }, el("h2", {}, "Self-hosted vs API"), heur(), el("span", { class: "hint" }, "$ per 1M tokens of this request mix")), costBody);
      panel.replaceChildren(el("div", { class: "grid" }, inputsCard, memCard, perfCard, costCard, sourcesCard(), validationCard(), savedCard()));

      function update() {
        let r;
        try { r = S.size(state, payload); } catch (e) {
          const msg = el("p", { class: "dp-note", role: "alert" }, String(e.message));
          memBody.replaceChildren(msg); perfBody.replaceChildren(); costBody.replaceChildren();
          return;
        }
        const f = r.feasibility;
        const pills = el("div", { class: "dp-pills", role: "group", "aria-label": "Feasibility" },
          O.pill(f.weights_fit ? "weights fit" : "weights do not fit", f.weights_fit ? "ok" : "err"),
          O.pill(f.context_fits ? "context fits" : "context does not fit", f.context_fits ? "ok" : "err"),
          O.pill(f.concurrency_fits ? "concurrency fits" : "concurrency short", f.concurrency_fits ? "ok" : "warn"),
          O.pill(f.feasible ? "feasible" : "infeasible", f.feasible ? "ok" : "err"));
        const m = r.memory;
        const parts = [
          { label: "Weights", value: m.weights_per_gpu_gb, color: "--cat-1" },
          { label: "Runtime overhead", value: m.overhead_gb, color: "--cat-3" },
          { label: "KV cache", value: Math.max(0, m.kv_free_per_gpu_gb), color: "--cat-2" },
          { label: "Headroom", value: m.headroom_gb, color: "--surface-overlay" },
        ];
        const fmtGb = (v) => num(v, 1) + " GB";
        const kvRows = [
          ["KV per token", num(r.kv.bytes_per_token) + " B (" + num(r.kv.bytes_per_token / 1000, 1) + " KB)"],
          ["KV tokens in cache", num(r.kv.max_tokens)],
          ["Max concurrency at this mix", num(r.kv.max_concurrency) + " sequences of " + num(r.kv.tokens_per_seq) + " tokens"],
          ["Full-context sequences", num(r.kv.full_context_seqs, 2)],
          ["Weights, all GPUs", fmtGb(r.weights.total_gb)],
        ];
        const reasons = f.reasons.length ? el("ul", { class: "dp-reasons" }, f.reasons.map((t, k) => el("li", { class: !f.feasible && k === 0 ? "" : "warn" }, t))) : null;
        memBody.replaceChildren(pills,
          O.compositionBar(parts, { format: fmtGb, label: "Memory per GPU", kv: kvRows }),
          reasons || el("p", { class: "dp-note" }, "All three checks pass for this configuration."));

        if (!r.perf) {
          perfBody.replaceChildren(el("p", { class: "dp-note" }, "No throughput estimate: the weights do not fit."));
          costBody.replaceChildren(el("p", { class: "dp-note" }, "No cost estimate: the weights do not fit."));
          return;
        }
        perfBody.replaceChildren(
          O.kvList([
            ["Batch used", num(r.moe.batch) + (r.moe.batch < state.concurrency ? " (capped by KV capacity)" : "")],
            ["Experts touched per layer", num(r.moe.experts_touched_per_layer, 1) + " of " + payload.spec.experts + " (" + num(r.moe.touched_fraction * 100, 0) + "%)"],
            ["Uniform-routing upper bound", num(r.moe.experts_touched_uniform, 1) + " experts"],
            ["Bytes read per step", num(r.moe.bytes_read_per_step_gb, 0) + " GB (" + num(r.moe.read_fraction_of_weights * 100, 0) + "% of weights)"],
            ["Step time", num(r.perf.step_time_ms, 1) + " ms, " + (r.perf.memory_bound ? "memory bound" : "compute bound")],
            ["Decode throughput, node", num(r.perf.decode_tok_s, 0) + " tok/s"],
            ["Per user", num(r.perf.per_user_tok_s, 1) + " tok/s"],
            ["Prefill, node", num(r.perf.prefill_tok_s, 0) + " tok/s"],
          ]),
          el("p", { class: "dp-note" }, "HBM efficiency " + num(r.perf.eta * 100, 1) + "% and routing skew " + num(r.perf.routing_skew, 2) +
            " are fitted on the two SGLang calibration points below. Active parameters do not shrink VRAM, and at batch 64 or more most of the weights are read every step."));

        const c = r.cost;
        const rows = c.curve.map((p) => ({
          label: Math.round(p.utilisation * 100) + "% utilised", value: p.self_usd_m, mono: true,
          pill: p.self_usd_m <= c.api_usd_m ? O.pill("cheaper", "ok") : O.pill("dearer", "warn"),
          tone: p.self_usd_m <= c.api_usd_m ? "ok" : undefined,
          tip: "Self-hosted " + usd(p.self_usd_m) + " per 1M tokens at " + Math.round(p.utilisation * 100) + "% utilisation\nAPI " + usd(c.api_usd_m),
        }));
        rows.unshift({ label: payload.apis[state.api].title + " (API)", value: c.api_usd_m, color: "--cat-4", mono: true,
          tip: "API list price for the same mix: " + usd(c.api_usd_m) + " per 1M tokens" });
        const max = Math.max(...rows.map((x) => x.value));
        costBody.replaceChildren(
          el("p", { class: "dp-break" }, c.breakeven_reachable
            ? "Break-even at " + num(c.breakeven_utilisation * 100, 0) + "% utilisation. Above that, " + num(c.node_usd_hr, 2) + " $/h of GPUs beats the API."
            : "Never breaks even at this configuration: even at 100% utilisation self-hosting costs " + usd(c.self_usd_m_full) + " vs " + usd(c.api_usd_m) + " per 1M tokens."),
          O.barList(rows, { format: usd, max, empty: "No cost curve." }),
          O.kvList([
            ["Node cost", usd(c.node_usd_hr) + " per hour"],
            ["At your utilisation (" + num(c.utilisation * 100, 0) + "%)", usd(c.self_usd_m) + " vs API " + usd(c.api_usd_m) + " per 1M tokens"],
            ["Per output token", usd(c.self_usd_per_m_output) + " per 1M output"],
          ]));
      }

      /* ---- static cards ---- */
      function link(url) { return el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, url); }
      function sourcesCard() {
        const rows = [];
        Object.entries(payload.gpus).forEach(([k, g]) => rows.push({ kind: "GPU price", name: g.title, value: "$" + g.usd_hr.toFixed(2) + "/GPU-h", url: g.price_source.url, date: g.price_source.date, id: k + "-p" }));
        Object.entries(payload.gpus).forEach(([k, g]) => rows.push({ kind: "GPU spec", name: g.title, value: g.hbm_gb + " GB, " + g.hbm_tb_s + " TB/s", url: g.spec_source.url, date: g.spec_source.date, id: k + "-s", unverified: g.spec_source.verified === false }));
        Object.entries(payload.apis).forEach(([k, a]) => rows.push({ kind: "API price", name: a.title, value: "$" + a.in_usd_m + " in / $" + a.out_usd_m + " out", url: a.source.url, date: a.source.date, id: k + "-a" }));
        Object.entries(data.calibration).forEach(([k, c]) => rows.push({ kind: "Calibration", name: c.label, value: c.out_tok_s ? num(c.out_tok_s, 1) + " out tok/s" : c.kv_tokens ? num(c.kv_tokens) + " KV tokens" : "", url: c.url, date: c.date, id: k + "-c" }));
        const table = el("table");
        O.sortableTable(table, [
          { key: "kind", label: "Kind", render: (r) => O.pill(r.kind, r.kind === "Calibration" ? "heur" : null) },
          { key: "name", label: "Item", render: (r) => el("span", {}, r.name, r.unverified ? O.pill("from memory", "warn") : null) },
          { key: "value", label: "Value", render: (r) => el("span", { class: "num" }, r.value) },
          { key: "date", label: "Read on", render: (r) => el("span", { class: "num" }, r.date) },
          { key: "url", label: "Source", render: (r) => el("span", { class: "dp-src" }, link(r.url)) },
        ], rows, { rowKey: (r) => r.id, sort: { key: "kind", dir: 1 }, label: "Sources", empty: "No sources." });
        return el("div", { class: "card c-12" },
          el("div", { class: "card-h" }, el("h2", {}, "Prices and calibration points"), el("span", { class: "hint" }, "every row has a source URL and the date it was read; provider prices change weekly")),
          el("div", { class: "tbl-wrap" }, table));
      }
      function validationCard() {
        const rows = data.validation.map((v) => ({ id: v.name, v }));
        return el("div", { class: "card c-6" },
          el("div", { class: "card-h" }, el("h2", {}, "Published figures"), el("span", { class: "hint" }, "recomputed from the model config and file listing")),
          el("div", { class: "card-b dp-stack" }, rows.map(({ v }) => el("div", {},
            el("div", { class: "dp-pills" }, O.pill(v.ok ? "pass" : "fail", v.ok ? "ok" : "err"), el("span", { class: "mono" }, v.name)),
            O.kvList([["computed", num(v.computed, v.computed > 1000 ? 0 : 1)], ["reference", num(v.reference, v.reference > 1000 ? 0 : 1)],
              ["error", num(v.error * 100, 3) + "% (tolerance " + num(v.tolerance * 100, 1) + "%)"], ["formula", v.formula], ["source", v.source]])))));
      }
      function savedCard() {
        const body = el("div", { class: "card-b" });
        if (!data.saved.length) body.append(el("p", { class: "dp-note" }, "No saved runs. Run `glmkit deploy size ...` and rebuild the report."));
        else {
          const table = el("table");
          O.sortableTable(table, [
            { key: "started_at", label: "Started", render: (r) => el("span", { class: "num" }, kit.fmtTime(r.started_at)) },
            { key: "gpu", label: "Config", render: (r) => el("span", { class: "mono" }, r.inputs ? r.inputs.n_gpus + "x " + r.inputs.gpu + " " + r.inputs.precision + " ctx " + r.inputs.context : "not logged"), value: (r) => (r.inputs ? r.inputs.gpu : "") },
            { key: "feasible", label: "Feasible", render: (r) => O.pill(r.feasible ? "feasible" : "infeasible", r.feasible ? "ok" : "err") },
            { key: "self_usd_m", label: "Self $/M", align: "r", render: (r) => el("span", { class: "num" }, usd(r.self_usd_m)) },
            { key: "api_usd_m", label: "API $/M", align: "r", render: (r) => el("span", { class: "num" }, usd(r.api_usd_m)) },
          ], data.saved, { sort: { key: "started_at", dir: -1 }, label: "Saved sizing runs", empty: "none" });
          body.append(el("div", { class: "tbl-wrap" }, table));
        }
        return el("div", { class: "card c-6" }, el("div", { class: "card-h" }, el("h2", {}, "Saved sizing runs"), el("span", { class: "hint" }, "from glmkit deploy size")), body);
      }
      update();
    },
  });
})();
