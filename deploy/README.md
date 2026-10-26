# Reference deployment for GLM-5.3

Config and docs only. The kit never applies these files to a cluster or a cloud account. Hand them to a partner
as a starting point, then size the hardware first:

```sh
glmkit deploy size --model glm-5.3 --precision fp8 --gpu h200 --gpus 8 --context 128k --concurrency 64
glmkit deploy validate          # 44.9 KB/token and 755.7 GB FP8 recomputed from the config and file listing
glmkit deploy health --target vllm-local --wait 600   # WIRE conformance cases against the running endpoint
```

| Profile | Files | Engine pin | Parsers (tool / reasoning) |
|---|---|---|---|
| Single node, Docker | `vllm-single-node/docker-compose.yml` | vLLM v0.28.0 (tag + digest) | `glm47` / `glm47` |
| Single node, Helm | `vllm-single-node/values.yaml` (production-stack `vllm-stack` 0.1.13) | vLLM v0.28.0 (tag + digest) | `glm47` / `glm47` |
| Single node, SGLang | `sglang-single-node/docker-compose.yml` | SGLang v0.5.18 (tag + digest) | `glm47` / `glm45` |
| Cluster | `llm-d-cluster/profile.yaml` | llm-d v1.0.0 with vLLM | `glm47` / `glm45` |

**Parsers differ per engine.** vLLM's recipe uses `glm47` for both parsers. SGLang and the llm-d guide use `glm47`
for tool calls and `glm45` for reasoning. Without parser flags the engine never emits tool calls (llm-d issue
#2640), so the health check matters. A test pins each file to its engine's values.

**Context length.** Do not assume 1M tokens on H200. The MLA cache is copied on every tensor-parallel rank, so one
8 x H200 node holds about 0.6M tokens in total. `--max-model-len` in the files is a placeholder to be set from
`glmkit deploy size`. For 1M context use 8 x B200 with `--max-num-seqs 32`.

**Before use.** Replace the digests when you move versions (`docker buildx imagetools inspect <image:tag>`), create
the Hugging Face Secret yourself, set an API key, and confirm the license terms (`license: other`, MIT-style with a
Model-as-a-Service clause; research/04 section 0). The llm-d guide covers GLM-5.2 only, so the cluster profile is an
adaptation, not a supported configuration.

**Linting.** `docker compose config` validates both compose files. The Helm values are checked against
`schemas/vllm-stack-values.schema.json` because `helm lint` needs the helm binary and the chart; run
`helm lint` yourself where helm is installed.
