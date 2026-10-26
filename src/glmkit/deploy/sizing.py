"""MoE sizing calculator for GLM-5.x (research/04-deploy-compliance.md section 2.1).

All formulas live here and are ported once to ``report/pages/deploy.js`` for the Deploy tab. A test runs both on
the same inputs and asserts equal outputs, so change both together.

Everything is an estimate except the two checks in :func:`validate`: the MLA KV bytes per token and the FP8
checkpoint size, both computed from the model's published config and file listing. Throughput comes from a
roofline fitted on two published SGLang measurements and is heuristic.
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import asdict, dataclass
from importlib import resources
from typing import Any

GIB = 1024**3
KV_REFERENCE_BYTES = 44_900  # llm-d: 25 GiB per rank / 598K tokens
KV_TOLERANCE = 0.01
WEIGHTS_REFERENCE_GB = 755.7  # research/04 section 1.1: sum of HF siblings sizes
WEIGHTS_TOLERANCE = 0.001
CTX_ALIASES = {"1m": 1_048_576}


class SizingError(ValueError):
    pass


def _data(name: str) -> Any:
    return json.loads(resources.files("glmkit.deploy").joinpath("data", name).read_text(encoding="utf-8"))


def load_catalog() -> dict[str, Any]:
    """Deploy catalog: GPU prices, calibration and sizing data from ``catalog.json``; the API prices of the
    comparison come from the one price table (``glmkit.cost.prices``) and are shaped as ``cat["apis"]``."""
    from glmkit.cost.prices import load_price_table

    cat = _data("catalog.json")
    table = load_price_table()
    apis: dict[str, Any] = {}
    for name, host in cat.pop("api_hosts").items():
        if name.startswith("_"):
            continue
        e = table.lookup(cat["model"]["id"], host)
        if e is None or e.input is None or e.output is None:
            raise SizingError(f"price table has no complete {cat['model']['id']} price for host {host!r}")
        src: dict[str, Any] = {"url": e.source, "date": e.retrieved.isoformat()}
        if e.secondary:
            src["secondary"] = True
        apis[name] = {
            "title": e.title or host,
            "in_usd_m": e.input,
            "cached_usd_m": e.cached_input,
            "out_usd_m": e.output,
            "source": src,
        }
    cat["apis"] = apis
    return cat


def load_config() -> dict[str, Any]:
    return _data("glm-5.3.config.json")


def load_manifest() -> dict[str, Any]:
    return _data("glm-5.3.files.json")


def parse_tokens(value: str | int) -> int:
    """'128k' -> 131072, '1m' -> 1048576, '32768' -> 32768."""
    if isinstance(value, int):
        return value
    s = value.strip().lower().replace(",", "").replace("_", "")
    if s in CTX_ALIASES:
        return CTX_ALIASES[s]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([km]?)", s)
    if not m:
        raise SizingError(f"cannot parse token count {value!r} (use 32768, 128k or 1m)")
    n = float(m.group(1)) * {"": 1, "k": 1024, "m": 1024**2}[m.group(2)]
    return int(n)


def manifest_gb(manifest: dict[str, Any]) -> float:
    return sum(size for _, size in manifest["files"]) / 1e9


def kv_bytes_per_token_mla(cfg: dict[str, Any], kv_dtype_bytes: float) -> float:
    """MLA latent cache: layers x (kv_lora_rank + qk_rope_head_dim) x bytes. One copy per sequence."""
    return cfg["num_hidden_layers"] * (cfg["kv_lora_rank"] + cfg["qk_rope_head_dim"]) * kv_dtype_bytes


def build_payload(
    catalog: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything the calculator needs, as plain JSON. The Deploy tab receives exactly this."""
    cat = copy.deepcopy(catalog or load_catalog())
    cfgdoc = config or load_config()
    cfg = cfgdoc["config"]
    mf = manifest or load_manifest()
    n_moe = cfg["num_hidden_layers"] - cfg["first_k_dense_replace"]
    expert_params = 3 * cfg["hidden_size"] * cfg["moe_intermediate_size"]
    p_total = cfgdoc["safetensors_total_params"]
    checkpoints = {
        "fp8": manifest_gb(mf),
        "bf16": cat["checkpoints"]["bf16"]["gb"],
        "nvfp4": cat["checkpoints"]["nvfp4"]["gb"],
    }
    spec = {
        "id": cat["model"]["id"],
        "layers": cfg["num_hidden_layers"],
        "moe_layers": n_moe,
        "kv_lora_rank": cfg["kv_lora_rank"],
        "qk_rope_head_dim": cfg["qk_rope_head_dim"],
        "experts": cfg["n_routed_experts"],
        "top_k": cfg["num_experts_per_tok"],
        "expert_params": expert_params,
        "indexer_full_layers": cfg["indexer_full_layers"],
        "index_head_dim": cfg["index_head_dim"],
        "total_params": p_total,
        "active_params": cat["model"]["active_params"],
        "max_positions": cfg["max_position_embeddings"],
        "checkpoint_gb": checkpoints,
    }
    return {
        "spec": spec,
        "gpus": cat["gpus"],
        "apis": cat["apis"],
        "kv_dtype_bytes": cat["kv_dtype_bytes"],
        "fitted": cat["fitted"],
        "assumptions": cat["assumptions"],
    }


