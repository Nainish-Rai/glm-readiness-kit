"""Readiness check for a deployment: wait for the endpoint, then run the WIRE conformance cases against it."""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from glmkit.conform.runner import RunOptions, RunOutcome, run_conformance
from glmkit.contracts.profile import EndpointProfile


def wait_ready(profile: EndpointProfile, wait_s: float, interval_s: float = 2.0) -> tuple[bool, str]:
    """Poll ``GET <base_url>/models`` until it answers 200 or ``wait_s`` runs out. Mock targets are always ready."""
    if profile.spawn_mock or "{mock}" in profile.base_url:
        return True, "in-process mock"
    url = profile.base_url.rstrip("/") + "/models"
    headers = {}
    key = profile.api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    deadline = time.monotonic() + wait_s
    last = "no attempt"
    while True:
        try:
            r = httpx.get(url, headers=headers, timeout=5.0)
            if r.status_code == 200:
                return True, f"GET {url} -> 200"
            last = f"GET {url} -> HTTP {r.status_code}"
        except httpx.HTTPError as e:
            last = f"GET {url} failed: {type(e).__name__}"
        if time.monotonic() >= deadline:
            return False, last
        time.sleep(interval_s)


async def run_health(
    profile: EndpointProfile, out_dir: Path, cases: list[str] | None = None, mock_defects: list[str] | None = None
) -> RunOutcome:
    """Run the WIRE cases (slice 01) against ``profile``; the result is an ordinary conform envelope."""
    opts = RunOptions(patterns=cases or ["WIRE-*"], out_dir=out_dir, mock_defects=mock_defects or [])
    return await run_conformance(profile, opts)
