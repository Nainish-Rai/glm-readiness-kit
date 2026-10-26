# Component 4 research: deployment evidence pack (deployment + compliance)

Research date: 2026-10-26. All URLs were fetched or searched on that date unless a page date is given.
Framing rule for the kit: everything below feeds a **checklist and evidence index, not legal advice**. Law-firm and vendor pages are cited as secondary sources; where I could only see a search-engine summary or an aggregator, I say so ("unverified").

Evidence labels used: [P] primary source read in full or in the relevant part; [S] secondary (law firm, vendor, aggregator); [U] unverified or contradictory.

---

## 0. Corrections to the working assumptions (read first)

| Assumption in the brief | What the sources say |
|---|---|
| "MIT weights" | The HF `config`/card says `license: other`, `license_name: glm-5.3`. The LICENSE file is MIT-style (use, modify, fine-tune, sublicense, sell) but adds (1) "use of the Software must comply with applicable laws and regulations" and (2) a **Model-as-a-Service clause: a licensee (with affiliates) running a MaaS business with >US$10B revenue over 12 months must pass a Z.AI security review before commercial use**. Contact glmlicense@z.ai. [P] https://huggingface.co/zai-org/GLM-5.3/raw/main/LICENSE (read 2026-10-26). Press and law-firm pieces say "MIT" for GLM-5.2 ([S] natlawreview below). **Kit action:** record the license as "MIT-style custom license, glm-5.3" and add the MaaS clause to the checklist; do not print "MIT" in generated packs. Also open question for AI Act Art. 53(2) open-source exemption eligibility (section 5). |
| GLM-5.3 "~753B / 40B active" | AWS model card: 753B total (744B excluding the MTP layer), ~40B active. [P] https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-zai-glm-5-3.html. HF safetensors total = 753,329,940,480 params; vLLM recipe says "~743B (39B active)". [P] https://huggingface.co/api/models/zai-org/GLM-5.3 ; https://recipes.vllm.ai/zai-org/GLM-5.3 (page dated 2026-08-28). All three numbers are the same model counted differently; the calculator should carry `total_params`, `params_excl_mtp`, `active_params` as separate fields. |
| Bedrock "US/Global only?" | Confirmed: **cross-Region only, US geo profile and Global profile; no EU geo profile, no in-Region**. See section 3. |
| Mistral regional inference for GLM | Mistral hosts GLM-5.3 (GA, model card dated 2026-09-15) and has EU/US regional endpoints, but **I could not confirm GLM-5.3 is served on the EU endpoint** (the regional page says "regional endpoints only serve models hosted in that region; check `models.list`"). See section 3. |
| "Z.ai on the GPAI Code of Practice" | Not on the Commission's list as of its 7 Oct 2026 update (22 signatories). See section 5. |

---

## 1. Self-hosting GLM-5.x

### 1.1 Model facts needed for sizing (from config.json, [P] https://huggingface.co/zai-org/GLM-5.3/raw/main/config.json, last modified 2026-10-14)

- Architecture `GlmMoeDsaForCausalLM` (`glm_moe_dsa`): MoE + MLA-style latent attention + DeepSeek Sparse Attention (DSA) indexer.
- 78 layers (first 3 dense, 75 sparse MoE), hidden 6144, 256 routed experts, top-8, 1 shared expert, `moe_intermediate_size` 2048, 64 heads, `kv_lora_rank` 512, `qk_rope_head_dim` 64, 1 nextn (MTP) layer, max positions 1,048,576, DSA `index_topk` 2048, indexer `index_head_dim` 128 with 21 "full" and 57 "shared" indexer layers.
- Checkpoints: `zai-org/GLM-5.3` = native FP8 e4m3 block-128 quant, repo files sum to **755.7 GB** (my sum of HF `siblings` sizes); `zai-org/GLM-5.3-BF16` = **1.507 TB** (HF `usedStorage`). NVFP4 variants: `Inferact/GLM-5.3-NVFP4` (~465 GB per vLLM recipe) and `RadixArk/GLM-5.3-NVFP4` (SGLang recipe). [P] vLLM recipe https://recipes.vllm.ai/zai-org/GLM-5.3 ; SGLang https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3.md
- GLM-5.3-Flash (`Glm5NextForConditionalGeneration`): 45 layers, hidden 4096, 288 routed experts top-8, multimodal, safetensors total 321.3 GB (HF API 2026-10-15). The config shows `qk_rope_head_dim: 0` and a `k_conv1d` module, so it is **not** a plain MLA layout; the KV formula in 2.1 must not be reused for Flash without reading its modeling code. [P] https://huggingface.co/zai-org/GLM-5.3-Flash . vLLM needs a special image (`vllm/vllm-openai:glm53-flash`) per HF discussions and a Dynamo issue about that tag being overwritten ([S] https://github.com/ai-dynamo/dynamo/issues/14573, opened 2026-09-10) -> pin by digest.

### 1.2 vLLM recipe (GLM-5.3) [P] https://recipes.vllm.ai/zai-org/GLM-5.3 (published 2026-08-28)

- Needs vLLM >= 0.28.0, transformers >= 5.15.0, DeepGEMM installed for FP8 performance.
- Hardware: single node 8xH200 or 8xH20 (141 GB each) for FP8; 8xB200 (180 GB) for full 1M context with FP8 KV; NVFP4 on B200/B300; BF16 needs multi-node. AMD MI300X/MI325X/MI350X/MI355X TP8 also documented (MI325X: 524,288 ctx, 1,477,168-token KV cache, 2.82x max concurrency; MTP mean acceptance length 2.77 on MI325X, 3.19 on MI350X).
- Reference command (8xH200, FP8):

```
vllm serve zai-org/GLM-5.3 \
  --kv-cache-dtype fp8 \
  --tensor-parallel-size 8 \
  --speculative-config.method mtp \
  --speculative-config.num_speculative_tokens 5 \
  --tool-call-parser glm47 \
  --reasoning-parser glm47 \
  --enable-auto-tool-choice \
  --served-model-name glm-5.3
```

- 1M-context on 8xB200: add `--max-num-seqs 32` (start there; "the single knob for fitting 1M context").
- NVFP4: `--tensor-parallel-size 8 --enable-expert-parallel --kv-cache-dtype fp8_e4m3` plus the same parsers.
- Optional DSpark external drafter (`RedHatAI/GLM-5.3-speculator.dspark`, 8 speculative tokens).
- Reasoning: thinking always on; `reasoning_effort` low/high/max via `chat_template_kwargs`, default **max** (highest token cost). Bedrock card lists low, medium, high, xhigh, max. -> cost calculator must take an effort-level token multiplier.
- Recipe warns synthetic benchmarks under-report MTP benefit (acceptance low on random data).
- Older GLM-5 recipe (744B, 2026-02-09) is BF16/FP8 and is superseded; https://recipes.vllm.ai/zai-org/GLM-5 .

