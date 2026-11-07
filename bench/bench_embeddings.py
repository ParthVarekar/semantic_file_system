"""Benchmark cached embedding models on this machine.

Measures load time, document throughput (bounded batches), query latency, peak memory, and a
small retrieval sanity check (does each query retrieve its intended synthetic document in the
top 3?). Uses only locally cached models (offline).

    .venv\\Scripts\\python bench\\bench_embeddings.py [--device auto|cpu|gpu] [--models a,b]
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lfi import config  # noqa: E402

config.enforce_offline_env()

from lfi.embeddings import CATALOG, FastEmbedBackend  # noqa: E402
from lfi.sysinfo import describe, rss_bytes  # noqa: E402

TOPICS = {
    "linear algebra lecture notes on eigenvalues and matrix diagonalization":
        "Lecture 7: Eigenvalues and eigenvectors. A square matrix A is diagonalizable when it has n "
        "linearly independent eigenvectors. We computed the characteristic polynomial det(A - lambda I).",
    "electricity bill payment receipt":
        "Monthly electricity statement. Amount due: 1,240.50. Payment received on 12 March via UPI. "
        "Consumer number 4471. Thank you for paying your bill on time.",
    "python script that resizes images":
        "from PIL import Image\nimport sys\n\ndef resize(path, width):\n    im = Image.open(path)\n"
        "    ratio = width / im.width\n    im.resize((width, int(im.height * ratio))).save(path)\n",
    "research paper about transformer attention":
        "Abstract. We propose a novel attention mechanism for transformer architectures that reduces the "
        "quadratic cost of self-attention. Experiments on machine translation benchmarks show BLEU gains.",
    "rental agreement contract for apartment":
        "This lease agreement is made between the landlord and the tenant for the apartment located at "
        "Flat 302. Monthly rent, security deposit and notice period terms are described below.",
    "resume of a software engineer":
        "Curriculum vitae. Software engineer with 4 years of experience in Python, React and cloud "
        "infrastructure. Education: B.Tech in Computer Science. Skills: Docker, Kubernetes, SQL.",
    "trip itinerary for goa vacation":
        "Day 1: arrive at Goa airport, check in to the beach resort. Day 2: visit Fort Aguada and Baga "
        "beach. Day 3: water sports and dinner cruise on the Mandovi river. Return flight on day 5.",
    "chemistry lab report on titration":
        "Experiment 4: acid-base titration. We titrated 25 mL of HCl against standardized NaOH using "
        "phenolphthalein indicator. The endpoint was reached at 23.4 mL. Molarity calculated as 0.094 M.",
}

FILLER = ("The committee reviewed the quarterly figures and noted several administrative changes "
          "regarding schedules, meeting rooms, and document retention policies for the department. ")


def corpus(n: int, seed: int = 7) -> list[str]:
    rnd = random.Random(seed)
    docs = list(TOPICS.values())
    while len(docs) < n:
        k = rnd.randint(4, 9)
        docs.append(" ".join(rnd.sample(FILLER.split(), k=len(FILLER.split()))) * 1 + " " + FILLER * k)
    return [d[:1200] for d in docs]


def bench(model: str, device: str, n_docs: int, batch: int) -> dict:
    cur0, _ = rss_bytes()
    t0 = time.perf_counter()
    b = FastEmbedBackend(model, device=device, batch_size=batch)
    load_s = time.perf_counter() - t0
    docs = corpus(n_docs)
    b.embed_documents(docs[:batch])  # warm-up (graph optimizations, GPU kernels)
    t1 = time.perf_counter()
    mat = b.embed_documents(docs)
    embed_s = time.perf_counter() - t1
    lat = []
    hits = 0
    for q in TOPICS:
        t = time.perf_counter()
        qv = b.embed_query(q)
        lat.append(time.perf_counter() - t)
        top = list((mat @ qv).argsort()[::-1][:3])
        hits += list(TOPICS).index(q) in top
    _, peak = rss_bytes()
    return {
        "model": model, "device": b.device, "fallback": b.fallback_reason, "dim": b.dim,
        "load_s": round(load_s, 2), "docs": n_docs, "batch": batch,
        "docs_per_s": round(n_docs / embed_s, 1), "query_ms_median": round(statistics.median(lat) * 1000, 1),
        "recall_at_3": f"{hits}/{len(TOPICS)}", "peak_rss_mb": round(peak / 2**20),
        "rss_growth_mb": round((peak - cur0) / 2**20),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="auto")
    ap.add_argument("--models", default=",".join(CATALOG))
    ap.add_argument("--docs", type=int, default=256)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--one", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.one:  # child process: isolates memory measurements per model
        try:
            print(json.dumps(bench(a.one, a.device, a.docs, a.batch)))
        except Exception as e:
            print(json.dumps({"model": a.one, "error": str(e)[:300]}))
        return
    print(json.dumps(describe()))
    for m in a.models.split(","):
        out = subprocess.run([sys.executable, __file__, "--one", m, "--device", a.device, "--docs", str(a.docs),
                              "--batch", str(a.batch)], capture_output=True, text=True)
        line = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        print(line[-1] if line else json.dumps({"model": m, "error": out.stderr[-400:]}), flush=True)


if __name__ == "__main__":
    main()
