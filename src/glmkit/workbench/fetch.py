"""Download the pinned files of a demo dataset, verify sha256, build the golden set next to the raw files.

Nothing is committed to the repo: ``<dest>/raw/<set>/...`` holds the downloads, ``<dest>/<set>.yaml`` the golden
set, ``<dest>/<set>.fetch.json`` the record (revision, checksums, license, when). A checksum mismatch deletes the
download and fails; a half-downloaded file never survives (written to ``.part`` first).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

from glmkit.workbench.builders import BUILDERS
from glmkit.workbench.cards import DatasetCard, SourceFile, load_card


class FetchError(RuntimeError):
    pass


class ChecksumError(FetchError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(client: httpx.Client, f: SourceFile, target: Path) -> None:
    part = target.with_suffix(target.suffix + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    try:
        with client.stream("GET", f.url, follow_redirects=True, timeout=120.0) as r:
            if r.status_code != 200:
                raise FetchError(f"{f.url}: HTTP {r.status_code}")
            with part.open("wb") as out:
                for chunk in r.iter_bytes(1 << 20):
                    h.update(chunk)
                    out.write(chunk)
    except httpx.HTTPError as e:
        part.unlink(missing_ok=True)
        raise FetchError(f"{f.url}: {type(e).__name__}: {e}") from e
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if h.hexdigest() != f.sha256:
        part.unlink(missing_ok=True)
        raise ChecksumError(f"{f.url}: sha256 {h.hexdigest()} != pinned {f.sha256}; download discarded")
    part.replace(target)


def verify_raw(card: DatasetCard, raw: Path) -> list[str]:
    """Problems with the raw files already on disk (missing, wrong size or wrong checksum)."""
    out = []
    for f in card.source.files:
        p = raw / f.path
        if not p.is_file():
            out.append(f"{f.path}: missing")
        elif sha256_file(p) != f.sha256:
            out.append(f"{f.path}: sha256 mismatch")
    return out


def fetch_set(
    set_id: str,
    dest: Path,
    *,
    client: httpx.Client | None = None,
    card: DatasetCard | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> Path:
    """Fetch and build one set; returns the golden set path. Re-running verifies and reuses good downloads."""
    card = card or load_card(set_id)
    raw = dest / "raw" / card.id
    own = client is None
    client = client or httpx.Client()
    try:
        for f in card.source.files:
            target = raw / f.path
            if target.is_file() and sha256_file(target) == f.sha256:
                log(f"  {f.path}: cached, sha256 ok")
                continue
            log(f"  {f.url}")
            _download(client, f, target)
            log(f"  {f.path}: {target.stat().st_size} bytes, sha256 ok ({f.sha256[:12]}...)")
    finally:
        if own:
            client.close()
    golden = BUILDERS[card.id](raw)
    for it in golden["items"]:
        it.setdefault("meta", {})["revision"] = card.source.revision
    out = dest / f"{card.id}.yaml"
    out.write_text(yaml.safe_dump(golden, sort_keys=False, allow_unicode=True), encoding="utf-8")
    record = {
        "set": card.id,
        "golden_set": out.name,
        "items": len(golden["items"]),
        "revision": card.source.revision,
        "license": card.license.spdx,
        "license_verified_at": card.license.verified_at,
        "fetched_at": datetime.now(UTC).isoformat(),
        "files": [{"path": f.path, "url": f.url, "sha256": f.sha256, "bytes": f.bytes} for f in card.source.files],
    }
    (dest / f"{card.id}.fetch.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return out


def fetched(set_id: str, dest: Path) -> dict | None:
    """The fetch record when the set was fetched into ``dest`` (and its golden set file is still there)."""
    rec = dest / f"{set_id}.fetch.json"
    if not rec.is_file() or not (dest / f"{set_id}.yaml").is_file():
        return None
    try:
        return json.loads(rec.read_text(encoding="utf-8"))
    except ValueError:
        return None
