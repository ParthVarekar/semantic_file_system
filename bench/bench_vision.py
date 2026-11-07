"""Benchmark local image-text (vision-language) embedding pairs for description-based image search.

    python bench\\bench_vision.py [--device cpu|auto] [--pairs nomic-q,clip-b32]

Quality: 12 synthetic images with recognizable content (code screenshot, receipt, charts,
document page, spreadsheet, sunset, stars, meadow, QR code, map, ...) named IMG_0000.png so only
pixels can match, plus 30 random distractor images. Each semantic image has one *vague*
description; we report recall@1 / recall@3 among all 42 images.
Speed: 64 photo-sized JPEGs (1600x1200) - images/s including JPEG decode and resize.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def run_one(pair: str, device: str, batch: int) -> dict:
    import numpy as np

    from lfi import config, synthetic
    from lfi.sysinfo import rss_bytes
    from lfi.vision import VISION_PAIRS, VisionBackend

    config.enforce_offline_env()
    work = Path(tempfile.mkdtemp(prefix="lfi-vis-"))
    sem = synthetic.make_semantic_images(work / "sem")
    for i in range(30):
        synthetic.make_image(work / "sem" / f"IMG_9{i:03d}.jpg", 100 + i)
    photos = work / "photos"
    photos.mkdir()
    for i in range(64):
        synthetic.make_image(photos / f"p{i}.jpg", 500 + i, (1600, 1200))
    t = time.perf_counter()
    vb = VisionBackend(VISION_PAIRS[pair], device=device, batch_size=batch)
    load_s = time.perf_counter() - t
    paths = sorted((work / "sem").iterdir())
    mat = vb.embed_image_paths([str(p) for p in paths])
    index = {str(p): i for i, p in enumerate(paths)}
    r1 = r3 = 0
    lat = []
    detail = {}
    for key, (p, q) in sem.items():
        t = time.perf_counter()
        qv = vb.embed_text(q)
        lat.append(time.perf_counter() - t)
        order = list(np.argsort(-(mat @ qv)))
        rank = order.index(index[str(p)])
        r1 += rank == 0
        r3 += rank < 3
        detail[key] = rank + 1
    ph = [str(p) for p in sorted(photos.iterdir())]
    vb.embed_image_paths(ph[:batch])  # warm-up
    t = time.perf_counter()
    vb.embed_image_paths(ph)
    ips = len(ph) / (time.perf_counter() - t)
    _, peak = rss_bytes()
    return {"pair": pair, "device": vb.device, "fallback": vb.fallback_reason, "dim": vb.dim,
            "download_mb": VISION_PAIRS[pair].size_mb, "load_s": round(load_s, 1),
            "recall@1": f"{r1}/{len(sem)}", "recall@3": f"{r3}/{len(sem)}", "rank_of_target": detail,
            "images_per_s": round(ips, 1), "query_ms": round(statistics.median(lat) * 1000, 1),
            "peak_rss_mb": round(peak / 2**20)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--pairs", default="nomic-q,clip-b32")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--one", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.one:
        try:
            print(json.dumps(run_one(a.one, a.device, a.batch)))
        except Exception as e:
            print(json.dumps({"pair": a.one, "error": f"{type(e).__name__}: {str(e)[:300]}"}))
        return
    for p in a.pairs.split(","):
        out = subprocess.run([sys.executable, __file__, "--one", p, "--device", a.device, "--batch", str(a.batch)],
                             capture_output=True, text=True)
        line = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        print(line[-1] if line else json.dumps({"pair": p, "error": out.stderr[-500:]}), flush=True)


if __name__ == "__main__":
    main()
