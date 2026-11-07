"""Does running several copies of the embedding model at once speed things up?

    python bench\\bench_parallel.py [--device auto] [--texts 3000]

Runs N independent ONNX sessions (each on its own thread, each with its own share of the texts)
for N = 1, 2, 3 and several batch sizes; reports chunks/s, speed-up over one session, and GPU
memory used (nvidia-smi, if present). Texts are realistic ~1,000-character chunks.
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def vram_mb() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.split()[0])
    except Exception:
        return None


def texts(n: int) -> list[str]:
    from bench_index import TOPICS, make_text

    rnd = random.Random(7)
    return [make_text(rnd, i % len(TOPICS), rnd.randint(120, 200))[:1200] for i in range(n)]


def run(sessions: int, batch: int, data: list[str], device: str) -> tuple[float, int | None, str]:
    from lfi.embeddings import FastEmbedBackend

    backends = [FastEmbedBackend("BAAI/bge-small-en-v1.5", device=device, batch_size=batch) for _ in range(sessions)]
    for b in backends:
        b.embed_documents(data[:batch])  # warm-up (graph compilation, allocation)
    peak = [vram_mb()]
    parts = [data[i::sessions] for i in range(sessions)]
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            v = vram_mb()
            if v is not None and (peak[0] is None or v > peak[0]):
                peak[0] = v
            time.sleep(0.5)

    w = threading.Thread(target=watch, daemon=True)
    w.start()
    t = time.perf_counter()
    threads = [threading.Thread(target=b.embed_documents, args=(p,)) for b, p in zip(backends, parts)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    dt = time.perf_counter() - t
    stop.set()
    return len(data) / dt, peak[0], backends[0].device


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="auto")
    ap.add_argument("--texts", type=int, default=3000)
    ap.add_argument("--sessions", default="1,2,3")
    ap.add_argument("--batches", default="32,64,128")
    a = ap.parse_args()
    from lfi import config

    config.enforce_offline_env()
    data = texts(a.texts)
    base = None
    print(f"idle VRAM: {vram_mb()} MB")
    for s in map(int, a.sessions.split(",")):
        for b in map(int, a.batches.split(",")):
            rate, vram, dev = run(s, b, data, a.device)
            base = base or rate
            print(f"sessions={s} batch={b:<4} device={dev:9s} {rate:7.1f} chunks/s  x{rate / base:4.2f}"
                  f"  peak VRAM {vram} MB", flush=True)


if __name__ == "__main__":
    main()
