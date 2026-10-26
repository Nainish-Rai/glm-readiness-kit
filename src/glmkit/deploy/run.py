"""Turn a sizing result into a result envelope (component ``deploy``) so the dashboard and evidence pack find it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from glmkit.contracts.envelope import CheckResult, ItemResult, ResultEnvelope, new_run_id

COMPONENT = "deploy"


def _item(item_id: str, title: str, ok: bool, severity: str, message: str, soft: bool = False) -> ItemResult:
    status = "pass" if ok else ("soft_fail" if soft else "fail")
    return ItemResult(
        id=item_id,
        group=item_id,
        title=title,
        kind="sizing_check",
        severity=severity,  # type: ignore[arg-type]
        status=status,
        reason=None if ok else message,
        checks=[CheckResult(name=item_id, status=status, message=message, soft=soft)],
    )


def sizing_envelope(
    result: dict[str, Any], validation: list[dict[str, Any]], started: datetime | None = None
) -> ResultEnvelope:
    f = result["feasibility"]
    reasons = f["reasons"]
    items = [
        _item("DEPLOY-weights-fit", "Weights fit in GPU memory", f["weights_fit"], "critical", "; ".join(reasons[:1])),
        _item(
            "DEPLOY-context-fits",
            "One full-context sequence fits in the KV cache",
            f["context_fits"],
            "critical",
            reasons[0] if reasons and f["weights_fit"] else "skipped: weights do not fit",
        ),
        _item(
            "DEPLOY-concurrency-fits",
            "Requested concurrency fits in the KV cache",
            f["concurrency_fits"],
            "major",
            next((r for r in reasons if r.startswith("concurrency")), "not evaluated"),
            soft=True,
        ),
    ]
    for v in validation:
        items.append(
            _item(
                f"DEPLOY-validate-{v['name']}",
                f"{v['name']} reproduces the published figure",
                v["ok"],
                "critical",
                f"computed {v['computed']:.6g} vs reference {v['reference']:.6g} "
                f"(error {v['error']:.4%}, tolerance {v['tolerance']:.2%})",
            )
        )
    now = datetime.now(UTC)
    env = ResultEnvelope(
        run_id=new_run_id(COMPONENT, now),
        component=COMPONENT,
        started_at=started or now,
        items=items,
        config=result["inputs"],
        data={"sizing": result, "validation": validation, "heuristic": True},
    )
    return env.finalize()