@dataclass
class Inputs:
    model: str = "glm-5.3"
    precision: str = "fp8"  # fp8 | bf16 | nvfp4 (weights)
    kv_dtype: str = "fp8"  # fp8 | bf16
    gpu: str = "h200"
    n_gpus: int = 8
    context: int = 131_072  # max model len to serve
    concurrency: int = 64
    avg_input: int = 8_192
    avg_output: int = 1_024
    prefix_reuse: float = 0.0
    api: str = "zai"
    utilisation: float | None = None
    mtp_accept: float = 1.0
    effort_multiplier: float = 1.0
    dcp: int = 1  # decode context parallel size (shards the MLA cache); 1 = replicated across TP ranks
    include_indexer: bool = False
    routing_skew: float | None = None
    eta: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Inputs:
        base = asdict(cls())
        base.update({k: v for k, v in d.items() if k in base and v is not None})
        return cls(**base)


def experts_touched(experts: int, top_k: int, batch: float, skew: float = 0.0) -> float:
    """Unique experts hit per layer by ``batch`` tokens. Uniform routing is an upper bound; ``skew`` 0..1 moves
    linearly to 'every token uses the same top_k experts'."""
    uniform = experts * (1 - (1 - top_k / experts) ** batch)
    return uniform * (1 - skew) + min(uniform, top_k) * skew


def _step_bytes(spec: dict[str, Any], bytes_per_param: float, touched: float) -> float:
    routed_active = spec["top_k"] * spec["moe_layers"] * spec["expert_params"]
    dense_active = spec["active_params"] - routed_active
    return (dense_active + touched * spec["moe_layers"] * spec["expert_params"]) * bytes_per_param


def fit_calibration(payload: dict[str, Any], calibration: dict[str, Any]) -> dict[str, float]:
    """Fit eta (HBM efficiency) from the batch-1 point and routing_skew from the batch-100 point."""
    spec = payload["spec"]
    b1, c100 = calibration["sglang_b1"], calibration["sglang_c100"]
    gpu = payload["gpus"][b1["gpu"]]
    bpp = spec["checkpoint_gb"]["fp8"] * 1e9 / spec["total_params"]
    n = b1["n_gpus"]
    peak = n * gpu["hbm_tb_s"] * 1e12
    eta = _step_bytes(spec, bpp, spec["top_k"]) / (peak * (b1["batch"] / b1["out_tok_s"]))
    t_target = c100["batch"] / c100["out_tok_s"]
    routed = spec["top_k"] * spec["moe_layers"] * spec["expert_params"]
    dense_active = spec["active_params"] - routed
    touched_target = (t_target * peak * eta / bpp - dense_active) / (spec["moe_layers"] * spec["expert_params"])
    uniform = experts_touched(spec["experts"], spec["top_k"], c100["batch"])
    skew = (uniform - touched_target) / (uniform - spec["top_k"])
    return {"eta": eta, "routing_skew": skew}


UTIL_GRID = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


