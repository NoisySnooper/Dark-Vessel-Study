"""Radar chips for the bundle: 130 x 64 px WebP (VV 64 x 64, 2 px gap, VH 64 x 64) at the 10 m GRD pixel spacing.

Windows are read remotely from the AWS mirror (sentinel-s1-l1c) with the helpers the CNN verifier uses: one windowed
VV/VH read per 1024 px COG tile group (darkvessel.ml.regional_verify.tile_groups), sigma0 in dB with thermal noise not
removed (darkvessel.ml.chips.chips_db), through darkvessel.live.scene.LiveGRDScene (reads retried on new connections).
Each polarisation is stretched from its 2nd percentile to its maximum (contract 6.2), grayscale, WebP quality 70.

Cache: data/cache/chips/<det_id>.webp (the backend serves the same files as /contacts/{det_id}/chip.webp), written
atomically, so a rerun reads only what is missing. A failed read is skipped, counted, and noted in
data/cache/chips/failed.json (retried on the next run).
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

CHIP_W, CHIP_H, GAP = 130, 64, 2
QUALITY = 70
PREFIX = "data:image/webp;base64,"
TIERS = {1: "primary contact of a lead, by priority", 2: "matched live contact", 3: "CNN-accepted unmatched live contact",
         4: "label queue sample: live high and medium contacts of the newest pass by CNN score, then September high "
            "contacts in a fixed hash order"}


def render(chip_db: np.ndarray) -> bytes:
    """(2, 64, 64) dB array (VV, VH) to WebP bytes: 2nd percentile to maximum per polarisation, NaN black."""
    img = np.zeros((CHIP_H, CHIP_W), np.uint8)
    for k in range(2):
        a = np.asarray(chip_db[k], dtype=np.float32)
        f = np.isfinite(a)
        if f.any():
            lo, hi = float(np.percentile(a[f], 2)), float(a[f].max())
            v = np.clip((np.where(f, a, lo) - lo) / max(hi - lo, 1e-6), 0, 1) * 255
            v[~f] = 0
        else:
            v = np.zeros_like(a)
        x0 = k * (64 + GAP)
        img[:, x0:x0 + 64] = v.astype(np.uint8)
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(img, "L").save(buf, "WEBP", quality=QUALITY, method=6)
    return buf.getvalue()


def entry_bytes(det_id: str, webp: bytes) -> int:
    """Bytes one chip adds to the chips part: "det_id":"data:image/webp;base64,...", plus a comma."""
    return len(det_id) + 6 + len(PREFIX) + 4 * ((len(webp) + 2) // 3)


def data_uri(webp: bytes) -> str:
    return PREFIX + base64.b64encode(webp).decode("ascii")


class ChipCache:
    def __init__(self, cache_dir: Path):
        self.dir = Path(cache_dir)
        self.failed_path = self.dir / "failed.json"

    def path(self, det_id: str) -> Path:
        return self.dir / f"{det_id}.webp"

    def get(self, det_id: str) -> bytes | None:
        p = self.path(det_id)
        return p.read_bytes() if p.exists() else None

    def put(self, det_id: str, webp: bytes) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{det_id}.webp.tmp{os.getpid()}"
        tmp.write_bytes(webp)
        os.replace(tmp, self.path(det_id))

    def failed(self) -> dict:
        try:
            return json.loads(self.failed_path.read_text())
        except (OSError, ValueError):
            return {}

    def note_failed(self, items: dict) -> None:
        cur = self.failed()
        cur.update(items)
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.failed_path.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(cur, indent=1, sort_keys=True))
        os.replace(tmp, self.failed_path)

    def clear_failed(self, det_ids) -> None:
        cur = self.failed()
        if any(d in cur for d in det_ids):
            for d in det_ids:
                cur.pop(d, None)
            self.failed_path.write_text(json.dumps(cur, indent=1, sort_keys=True))


def read_chips(todo: pd.DataFrame, cache: ChipCache, log=print, io_threads: int = 4) -> tuple[int, dict]:
    """Read and cache the chips of `todo` (det_id, scene_id, row, col); returns (n written, {det_id: reason})."""
    os.environ.setdefault("GDAL_HTTP_RETRY_CODES", "404,429,500,502,503,504")
    os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "6")
    os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
    from rasterio.windows import Window

    from darkvessel.live.scene import LiveGRDScene
    from darkvessel.ml.chips import chips_db
    from darkvessel.ml.regional_verify import scene_path, tile_groups

    written, failed = 0, {}
    for scene_id, g in todo.groupby("scene_id", sort=False):
        t0 = time.time()
        rows, cols = g["row"].to_numpy(float), g["col"].to_numpy(float)
        ids = g["det_id"].astype(str).to_numpy()
        try:
            sc = LiveGRDScene(scene_path(str(scene_id)), io_threads=io_threads)
            groups = tile_groups(rows, cols, sc.shape)
        except Exception as exc:  # noqa: BLE001 (one scene failing never stops the build)
            for d in ids:
                failed[d] = f"scene open failed: {type(exc).__name__}: {str(exc)[:160]}"
            log(f"  chips: {scene_id}: open failed ({type(exc).__name__}); {len(ids)} chips skipped")
            continue
        n_ok = 0
        for sel, (r0, r1, c0, c1) in groups:
            try:
                win = Window(c0, r0, c1 - c0, r1 - r0)
                sig = {p: sc.read_sigma0(p, win, denoise=False) for p in ("VV", "VH")}
                ch = chips_db(sig, rows[sel], cols[sel], r0, c0)
            except Exception as exc:  # noqa: BLE001
                for d in ids[sel]:
                    failed[d] = f"window read failed: {type(exc).__name__}: {str(exc)[:160]}"
                continue
            for d, c in zip(ids[sel], ch):
                if not np.isfinite(c.astype(np.float32)).any():
                    failed[d] = "window holds no valid pixel"
                    continue
                cache.put(d, render(c))
                written += 1
                n_ok += 1
        try:
            if sc._pool is not None:
                sc._pool.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
        log(f"  chips: {scene_id[:32]}: {n_ok}/{len(ids)} in {time.time() - t0:.1f} s")
    return written, failed


def fill(candidates: pd.DataFrame, budget_bytes: int, cache: ChipCache, *, fetch: bool = True, log=print) -> tuple[dict, dict]:
    """Chips in candidate order until the next one would pass `budget_bytes` (bytes of the part's JSON object).

    candidates: det_id, scene_id, row, col, tier, in selection order. Missing chips are read in batches a little
    larger than what the remaining budget can hold. Returns ({det_id: data URI}, stats)."""
    chips: dict[str, str] = {}
    used = 2  # "{}"
    stats = {"candidates": int(len(candidates)), "embedded": 0, "left_out": 0, "failed": 0, "read": 0,
             "from_cache": 0, "by_tier": {}, "budget_bytes": int(budget_bytes)}
    failed: dict[str, str] = {}
    cand = candidates.reset_index(drop=True)
    n, pos, full = len(cand), 0, False
    while pos < n and not full:
        room = max(1, (budget_bytes - used) // 3000 + 10)  # chips the remaining budget can still hold, plus a margin
        window = cand.iloc[pos: pos + room]
        pos += len(window)
        have = {d: cache.get(d) for d in window["det_id"]}
        cached_before = {d for d, v in have.items() if v is not None}
        missing = window[[have[d] is None for d in window["det_id"]]]
        if fetch and len(missing):
            ok_rows = missing.dropna(subset=["scene_id", "row", "col"])
            for d in set(missing["det_id"]) - set(ok_rows["det_id"]):
                failed[d] = "no scene or pixel position"
            if len(ok_rows):
                w, f = read_chips(ok_rows, cache, log=log)
                stats["read"] += w
                failed.update(f)
            have = {d: cache.get(d) for d in window["det_id"]}
        for d, tier in zip(window["det_id"], window["tier"]):
            webp = have.get(d)
            if webp is None:
                stats["failed"] += int(d in failed)
                continue
            add = entry_bytes(d, webp)
            if used + add > budget_bytes:
                full = True
                break
            chips[d] = data_uri(webp)
            used += add
            stats["embedded"] += 1
            stats["from_cache"] += int(d in cached_before)
            stats["by_tier"][str(int(tier))] = stats["by_tier"].get(str(int(tier)), 0) + 1
    if failed:
        cache.note_failed({d: {"reason": v, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())} for d, v in failed.items()})
    cache.clear_failed(list(chips))
    stats["left_out"] = int(n - stats["embedded"])
    stats["bytes"] = int(used)
    return chips, stats