### 1.3 SGLang (GLM-5.3 page, [P] https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3.md; GLM-5 page [P] https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5)

- Hardware list: H200, B200, B300, GB300 (4-GPU single node TP4), AMD MI300X/MI325X/MI355X, Ascend NPU. GLM-5 page table: FP8 needs H100 TP16 (2 nodes), H200 TP8, B200 TP8; BF16 is 2x the GPUs. BF16 (~1.5 TB) fits one node only on 8xB300 / MI325X / MI355X.
- MTP via EAGLE: `--speculative-algorithm EAGLE --speculative-num-steps 5 --speculative-eagle-topk 1 --speculative-num-draft-tokens 6` (low-latency) or 1-1-2 (balanced). Start `--mem-fraction-static ~0.8` on H200 TP8.
- Parsers: SGLang resolves `--tool-call-parser auto` -> **glm47**; `--reasoning-parser auto` -> **glm45** for GLM-5.3. **Discrepancy:** vLLM recipe uses `--reasoning-parser glm47`; SGLang and the llm-d guide use `glm45`. Kit must pin parser per engine and include a tool-call + reasoning-separation smoke test in the conformance suite (llm-d issue #2640, 2026-10-01, says most llm-d manifests omit parser flags so tool calls are never emitted: https://github.com/llm-d/llm-d/issues/2640).
- Prompt caching gotcha: GLM-5.3's chat template renders `tools` before `system`, so any per-request varying system header kills radix prefix cache (SGLang page, Claude Code section).
- DP-attention + DeepEP for throughput; prefill CP (`--enable-prefill-cp --cp-strategy interleave --attn-dp-size 1`); IndexCache override is accuracy-lossy, "not recommended for rigorous accuracy evaluations" (GLM-5 page) -> exclude from any evidence-grade configuration.
- Measured numbers on the SGLang GLM-5 page: **GLM-5-FP8, 8xH200, TP8, SGLang commit 947927bdb**. Random 1000/1000: concurrency 1 -> 117.96 output tok/s (peak 148); concurrency 100 -> 1,214.97 output tok/s, 2,435 total tok/s, 2.43 req/s. This is GLM-5 not 5.3, synthetic data, and the page does not say whether MTP was on for the throughput run. Use as a smoke-test floor only.

### 1.4 Best measured numbers (production-like), llm-d blog [P] https://llm-d.ai/blog/serving-glm-5-2-agentic-workloads-on-llm-d (2026-07-22)

GLM-5.2-FP8 (same architecture as 5.3; vLLM recipe says serving flags identical), 64xH200 on CoreWeave InfiniBand, P/D disaggregated wide-EP (DEP16 prefill and decode pools), agentic traces (median ~195K input / ~317 output tokens, 96% of turns reuse >=90% of prefix):

- Output 1,167 tok/s (c16) -> 6,281 tok/s (c128); c128: TTFT p50 2.18 s / p90 15.8 s, ITL p50 19.1 ms; ~98 output tok/s per GPU.
- MTP (3 draft tokens): acceptance 64-70.5%, ~2.95 tokens/step; median ITL roughly halved; at c128 5,647 vs 3,505 tok/s (offloading only).
- CPU KV offload: at c128 2,776 vs 951 tok/s baseline in a 24-GPU run; miss restore ~59 ms vs recompute of ~45K tokens.
- Authors' cost arithmetic: ~37-41 tok/s/user, **$0.05/M input and $3.55/M output at $2.71/GPU-hour** (vs Z.ai list $1.40/$4.40). Assumes sustained load; prefill rental attributed to input. Vendor-adjacent blog, treat as best case.
- Cache hit rate, not FLOPs, sets throughput for agentic loads -> the calculator needs a prefix-reuse parameter.

NVIDIA Dynamo recipe for GLM-5.3 (SGLang 0.5.18, Dynamo 1.5.1): H200 aggregated TP8/EP8, 250K ctx; H200 disagg 8+8 GPUs; B200 NVFP4 4 GPUs aggregated at 500K ctx; EAGLE-style MTP draft length 3 (SpeedBench acceptance length 2.69). [P] https://github.com/ai-dynamo/dynamo/tree/main/recipes/glm-5.3

### 1.5 Kubernetes stacks compared (repo stats via `gh api` 2026-10-26)

| Stack | Latest release / activity | License | GLM-5.x assets | Fit as reference base |
|---|---|---|---|---|
| **llm-d** (llm-d/llm-d) | v1.0.0 (2026-10-20), 5.0k stars, pushed today | Apache-2.0 | `guides/models/glm-5-2` (H200 + TPU v7x, vLLM and SGLang, P/D wide-EP, tiered KV, MTP, glm45/glm47 parsers); open perf issues for GLM-5.2 WideEP (#2826, #2923) | **Best base for the K8s reference.** Helm/Kustomize guides, well-lit "foundations", CNCF-aligned, engine-agnostic, KServe LLMInferenceService builds on it. Weakness: 64-GPU IB reference is heavy; need a "functional 2-node `p1w1d1w1`" profile for EU clouds without IB; no GLM-5.3 guide yet (5.2 only). |
| **NVIDIA Dynamo** | v1.6.0 (2026-10-22), 8.4k stars | Apache-2.0 per file headers; GitHub shows NOASSERTION | `recipes/glm-5.3`, `glm-5.3-flash`, `glm-5-nvfp4` (SGLang-based) | Strongest GLM-5.3-specific recipes and NVFP4/B200, but NVIDIA-hardware-centric and open GLM tool-call issue (#16237). Good as an "alternate profile". |
| **vllm-project/production-stack** | helm chart 0.1.13 (2026-09-29), 2.7k stars | Apache-2.0 | none for GLM; issue #1059 asks for hybrid-attention model support in LMCache MP mode | Simple Helm chart (router + engine), easiest to read, but multi-node 753B and P/D are not its focus. Good for the "single 8xH200 node" profile. |
| **KServe** | v0.21.0 (2026-09-25), 6.2k stars | Apache-2.0 | `LLMInferenceService` v1alpha2 present; no GLM sample found | Useful as the enterprise API layer (auth, canary); not an inference-perf stack by itself. |
| **AIBrix** | v0.8.0 (2026-10-13), 5.2k stars | Apache-2.0 | none found | KV-cache and autoscaling features; no GLM evidence. Skip for v1. |
| **SkyPilot** | v0.14.1 (2026-10-19), 10.8k stars | Apache-2.0 | none found in repo code search | Good for provisioning GPUs across Nebius/Scaleway/Crusoe etc. (Terraform alternative), not a serving stack. |
| SGLang OME | v1.2.2 (2026-08-05), 520 stars | Apache-2.0 | n/a | Too small. |

**Recommendation:** two profiles.
1. `single-node`: vLLM 0.28.0 (or SGLang) on 1x8 H200 (or 8x B200 for 1M ctx), packaged as plain Helm (production-stack chart or a minimal Deployment + Service + PVC), FP8, MTP on. This is what most FDE engagements need.
2. `cluster`: llm-d v1.0.0 (guide `glm-5-2` adapted to 5.3) with vLLM, P/D + wide-EP, for >=16 GPUs with RDMA. Terraform only for the cloud layer (Nebius, Scaleway, Crusoe; SkyPilot YAML as optional shortcut).
Avoid claiming llm-d GLM-5.3 support: only a 5.2 guide exists as of today.

---

## 2. Sizing / cost: methodology and existing tools

### 2.1 MoE sizing formulas for the kit (computed from the config above; validated against llm-d's published 44.9 KB/token figure: 25 GiB per rank / 598K tokens)

Let `P_total` = 753.3e9, `P_act` ~ 40e9 (39-40B), `L`=78, `r`=kv_lora_rank 512, `d_rope`=64, `E`=256, `k`=8.

1. **Weight memory** = checkpoint bytes, not `P_total x bytes` (norms, gate, embeddings, MTP stay BF16/F32). FP8 755.7 GB; BF16 1,507 GB; NVFP4 ~465 GB. Active params do **not** reduce VRAM; all experts stay resident.
2. **KV per token per sequence (MLA)** = `L x (r + d_rope) x bytes_kv` = 78 x 576 = **44,928 B/token at FP8 (≈43.9 KiB), 89,856 B at BF16**. 1M tokens at FP8 ≈ 44.9 GB per sequence. Add DSA indexer key cache ≈ 21 full-indexer layers x 128 B ≈ 2.7 kB/token (FP8, my estimate; engine-specific, verify per engine). MTP layer adds one more layer.
3. **TP vs DP/EP replication.** vLLM with TP8 and no decode-context-parallelism **copies the MLA cache on every rank** (llm-d blog): effective capacity = per-GPU free bytes / per-token bytes, not x8. Worked: 8xH200, FP8 weights 94.5 GB/GPU, `gpu_memory_utilization` 0.9 of 141 GB = 126.9 GB -> ~32 GB/GPU left for KV + activations + CUDA graphs; llm-d measured **~598K logical tokens** at TP8, which is less than one 1M-token sequence. That explains why the vLLM recipe puts 1M context on 8xB200 (180 GB) and tells you to tune `--max-num-seqs`.
4. **Per-node concurrency** ≈ `floor(KV_tokens_available / (avg_ctx + avg_out))`, with `avg_ctx` including shared prefixes only once if prefix caching hits.
5. **Experts touched per step (the MoE bandwidth term).** Under uniform routing, unique experts per layer for a batch of B tokens ≈ `E x (1 - (1 - k/E)^B)`: B=1 -> 8 (3%), 4 -> 31 (12%), 16 -> 102 (40%), 32 -> 163 (64%), 64 -> 222 (87%), 128 -> 252 (98%). So decode bytes/step ≈ dense part + touched-experts x expert bytes; at batch >= ~64 you read nearly the whole 755 GB per step, so "active params" speed claims only hold at low batch. Real routing is skewed, so this is an upper bound; the kit should expose a `routing_skew` knob and calibrate it from measured benchmarks.
6. **Roofline decode** (batch B, TP over N GPUs): `t_step ≈ max( bytes_read / (N x HBM_BW x eta), 2 x P_act x B / (N x FLOPs x eta_c) ) + comm`, with `eta` fit from measurement. Sanity: measured 118 tok/s at batch 1 on 8xH200 (SGLang GLM-5-FP8) vs naive ceiling ~ (8 x 4.8 TB/s)/40 GB ≈ 960 tok/s (H200 bandwidth is the NVIDIA spec from memory, not re-fetched) -> eta ~ 12%. MTP multiplies tokens per step by mean acceptance length (2.7-3.2 measured by vLLM on AMD, ~2.95 by llm-d, 2.69 by Dynamo) but costs verify compute.
7. **Cost per million tokens** (self-hosted) = `(GPU_hour_price x N_gpus) / (tokens_per_second x 3600) x 1e6`, computed **separately for prefill and decode GPUs** in disaggregated setups, with `tokens_per_second` measured under the SLO (e.g. >= 30 tok/s/user), and a utilisation divisor (diurnal load) plus idle headroom for HA (2 replicas). Worked examples (my arithmetic):
   - 8xH200 on-demand at Nebius list $5.40/GPU-h = $43.2/h; SGLang GLM-5 random 1k/1k at c100 = 2,435 total tok/s -> **$4.93 per M tokens (input+output blended)**; output-only attribution $9.88/M. At the API list ($1.40 in / $4.40 out) a single 8xH200 node on-demand is **not** cheaper on synthetic load; it only wins with high utilisation, cache-heavy agentic traffic, MTP, or discounted capacity (llm-d's $2.71/GPU-h gives $2.47/M blended on the same throughput).
   - llm-d 64xH200 @ $2.71 = $173.4/h at 6,281 output tok/s (c128) -> $7.67/M if all cost is assigned to output; the blog's $0.05 in/$3.55 out split assigns prefill GPUs to input.
8. **Effort multiplier:** `reasoning_effort=max` is default and costs the most output tokens; include `tokens_out(effort)` as measured per-eval, since evidence packs attach eval results.
9. **Memory-bandwidth for KV reads** grows with context but DSA top-2048 makes attention cost near-constant for long contexts (SGLang page); do not use a dense `O(ctx)` attention term.

### 2.2 Existing calculators and gaps

| Tool | What it does | MoE/MLA gaps | Source |
|---|---|---|---|
| ApX LLM VRAM & performance calculator | Weights + KV + overhead; changelog says MoE fixes for DeepSeek V3/Qwen3, non-MHA attention, KV quantization; shows an MLA formula using latent+RoPE dims | New models "may be less accurate"; no DSA indexer; no TP-replication of MLA cache; no cost or P/D; source repo not shown | [S] https://apxml.com/tools/vram-calculator |
| Vantaige LLM VRAM Calculator | Weights/KV/runtime split, MLA-aware, states active params do not reduce VRAM | Not a throughput or cost tool | [S] https://vantaige.io/blog/vantaige-launches-the-llm-vram-calculator-a-free-gpu-compatibility-finder-for-open-source-and-open-weight-ai |
| BenchLM VRAM calculator | Total params for MoE, flat 15% runtime allowance | Flat overhead hides TP-replicated MLA cache | [S] https://www.benchlm.ai/tools/llm-vram-calculator |
| Martin Alderson local-LLM speed calculator | Notes MoE speed depends on active, memory on total params | Consumer/local focus | [S] https://martinalderson.com/posts/local-llm-speed-calculator/ |
| GitHub small repos (`StartServerr/llm-vram-calculator` 7 stars, `GPUforLLM/llm-vram-calculator` 3 stars, ...) | GGUF/GQA oriented | None are maintained references for MLA/DSA | `gh search repos` 2026-10-26 |
| Artificial Analysis blended price | **7:2:1 ratio of cache-hit : input : output tokens** (formula `(7xcache + 2xinput + output)/10`); also "Cost per Task" weighted by Intelligence Index tasks | Serverless per-token only; no self-hosted TCO, no GPU-hours; the blend assumes ~70% cache hits (DigitalOcean critique, [S] https://www.digitalocean.com/community/tutorials/hidden-cost-output-token-pricing, July 2026) | [P] https://artificialanalysis.ai/methodology |
| llm-d blog cost section | GPU-hour -> $/M with prefill/decode split | One workload, one cluster | [P] link in 1.4 |
| Provider calculators (Baseten/Modal/Together) | Not researched in depth in this pass; no GLM-5.x MoE-aware calculator surfaced in searches | -- | [U] |

**Gap summary the kit can fill:** (1) config-driven (reads `config.json`) MLA/DSA-aware KV and per-rank replication; (2) experts-touched-per-batch bandwidth term; (3) TP/EP/DP and P/D topologies; (4) prefix-reuse and MTP acceptance as parameters; (5) outputs both capacity and $/M with the AA 7:2:1 blend and a user mix, side by side with API list prices; (6) reasoning-effort token multiplier; (7) calibration mode that ingests `vllm bench serve` / `sglang.bench_serving` JSON and fits `eta`.

---

## 3. Hosting options for GLM (EU and Singapore)

Prices are USD per 1M tokens (in / cached-in / out) unless stated. Aggregator prices are [S] and can lag; always re-pull from the provider.

| Option | GLM-5.x offered | Region control | Price (sourced) | Notes / evidence |
|---|---|---|---|---|
| **Z.ai API (first party)** | GLM-5.3 | China-operated; no EU region found | $1.40 / - / $4.40 ([S] aggregators citing docs.z.ai pricing, e.g. https://anotherwrapper.com/tools/llm-pricing/glm-5.3) | Entity-List issue, section 5.4. |
| **Amazon Bedrock** | GLM 5.3 (launched 2026-10-05), "limited to eligible customers, contact AWS account team" | **Cross-Region only**: geo profile `us.zai.glm-5.3` (US source Regions only); global profile `global.zai.glm-5.3` callable from 31 Regions incl. all EU Regions (Frankfurt, Ireland, Paris, Stockholm, Milan, Spain, Zurich, London) and Singapore `ap-southeast-1`; **no EU geo profile, no in-Region** | Third-party tracker: US $1.85 / $0.34 cache read / $5.81; Global $1.68 / $0.31 / $5.28 ([S] https://custom.typingmind.com/tools/estimate-llm-usage-costs/amazon-bedrock/us-zai-glm-5-3). Not confirmed on an AWS pricing page. Service tiers Standard, Priority, Flex (Reserved not supported). Output burndown 10x on quotas. | [P] https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-zai-glm-5-3.html. An EU data-residency requirement **cannot be met** with this model today; the evidence pack should auto-fail the "EU-only processing" control for Bedrock GLM 5.3. Earlier GLM 5 was unreachable in eu-north-1 per an AWS re:Post question ([S]). |
| **Mistral (La Plateforme/Studio)** | GLM 5.3 GA (card dated 2026-09-15), GLM 5.2 deprecated 2026-09-29; "served without Mistral modifications"; also GLM 5.3 Flash | EU endpoint `api.eu.mistral.ai` (EU and EFTA data centres), US endpoint, and global. **Regional endpoints only serve models hosted in that region; availability of GLM-5.3 on the EU endpoint is not stated on the pages I could read** -> verify with `models.list`. Regional limits: only function calling among tools; no Agents, Batch, Files API on regional endpoints. Control plane (billing, keys, metadata) is not regional; "limited, safeguarded transfers to sub-processors that may occur outside that region" | Pricing page: GLM 5.3 **$1.4 in / $0.14 cached / $4.4 out**; Flash $0.15 / $0.03 / $0.5; **regional = 1.1x** | [P] https://docs.mistral.ai/inference/regional-inference ; https://docs.mistral.ai/inference/pricing ; https://mistral.ai/news/regional-inference-open-models-new-compute/ (2026-08-11); https://docs.mistral.ai/models/model-cards/zai-glm-5-3 . Priority Tier in public preview with 99.5% SLA. Log hostname, model ID, request ID per call (Mistral's own audit advice) -> reuse as evidence. |
| **Nebius Token Factory** | GLM-5.3 and Flash listed by aggregators | Finland and France data centres plus US; public endpoint locations fixed, dedicated endpoints choose DC; zero-retention described as EU/US option | $1.40 / - / $4.40; Flash $0.15 / $0.50 ([S] https://www.getmaxim.ai/bifrost/model-library/compare/nebius/glm-5.3.md; may equal Z.ai list) | Legal quick guide https://docs.tokenfactory.nebius.com/legal/legal-quick-guide . GPU cloud list (live page, 2026-10-26): HGX H200 **$5.40/GPU-h on-demand** (preemptible from $0.79), HGX B200 $8.50, HGX B300 $9.50, H100 $4.50 [P] https://nebius.com/prices . Region of those prices not stated. |
| **Scaleway** (FR) | GLM-5.2/5.3 on Generative APIs per aggregators; Artificial Analysis tracks Scaleway GLM-5.2 | France (Paris) | GLM-5.3 ~$2.01 / $6.15 (computeprices, updated 2026-10-14) [S]; GLM-5.2 quotes range $1.80-$2.09 in [S] (disagree). Scaleway context listed 256K for 5.2. | GPUs (PAR-2, live page): **H100-SXM-8-80G EUR 25.33/h; B300-SXM-8-288G EUR 60/h (8 GPUs, 2.3 TB); no H200 listed** [P] https://www.scaleway.com/en/pricing/gpu/?zone=fr-par-2 . 8xB300 holds BF16 in one node (SGLang page) or FP8 with large KV. |
| **OVHcloud AI Endpoints** | No GLM found in third-party catalogue lists (Qwen3.5-397B, Llama 3.3, Mistral Small, gpt-oss) | EU | n/a | Live catalogue is the authority: https://endpoints.ai.cloud.ovh.net/catalog -> [U]. I could not extract GPU prices from OVH's price page. |
| **IONOS AI Model Hub** | No evidence of GLM; hub described as German-hosted Llama/Mistral/Teuken | Germany | n/a | [S] https://www.channelpartner.de/article/3904134/ionos-integriert-weitere-open-source-ki-modelle.html ; [U] for 2026. |
| **Hetzner GPU** | Cannot host the full model: GEX line is RTX PRO 4000/6000 Blackwell, up to 4x RTX PRO 6000 Max-Q (GEX430) | DE/FI | n/a | [P] https://www.hetzner.com/dedicated-rootserver/matrix-gex/ . Flash (321 GB) or NVFP4 on 4x96 GB is borderline; untested, do not list as supported. |
| **Crusoe** | Managed Inference: GLM-5.3 $1.40 / $0.26 cached / $4.40; GLM-5.3 Flash $0.15/$0.03/$0.50; serverless fine-tuning for GLM-5.2/5.3 ($10 per 1M tokens) | **Page does not state regions/EU sites** | GPU table flattened by extraction; best reading: H200 spot $3.45, B200 on-demand $2.00 (looks inconsistent, verify), GB300 $8.96 | [P] https://crusoe.ai/cloud/pricing (read via WebFetch summarizer, medium confidence). The prior research hint "Crusoe EU" is unconfirmed here. |
| **DeepInfra** | GLM-5.3 (US-labelled in Requesty listing) | EU region **not confirmed** | $0.90 / $3.00 (own guide dated 2026-10-03) vs $1.20 / $4.00 (aggregators) vs $0.563 / $2.50 (temporary) | [S] https://deepinfra.com/blog/glm-5-3-provider-pricing-guide ; treat as non-EU unless confirmed. |
| Other EU routes seen | Melious, sference, TensorX (zero-retention, EU) via Opper; sference $1.20 / $4.20 | EU | [S] https://opper.ai/zhipu/glm-5-3 , Requesty | Unvetted small providers; require due diligence. |
| **Singapore** | AWS global profile can be *called* from ap-southeast-1 but processing is not pinned to Singapore | no in-Region | see Bedrock | Self-host options: Singtel Digital InfraCo (GPUaaS; investor deck mentions H100 pilot, GB200, H200; no GLM) [S] https://links.sgx.com/1.0.0/corporate-announcements/CMWV5JCH9XBU8DEJ/902530_Investor%20Day%202026%20-%20DICo.pdf ; Firmus, CloudRift, Fluence, Hostrunway self-listed H200 (low reliability directory https://viabandwidth.com/gpu/in/h200/sg). I found **no managed GLM-5.x endpoint pinned to Singapore**; nothing on ST Telemedia or Nebius in Singapore. [U] |

Cost lines to keep in the calculator inputs: provider list prices above, `1.1x` Mistral regional multiplier, Bedrock tier selection, GPU $/h from provider pages with date stamps.

---

## 4. Compliance research

### 4.1 EU AI Act: dates in force as of 2026-10-26

| Date | What | Source |
|---|---|---|
| 2 Feb 2025 | Art. 5 prohibitions; (Art. 4 AI literacy originally) | [S] Sidley, White & Case |
| 2 Aug 2025 | GPAI provider obligations (Chapter V, Arts. 51-56) apply | [S] https://artificialintelligenceact.eu/gpai-guidelines-overview/ ; CSA note |
| 2 Aug 2026 | Commission/AI Office enforcement powers over GPAI providers start (fines up to 3% worldwide turnover or EUR 15M under Art. 101, per Beam/CSA summaries); Art. 50 transparency applies | [S] https://labs.cloudsecurityalliance.org/research/csa-research-note-eu-ai-act-gpai-enforcement-20260803-csa-st/ ; White & Case |
| 24 Jul 2026 (published), 27 Jul 2026 (in force) | **Digital Omnibus on AI = Regulation (EU) 2026/1744**, signed 8 Jul 2026 | [P] White & Case 2026-08-04 https://www.whitecase.com/insight-alert/eu-ai-omnibus-enters-force-amending-ai-act ; [S] Sidley https://datamatters.sidley.com/2026/06/22/eu-lawmakers-reach-provisional-agreement-to-delay-key-eu-ai-act-obligations/ |
| 2 Dec 2026 | Watermarking grace end for generative systems placed on market before 2 Aug 2026; new ban on AI generating CSAM / non-consensual intimate imagery | White & Case |
| 2 Dec 2027 | Annex III stand-alone high-risk obligations (Chapter III) | White & Case |
| 2 Aug 2028 | Annex I embedded high-risk | White & Case |
| 2 Aug 2027 | Legacy GPAI models (placed before 2 Aug 2025) must comply; national sandboxes deadline moved to 2 Aug 2027 | [S] CSA note; Sidley (sandboxes) |

Omnibus changes relevant to the kit: Art. 4 AI literacy softened from "ensure" to "take measures to support" (obligation on Commission/Member States to facilitate); AI Office supervision of AI systems built on GPAI models by the same provider strengthened; GPAI obligations and enforcement unchanged; Art. 6(3) registration kept (simplified). White & Case also says the GDPR/ePrivacy/NIS2/Data Act strands of the wider Digital Omnibus are **not yet law** (as of 2026-08-04).

Caveat: article numbers below are from Regulation (EU) 2024/1689 and the article titles were confirmed at https://artificialintelligenceact.eu/article/N/ ; the page text may predate the Omnibus amendments, so verify amended wording on EUR-Lex `OJ:L_202601744`.

### 4.2 Checklist skeleton (deployer of a third-party GPAI model, and fine-tuning provider)

Checklist items are prompts for the human reviewer. Each item has `status`, `evidence_ref`, `owner`. No item states a legal conclusion.

**A. Role determination (precedes everything)**
- A1. Which roles apply to us: deployer (Art. 3 definitions), provider of an AI system (we integrate GLM into a system placed on the EU market under our name), provider of a GPAI model (only if we modify enough, see C). Art. 2 scope: includes non-EU providers/deployers where output is used in the EU.
- A2. Is the use case in Annex III or prohibited under Art. 5 (as amended by Reg. 2026/1744)? If Annex III: obligations from 2 Dec 2027 (not now) but plan for Art. 26 deployer duties, Art. 27 FRIA (where applicable), Art. 73 incident reporting.
- A3. Art. 25: do we put our name on a high-risk system, make a substantial modification, or change intended purpose to high-risk? Then we become the provider of that high-risk system.

**B. Deployer of a GPAI-based system, not high-risk (all deployments)**
- B1. Art. 4 AI literacy measures for staff (post-Omnibus wording: support/facilitate).
- B2. Art. 5: confirm no prohibited practice; add output filters for the new CSAM/NCII ban (from 2 Dec 2026).
- B3. Art. 50 transparency: users informed they interact with AI (50(1), provider duty); machine-readable marking of synthetic text/audio/image/video (50(2)); deployer duties for deepfakes and for AI-generated text published to inform the public on matters of public interest, and for emotion-recognition/biometric categorisation (50(3)-(4)). Watermarking date: 2 Dec 2026 for pre-Aug-2026 systems.
- B4. Upstream documentation obtained from the model provider (see D): record what Z.ai (or Mistral/AWS as hosts) supplies. Note: Art. 53(1)(b) documentation to downstream providers is owed by the GPAI model provider, and open-source exemption may remove it (Art. 53(2)).
- B5. Logging and monitoring we run ourselves (hosted: provider logs; self-hosted: our logs, retention, access control).

**C. If we fine-tune or otherwise modify GLM (provider-status test)**
- C1. Commission GPAI guidelines (published 2025-07-18): a downstream modifier becomes a GPAI model provider if modification compute exceeds one-third of the original model's training compute (fallback thresholds 1/3 of 10^23 FLOP, or 1/3 of 10^25 FLOP for systemic-risk models). Minor changes typically do not. Obligations then apply only to the modification (documentation, training-data summary, copyright policy) unless the original has systemic risk. [S] https://artificialintelligenceact.eu/gpai-guidelines-overview/ (summary; primary guideline text not re-read; WilmerHale summary https://www.wilmerhale.com/en/insights/blogs/wilmerhale-privacy-and-cybersecurity-law/20250724-european-commission-issues-guidelines-for-providers-of-general-purpose-ai-models also surfaced). Search snippets disagree on "exceeds" vs "at least"; take the operator from the guideline PDF.
- C2. Record fine-tuning FLOP (kit should compute `6 x P_act x tokens` estimate and log measured GPU-hours), dataset provenance, and copyright policy.
- C3. Is the base model systemic-risk (>=10^25 FLOP presumption, Art. 51)? My own back-of-envelope for GLM-5 (40B active x 28.5T tokens x 6 ≈ 6.8e24, using the GLM-5 token count from the vLLM GLM-5 recipe) is below 1e25 before post-training, but this is an unverified estimate, not a determination; ask Z.ai or check whether it notified the Commission. No notification evidence found.
- C4. Art. 54 authorised representative: third-country GPAI providers must appoint one in the Union unless exempt; check whether Z.ai has one (not found in sources).
- C5. Open-source exemption (Art. 53(2)): requires a free and open-source licence permitting use/access/modification/redistribution without monetisation; the GLM-5.3 license has a MaaS/>$10B security-review clause and "comply with applicable laws"; whether that defeats the exemption is a question for counsel. Flag in pack.

**D. Upstream provider evidence (Z.ai)**
- D1. Signatory status: not on Commission list as of its 7 Oct 2026 update (below). Non-signatories "must independently demonstrate compliance" and expect more AI Office scrutiny (artificialintelligenceact.eu summary).
- D2. Model card, training-data summary, copyright policy: retrieve from HF card / Z.ai site and attach; if absent, record as gap.

**E. Governance artifacts the kit can generate**
- E1. Data-flow diagram (hosted vs self-hosted); E2. export-control path checklist (4.4); E3. DPIA template (4.3); E4. conformance and eval results; E5. AI-BOM (section 6); E6. change log and incident log; E7. "this is a checklist, not legal advice" notice on every page.

### 4.3 GDPR DPIA (Art. 35) and related

- Art. 35(1) DPIA when processing is likely to result in high risk; Art. 35(3) lists: (a) systematic and extensive automated evaluation incl. profiling with legal/similarly significant effects, (b) large-scale special-category or criminal-offence data, (c) large-scale systematic monitoring of public areas. Art. 35(7) minimum contents: systematic description of processing and purposes; necessity and proportionality assessment; risk assessment; measures to address risks. [P] https://gdpr-info.eu/art-35-gdpr/ (read 2026-10-26; Art. 35(7)(b) necessity-proportionality item exists though my grep cut it).
- EDPB Opinion 28/2024 (adopted 17 Dec 2024): anonymity of a model is case-by-case; legitimate interest (Art. 6(1)(f)) cannot be the default basis and needs the 3-step test; unlawful development-phase processing can affect lawfulness of deployment; [S] https://cms.law/en/int/legal-updates/edpb-opinion-28-2024-key-takeaways-on-processing-personal-data-in-the-context-of-ai-models . The kit's DPIA template should include a "did we assess the upstream model's development lawfulness" prompt (deployer duty in the Opinion; wording to verify in the primary opinion).
- Hosted path adds: processor agreement (Art. 28), transfer mechanism for Chinese/US sub-processors (Chapter V), sub-processor list (Mistral says control plane and some sub-processors are outside region).
- Not researched: national DPA AI guidance beyond the EDPB opinion; Digital Omnibus GDPR amendments (not law as of 2026-08-04 per White & Case).

### 4.4 US Entity List / EAR: hosted API vs self-hosted open weights

- Zhipu AI (Z.ai) added to the Entity List 16 Jan 2025 (90 FR 4619; presumption of denial for all items). [S/P] cited in https://natlawreview.com/article/choosing-between-us-and-chinese-ai-models-export-control-risks-both-sides (published 2026-07-23); Federal Register link given there: https://www.federalregister.gov/documents/2025/01/16/2025-00704/addition-of-entities-to-and-revision-of-entry-on-the-entity-list . **I could not confirm that the NatLawReview piece is the "Sheppard Mullin 2026-07-23" article**; the author firm was not visible in the extracted text and there are no further matches from search. Date matches. Treat as the candidate; confirm author before citing a firm name.
- Its positions (all [S], 2026-07-23): downloading the freely published weights "is not an export to Zhipu", but "sending controlled technology into Zhipu's hosted API is an export requiring a license BIS presumptively denies"; prompting a model hosted in China or run by a Chinese provider with EAR/ITAR technical data is best analysed as an export/release to China; routed via US/EU clouds (Bedrock, Azure Foundry, Together, etc.) "involves no export to China at all" but "check the wiring"; self-hosting "eliminates the provider layer" but the weight-level behaviours remain, and a redesigned U.S. AI-diffusion framework might control foreign-trained weights or outputs; Huawei Ascend hardware is a separate presumptive-violation topic (BIS May 2026 guidance); no known U.S. enforcement action yet for using Chinese AI services with controlled data; AI Diffusion Rule text (ECCN 4E091) was never formally rescinded; recommended 4-step process: map workflows to models, find where controlled technology enters, set model-and-deployment posture per workload, document diligence. Also notes (2026-07) Beijing reportedly considering limiting overseas access to leading Chinese open-weight models.
- Another headline source: "Z.ai's GLM-5.2 is the open-weight model US export controls cannot touch" [S] https://startupfortune.com/zais-glm-52-is-the-open-weight-model-us-export-controls-cannot-touch/ (not read).
- Government evaluation: NIST CAISI assessment of GLM-5.2 (July 2026) judged it comparable to a US model ~6 months older; link per NatLawReview: https://www.nist.gov/news-events/news/2026/07/caisi-assessment-zais-glm-52 (not read).

**Export-control path checklist (for the pack):**
1. Does any prompt/context/output contain EAR- or ITAR-controlled technology or technical data? If yes, record the authorisation basis before using any China-hosted or Z.ai-operated endpoint.
2. Hosted via Z.ai first-party API? -> counterparty is an Entity List party; stop-and-escalate.
3. Hosted via US/EU cloud or reseller (Bedrock, Mistral, Nebius, Scaleway...)? Record: who operates the model, where inference runs, whether any data/telemetry/fees flow to Z.ai (Bedrock docs say inputs/outputs not shared with providers for DeepSeek; confirm per model).
4. Self-hosted open weights? Record download source/hash, license text, GPU vendor (no Huawei Ascend without review), no outbound telemetry, access to the weights by foreign persons (deemed export is person-based).
5. Re-check the Federal Register/BIS for new model-access rules (analysts expect an interim final rule "probably by fall of 2026").

### 4.5 Singapore

- **Model AI Governance Framework for Agentic AI**, IMDA, launched 22 Jan 2026 at Davos; voluntary; four dimensions: bound risks upfront (use-case assessment, limit autonomy and tool/data access), human accountability and approval checkpoints, technical controls across the lifecycle (testing, restricted access), end-user responsibility (transparency, training). [S] https://www.crowdfundinsider.com/?p=257931 ; https://singaporelegaladvice.com/singapore-new-agentic-ai-governance-framework/ ; Modulos docs mention a "v1.5, 20 May 2026" edition (unverified): https://docs.modulos.ai/frameworks/singapore-mgf-agentic . I did not reach the IMDA primary document.
- **PDPC Advisory Guidelines on Use of Personal Data in Generative AI**, published 20 Jul 2026: lifecycle structure (development, deployment...), Publicly Available Exception for web-scraping with documentation requirement (DPIA or written record when reliance is arguable), data responsibilities of GenAI stakeholders, handling individual requests; read with the 2024 AI Recommendation and Decision Systems guidelines (1 Mar 2024). Non-binding but indicates PDPC enforcement positions. [S] https://www.allenandgledhill.com/sg/publication/articles/33390/pdpc-issues-advisory-guidelines-on-use-of-personal-data-in-generative-ai (A&G, 25 Aug 2026; page read).
- **AI Verify / Project Moonshot**: Moonshot (LLM benchmarking + red-teaming toolkit, launched May 2024); GitHub `aiverify-foundation/moonshot` Apache-2.0, v0.7.6 (2026-02-05), last push 2026-06-10; `aiverify-foundation/aiverify` Apache-2.0, v2.2.1 (2026-03-23). [P gh API]. These produce test reports that can be attached as eval evidence for the Singapore variant; 2026 status of the Foundation beyond this not found.
- PDPA general duties (consent, purpose limitation, protection, transfer limitation) apply to hosted paths that send personal data abroad; not researched beyond the PDPC guidelines.

### 4.6 GPAI Code of Practice: is Z.ai a signatory?

- Commission signatory page, "Last update: 7 October 2026" lists 22: AI Studio Delta, Aleph Alpha, Almawave, Amazon, Anthropic, Black Forest Labs, Bria AI, Cohere, Domyn, Dweve, Fastweb, Google, IBM, LINAGORA, Microsoft, Mistral AI, Open Hippo, OpenAI, Pleias, Reflection AI, ServiceNow, WRITER; xAI signed only the Safety and Security chapter. **Zhipu / Z.ai not listed.** [P via WebFetch summarizer; direct curl returned 403 so I could not re-read the page text] https://digital-strategy.ec.europa.eu/en/policies/contents-code-gpai . Meta declined (as of Jan 2026, Wikipedia https://en.wikipedia.org/wiki/General-Purpose_AI_Code_of_Practice). Mistral, Amazon signed -> hosts are covered but they are not the model provider.
- Code of Practice published 10 Jul 2025; chapters: Transparency, Copyright, Safety and Security (systemic-risk models only). Signatories get presumption-of-conformity-style benefits; non-signatories face more scrutiny.

---

## 5. Competitor and adjacent compliance tooling

### 5.1 Commercial platforms (marketing-level evidence only; formats mostly undocumented publicly)

| Product | What the sources show | Evidence formats | Gaps vs our pack |
|---|---|---|---|
| **Credo AI** | AI registry; EU AI Act Readiness Policy Pack (announced Oct 2023); readiness score per use case; vendor-compliance page claims "automated evidence generation" for EU AI Act, NIST AI RMF, ISO 42001, SOC 2; model cards (glossary) [S] https://www.credo.ai/blog/product-announcement-eu-ai-act-readiness-pack-your-first-step-towards-global-ai-compliance ; https://www.credo.ai/solutions/vendor-compliance | Export formats not documented publicly | Generic governance; no deployment/infra topology, no GLM-specific hosted-vs-self-hosted export-control path, no calculator, no conformance/eval attach from the serving stack. A 2026 post notes US CLOUD Act concerns for EU buyers ([S] sota.io, advocacy). |
| **Holistic AI** | Searches returned nothing about its product specifically | unknown | [U] Not assessed. |
| **Vanta** | EU AI Act support press release (risk classification high/low-risk, automated evidence, AI risk assessments); extends SOC 2/ISO 27001 style continuous compliance to ISO 42001/EU AI Act/NIST AI RMF [S] https://www.silicon.co.uk/press-release/vanta-introduces-eu-ai-act-support-for-the-ethical-development-and-use-of-ai-in-europe | Control-evidence repository, auditor exports (generic) | No model-serving or data-flow evidence; relies on integrations. |
| **Drata** | Mapped ISO 42001 framework; holds own ISO 42001 certificate; EU AI Act webinar [S] https://drata.com/resources/eu-ai-act-iso-42001-ai-governance | same | same |
| **OneTrust** | AI impact assessments, conformity assessments, model cards; FPF x OneTrust conformity-assessment guide/flowchart (written before the Omnibus dates) [S] https://www.onetrust.com/de/solutions/eu-ai-act-compliance ; https://fpf.org/blog/fpf-and-onetrust-launch-updated-conformity-assessment-under-the-eu-ai-act-guide-and-infographic/ | Assessment templates/workflows | Questionnaire-based; not tied to running infra. |
| **Modulos** | Framework docs for Singapore agentic MGF and EU AI Act; named in a Gartner MQ for AI Governance Platforms (per its own page) [S] https://docs.modulos.ai/frameworks/singapore-mgf-agentic ; https://www.modulos.ai/modulos-vs-vanta/ | Framework-mapped controls | Same gap. |
| **Fairly AI** | Not researched (time) | -- | [U] |
| **Openlayer** | Markets automated Annex IV documentation from CI/CD [S] https://www.openlayer.com/blog/post/eu-ai-act-technical-documentation-requirements | Annex IV docs | High-risk oriented, not GPAI deployer; not infra-aware. |

Across commercial tools: none publishes a machine-readable evidence schema in what I could read. The differentiator for the kit is **evidence generated from the actual deployment** (Helm values, serving flags, benchmark JSON, region/endpoint logs) and **cross-jurisdiction checklists (EU + SG + US export path)** for one specific model family.

### 5.2 Open-source, potentially reusable (`gh` 2026-10-26)

| Repo | License | Stars / last push | What it is | Reuse |
|---|---|---|---|---|
| Hiepler/EuConform | MIT | 126 / 2026-07-01 | EU AI Act risk classification + bias testing | Candidate for risk-tier questionnaire logic |
| airblackbox/airblackbox | Apache-2.0 | 24 / 2026-10-24 | Local-first scanner, 51 checks across Arts. 9-12, 14-15 | Aimed at high-risk code scanning; limited fit for GPAI deployer |
| ark-forge/mcp-eu-ai-act | MIT | 11 / 2026-09-25 | MCP scanner for EU AI Act violations in codebases | Pattern only |
| el1ght/aulite | "other" | 146 / 2026-06-14 | HTTP proxy logging AI interactions, 143 rules, PDF reports, open-core | License needs review |
| SdSarthak/AegisAI | other | 97 | AI-GRC platform | License needs review |
| GenAI-Gurus/awesome-eu-ai-act | CC0 | 112 / 2026-10-15 | Curated lists | Source discovery |
| Lab700xOrg/aisbom | (see repo) | 83 / 2026-10-25 | Model-file scanner (pickle, Keras Lambda, GGUF templates), emits CycloneDX/SPDX AIBOM | Reusable for weights integrity check; relevant to "model integrity" risk |
| GoogleCloudPlatform/k8s-aibom | (see repo) | 58 / 2026-10-26 | K8s controller inventorying models/runtimes/agents, emits **CycloneDX 1.6 ML-BOMs** with evidence, confidence tiers and Sigstore-verified signatures | Strong fit: runtime AI-BOM for our reference deployment |
| cisco-ai-defense/aibom | -- | 115 / 2026-10-21 | AI-BOM from source scanning | Optional |
| GenAI-Security-Project/aibom-generator | -- | 107 / 2026-10-17 | OWASP GenAI Security Project AIBOM generator (HF models) | Candidate to generate from HF model IDs |
| sbom-tool/sbom-tools | -- | 251 / 2026-10-26 | SBOM/AI-BOM diff, quality scoring, EU AI Act regulatory gates for CycloneDX/SPDX | Validation/lint of our output |
| manifest-cyber/aibom | -- | 47 / 2026-07-17 | Community wiki | Reference |
| aiverify-foundation/moonshot, aiverify | Apache-2.0 | 359 / 103 | Singapore LLM eval + report toolkit | Eval evidence for SG variant |

Not verified: license files for repos marked "--" (the `gh search` result gave no license). Check before copying code.

---

## 6. AI-BOM format recommendation

- **CycloneDX is the primary format.** Spec repo `CycloneDX/specification`, Apache-2.0, **latest 1.7.2 (2026-09-17)**, schemas for 1.5, 1.6, 1.7 present [P gh API]. It has ML model and data components with model-card fields, vulnerabilities/VEX, services, and `evidence`. Google's `k8s-aibom` emits **1.6**; `cyclonedx-python-lib` v11.12.0 (2026-08-13). Recommendation: emit **CycloneDX 1.6 (widest tool support) and validate also against 1.7 schema**; keep a mapping layer so we can bump to 1.7 once k8s-aibom/aisbom support it. (Whether 1.7 adds new AI-specific fields was not verified; do not claim any.)
- **SPDX 3.0 AI profile as optional export.** `spdx/spdx-3-model` latest release 3.0.1 (2024-12-12), active 2026-10-23. Search summaries claim a finalised AI profile with fields for training environment, energy use, metrics, safety risk classification, intended use ([S] https://wavect.io/blog/ai-bill-of-materials-cyclonedx-spdx-2026/, vendor blog; maturity conflicting across sources). Treat as lower tooling maturity.
- **Components to include per deployment:** (1) model: `zai-org/GLM-5.3` repo, revision SHA, file hashes (sha256 of safetensors), license `LicenseRef-GLM-5.3` with link, modelCard (architecture, params 753.3B, quantisation, intended use), (2) serving engine + version + image digest (vLLM 0.28.0 / SGLang 0.5.x), (3) tokenizer/chat template hash (template affects tool rendering), (4) MTP/draft model if any (DSpark/DFlash2 are third-party weights with their own licenses), (5) hardware/driver/CUDA versions, (6) hosting service as a `service` component with endpoint, region(s), data classification and cross-border flags, (7) eval and conformance results as `evidence`/external references to signed report files, (8) provenance: Sigstore signatures where available.
- Pack manifest: a top-level JSON (our own, versioned schema) that indexes the BOM, DFD (Mermaid/SVG), checklists (YAML with `status`, `evidence_ref`), DPIA (Markdown), bench JSON, eval JSON, and a hash tree; sign with Sigstore/cosign.

---

## 7. Reference deployment recommendation (summary)

1. **Default self-hosted EU profile:** 1 node 8x H200 (Nebius list $5.40/GPU-h; H200 not in Scaleway's PAR-2 list; Scaleway alternative 8x B300 EUR 60/h), FP8 `zai-org/GLM-5.3`, vLLM 0.28.0 with the 1.2 command, plus `--max-model-len` set from the calculator (do not assume 1M on H200; ~0.6M tokens total KV at TP8), `--max-num-seqs` tuned, `--gpu-memory-utilization` 0.9, prefix caching on, MTP 5 tokens (3 per llm-d practice at high concurrency). Pin image digest, transformers >= 5.15.0, DeepGEMM.
2. **High-context profile:** 8x B200 (vLLM recipe) with FP8 KV and `--max-num-seqs 32`, or NVFP4 (Inferact, ~465 GB) with EP.
3. **Cluster profile:** llm-d v1.0.0 + vLLM, P/D disaggregated, DEP16 pools, tiered KV; start from `guides/models/glm-5-2`.
4. **Flash profile:** 8x H200 aggregated per Dynamo recipe (dev image tag risk).
5. **Conformance suite (attached as evidence):** parser smoke tests (tool_calls and `reasoning_content` separation), long-context needle test at the configured max length, MTP acceptance logging, prefix-cache hit-rate probe, `vllm bench serve` / `sglang.bench_serving` JSON, plus accuracy spot checks (avoid IndexCache override).
6. **Hosted profiles:** Mistral EU endpoint (verify GLM-5.3 on `api.eu.mistral.ai/models`; 1.1x price), Bedrock (flag as non-EU-resident, global/US only, eligibility gating), others by provider table.

---

## 8. Open questions and unverified items for the lead

1. Is GLM-5.3 actually served on Mistral's EU regional endpoint? (call `models.list` against `api.eu.mistral.ai`).
2. Sheppard Mullin 2026-07-23: not found by name; the matching-date piece is on NatLawReview with no visible firm byline. Confirm author.
3. Official Bedrock GLM 5.3 prices (only third-party trackers).
4. Crusoe GPU prices and EU locations, OVH catalogue, IONOS model list, DeepInfra EU: unconfirmed.
5. Primary text of the Commission GPAI guidelines (modifier threshold operator and fallback) and of the Code of Practice Transparency chapter not re-read; only summaries.
6. Whether the GLM-5.3 license qualifies as "free and open-source" for Art. 53(2); counsel question.
7. Z.ai AI Act authorised representative / systemic-risk notification: nothing found.
8. Competitor evidence export formats (Credo, Holistic, Fairly) not publicly documented in what I could read.
9. H200 HBM bandwidth (4.8 TB/s) used in roofline came from memory, not a fetched source.
10. Date stamps: all pages "as of 2026-10-26"; provider prices change weekly.