def size(inp: Inputs | dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Memory, KV capacity, MoE bandwidth, throughput, cost and feasibility for one hardware choice."""
    payload = payload or build_payload()
    i = inp if isinstance(inp, Inputs) else Inputs.from_dict(inp)
    spec, asm = payload["spec"], payload["assumptions"]
    if i.model != spec["id"]:
        why = _data("catalog.json")["model"]["unsupported"].get(i.model)
        raise SizingError(why or f"unknown model {i.model!r}; supported: {spec['id']}")
    if i.precision not in spec["checkpoint_gb"]:
        raise SizingError(f"unknown precision {i.precision!r}; choose {', '.join(spec['checkpoint_gb'])}")
    if i.kv_dtype not in payload["kv_dtype_bytes"]:
        raise SizingError(f"unknown kv dtype {i.kv_dtype!r}; choose {', '.join(payload['kv_dtype_bytes'])}")
    if i.gpu not in payload["gpus"]:
        raise SizingError(f"unknown gpu {i.gpu!r}; choose {', '.join(payload['gpus'])}")
    if i.api not in payload["apis"]:
        raise SizingError(f"unknown api {i.api!r}; choose {', '.join(payload['apis'])}")
    if i.n_gpus < 1 or i.concurrency < 1 or i.dcp < 1:
        raise SizingError("n_gpus, concurrency and dcp must be >= 1")
    if not 0 <= i.prefix_reuse < 1:
        raise SizingError("prefix_reuse must be in [0, 1)")
    if i.context < i.avg_input + i.avg_output:
        raise SizingError(f"context {i.context} is smaller than avg_input + avg_output")
    gpu, api = payload["gpus"][i.gpu], payload["apis"][i.api]
    util = asm["default_utilisation"] if i.utilisation is None else i.utilisation
    if not 0 < util <= 1:
        raise SizingError("utilisation must be in (0, 1]")
    eta = payload["fitted"]["eta"] if i.eta is None else i.eta
    skew = payload["fitted"]["routing_skew"] if i.routing_skew is None else i.routing_skew

    # Memory per GPU
    weights_gb = spec["checkpoint_gb"][i.precision]
    weights_pg = weights_gb / i.n_gpus
    hbm = gpu["hbm_gb"]
    usable = hbm * asm["gpu_memory_utilization"]
    overhead = asm["runtime_overhead_gb"]
    kv_free = usable - weights_pg - overhead

    # KV
    kvb = payload["kv_dtype_bytes"][i.kv_dtype]
    mla = spec["layers"] * (spec["kv_lora_rank"] + spec["qk_rope_head_dim"]) * kvb
    indexer = spec["indexer_full_layers"] * spec["index_head_dim"] * 1 if i.include_indexer else 0
    per_token = mla + indexer
    max_tokens = max(0.0, kv_free) * 1e9 * i.dcp / per_token
    tokens_per_seq = i.avg_input * (1 - i.prefix_reuse) + i.avg_output
    max_conc = math.floor(max_tokens / tokens_per_seq)

    # Feasibility
    weights_fit = kv_free > 0
    context_fits = weights_fit and max_tokens >= i.context
    conc_fits = weights_fit and max_conc >= i.concurrency
    reasons: list[str] = []
    if not weights_fit:
        reasons.append(
            f"weights {weights_gb:,.0f} GB over {i.n_gpus} GPUs = {weights_pg:,.1f} GB each; only "
            f"{usable - overhead:,.1f} GB of {hbm} GB x {asm['gpu_memory_utilization']} is usable after overhead"
        )
    elif not context_fits:
        reasons.append(
            f"KV cache holds {max_tokens:,.0f} tokens but context {i.context:,} needs one full sequence; "
            "the MLA cache is replicated on every tensor-parallel rank, so adding GPUs adds no KV capacity "
            "(use more HBM per GPU, FP8 KV, dcp, or a shorter context)"
        )
    if weights_fit and not conc_fits:
        reasons.append(
            f"concurrency {i.concurrency} needs {i.concurrency * tokens_per_seq:,.0f} KV tokens at "
            f"{tokens_per_seq:,.0f} per sequence; capacity is {max_tokens:,.0f} (max concurrency {max_conc})"
        )
    feasible = weights_fit and context_fits

    out: dict[str, Any] = {
        "inputs": asdict(i),
        "weights": {"total_gb": weights_gb, "per_gpu_gb": weights_pg},
        "memory": {
            "hbm_gb": hbm,
            "usable_gb": usable,
            "weights_per_gpu_gb": weights_pg,
            "overhead_gb": overhead,
            "kv_free_per_gpu_gb": kv_free,
            "headroom_gb": hbm - usable,
        },
        "kv": {
            "bytes_per_token_mla": mla,
            "bytes_per_token_indexer": indexer,
            "bytes_per_token": per_token,
            "max_tokens": max_tokens,
            "tokens_per_seq": tokens_per_seq,
            "max_concurrency": max_conc,
            "full_context_seqs": max_tokens / i.context,
        },
        "feasibility": {
            "feasible": feasible,
            "weights_fit": weights_fit,
            "context_fits": context_fits,
            "concurrency_fits": conc_fits,
            "reasons": reasons,
        },
        "moe": None,
        "perf": None,
        "cost": None,
    }
    if not weights_fit:
        return out

    # MoE bandwidth term and roofline
    batch = max(1, min(i.concurrency, max_conc))
    bpp = weights_gb * 1e9 / spec["total_params"]
    touched = experts_touched(spec["experts"], spec["top_k"], batch, skew)
    uniform = experts_touched(spec["experts"], spec["top_k"], batch, 0.0)
    step_bytes = _step_bytes(spec, bpp, touched)
    peak_bw = i.n_gpus * gpu["hbm_tb_s"] * 1e12
    flops = i.n_gpus * gpu["fp8_tflops"] * 1e12 * (0.5 if i.precision == "bf16" else 1.0)
    t_mem = step_bytes / (peak_bw * eta)
    t_comp = 2 * spec["active_params"] * batch / (flops * asm["compute_eta"])
    t_step = max(t_mem, t_comp)
    tok_s = batch * i.mtp_accept / t_step
    prefill_tok_s = flops * asm["prefill_eta"] / (2 * spec["active_params"])
    out["moe"] = {
        "batch": batch,
        "experts_touched_per_layer": touched,
        "experts_touched_uniform": uniform,
        "touched_fraction": touched / spec["experts"],
        "bytes_read_per_step_gb": step_bytes / 1e9,
        "read_fraction_of_weights": step_bytes / (weights_gb * 1e9),
    }
    out["perf"] = {
        "step_time_ms": t_step * 1e3,
        "memory_bound": t_mem >= t_comp,
        "tokens_per_step": i.mtp_accept,
        "decode_tok_s": tok_s,
        "per_user_tok_s": tok_s / batch,
        "prefill_tok_s": prefill_tok_s,
        "eta": eta,
        "routing_skew": skew,
    }

    # Cost: self-hosted vs API for the same request mix
    out_tok = i.avg_output * i.effort_multiplier
    in_uncached = i.avg_input * (1 - i.prefix_reuse)
    in_cached = i.avg_input * i.prefix_reuse
    node_usd_s = gpu["usd_hr"] * i.n_gpus / 3600
    req_s = in_uncached / prefill_tok_s + out_tok / tok_s
    self_req = node_usd_s * req_s
    cached_price = api["cached_usd_m"] if api["cached_usd_m"] is not None else api["in_usd_m"]
    api_req = (in_uncached * api["in_usd_m"] + in_cached * cached_price + out_tok * api["out_usd_m"]) / 1e6
    m_tokens = (i.avg_input + out_tok) / 1e6
    breakeven = self_req / api_req
    curve = [{"utilisation": u, "self_usd_m": self_req / u / m_tokens} for u in UTIL_GRID]
    out["cost"] = {
        "node_usd_hr": gpu["usd_hr"] * i.n_gpus,
        "utilisation": util,
        "self_usd_m_full": self_req / m_tokens,
        "self_usd_m": self_req / util / m_tokens,
        "api_usd_m": api_req / m_tokens,
        "self_usd_per_m_output": self_req / util / (out_tok / 1e6),
        "api_usd_per_request": api_req,
        "self_usd_per_request": self_req / util,
        "self_vs_api": (self_req / util) / api_req,
        "breakeven_utilisation": breakeven,
        "breakeven_reachable": breakeven <= 1.0,
        "curve": curve,
    }
    return out


def cost_per_m_from_throughput(gpu_usd_hr: float, n_gpus: int, tok_s: float) -> float:
    """research/04 section 2.1 item 7: (GPU_hour_price x N) / (tokens_per_second x 3600) x 1e6."""
    return gpu_usd_hr * n_gpus / (tok_s * 3600) * 1e6


def flatten(d: Any, prefix: str = "") -> dict[str, float | bool]:
    """Numeric and boolean leaves, keyed by dotted path. Used to compare Python and JS outputs."""
    out: dict[str, float | bool] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, f"{prefix}{k}."))
    elif isinstance(d, list):
        for n, v in enumerate(d):
            out.update(flatten(v, f"{prefix}{n}."))
    elif isinstance(d, bool | int | float):
        out[prefix.rstrip(".")] = d
    return out


# ---- validation against published figures --------------------------------------------------------------------


def validate(config: dict[str, Any] | None = None, manifest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Recompute the two published figures from the model config and file listing."""
    cfgdoc = config or load_config()
    cfg = cfgdoc["config"]
    mf = manifest or load_manifest()
    kv = kv_bytes_per_token_mla(cfg, 1)
    kv_err = abs(kv - KV_REFERENCE_BYTES) / KV_REFERENCE_BYTES
    w = manifest_gb(mf)
    w_err = abs(w - WEIGHTS_REFERENCE_GB) / WEIGHTS_REFERENCE_GB
    return [
        {
            "name": "kv_bytes_per_token_fp8",
            "ok": kv_err <= KV_TOLERANCE,
            "computed": kv,
            "reference": KV_REFERENCE_BYTES,
            "error": kv_err,
            "tolerance": KV_TOLERANCE,
            "formula": f"{cfg['num_hidden_layers']} layers x ({cfg['kv_lora_rank']} + {cfg['qk_rope_head_dim']}) x 1 B",
            "source": "llm-d blog: 25 GiB per rank / ~598K tokens = 44.9 KB",
        },
        {
            "name": "fp8_checkpoint_gb",
            "ok": w_err <= WEIGHTS_TOLERANCE,
            "computed": w,
            "reference": WEIGHTS_REFERENCE_GB,
            "error": w_err,
            "tolerance": WEIGHTS_TOLERANCE,
            "formula": f"sum of {len(mf['files'])} file sizes in the repo listing",
            "source": "research/04 section 1.1 (HF siblings sum)",
        },
    ]
