"""Several copies of the embedding model in separate *processes* (one DirectML session each).

    python bench\\bench_parallel_proc.py [--procs 1,2,3] [--batch 32]

Two sessions in one process crash ONNX Runtime's DirectML provider (native crash, measured
2026-10-27), so parallel GPU embedding can only be done with one session per process. Each process
loads the model, waits at a barrier, then embeds its share; the time from barrier to the last
finish is measured (model loading excluded, it is a one-time cost).
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def worker(part, batch, barrier, out):
    from lfi import config
    from lfi.embeddings import FastEmbedBackend

    config.enforce_offline_env()
    b = FastEmbedBackend("BAAI/bge-small-en-v1.5", device="auto", batch_size=batch)
    b.embed_documents(part[:batch])
    barrier.wait()
    t = time.perf_counter()
    b.embed_documents(part)
    out.put((time.perf_counter(), b.device))
    _ = t


def main() -> None:
    from bench_parallel import texts, vram_mb

    ap = argparse.ArgumentParser()
    ap.add_argument("--procs", default="1,2,3")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--texts", type=int, default=3000)
    a = ap.parse_args()
    data = texts(a.texts)
    base = None
    for n in map(int, a.procs.split(",")):
        barrier, out = mp.Barrier(n + 1), mp.Queue()
        ps = [mp.Process(target=worker, args=(data[i::n], a.batch, barrier, out)) for i in range(n)]
        for p in ps:
            p.start()
        barrier.wait()
        t0 = time.perf_counter()
        vram = vram_mb()
        ends = [out.get(timeout=600) for _ in ps]
        dt = max(e for e, _ in ends) - t0
        for p in ps:
            p.join(30)
        rate = len(data) / dt
        base = base or rate
        crashed = [p.exitcode for p in ps if p.exitcode]
        print(f"processes={n} batch={a.batch} device={ends[0][1]} {rate:7.1f} chunks/s  x{rate / base:4.2f}  "
              f"VRAM while running ~{vram} MB  crashes={crashed}", flush=True)


if __name__ == "__main__":
    main()
