"""End-to-end indexing benchmark on a generated mixed corpus (synthetic, reproducible).

    python bench\\bench_index.py --docs 2000 --model BAAI/bge-small-en-v1.5 --device auto

Reports files/s, per-phase seconds, incremental re-scan time, hybrid search latency, index
size per file, and peak memory. Uses only locally cached models.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TOPICS = [
    ("linear algebra", "eigenvalues eigenvectors matrix determinant vector space basis rank"),
    ("organic chemistry", "alkane alkene reaction mechanism nucleophile titration molarity"),
    ("machine learning", "neural network gradient descent loss function overfitting transformer"),
    ("personal finance", "bank statement salary tax invoice receipt payment rent insurance"),
    ("travel", "itinerary flight hotel beach museum train booking passport visa"),
    ("cooking", "recipe paneer spices oven marinate yogurt flour bake simmer"),
    ("software", "install configure server database api deployment docker logs"),
    ("history", "empire dynasty revolution treaty century archaeology monument"),
]


def make_text(rnd: random.Random, topic: int, n_words: int) -> str:
    name, words = TOPICS[topic]
    vocab = words.split() + "the of and to in is was for on with as by this that report notes".split()
    sents = []
    for _ in range(n_words // 12):
        sents.append(" ".join(rnd.choice(vocab) for _ in range(12)).capitalize() + ".")
    return f"{name.title()} notes.\n\n" + " ".join(sents)


def build(root: Path, n: int, seed: int = 1) -> None:
    from lfi import synthetic

    rnd = random.Random(seed)
    for i in range(n):
        topic = rnd.randrange(len(TOPICS))
        d = root / f"folder_{i // 100:02d}"
        d.mkdir(parents=True, exist_ok=True)
        text = make_text(rnd, topic, rnd.randint(150, 900))
        r = rnd.random()
        if r < 0.35:
            (d / f"note_{i}.txt").write_text(text, encoding="utf-8")
        elif r < 0.5:
            (d / f"note_{i}.md").write_text("# " + text, encoding="utf-8")
        elif r < 0.7:
            synthetic.make_docx(d / f"doc_{i}.docx", text.split(". "))
        elif r < 0.85:
            lines = text.split(". ")
            synthetic.make_pdf(d / f"paper_{i}.pdf", [lines[j:j + 40] for j in range(0, len(lines), 40)])
        elif r < 0.92:
            synthetic.make_xlsx(d / f"sheet_{i}.xlsx", [["item", "amount"]] + [[w, rnd.randint(1, 999)]
                                                                               for w in text.split()[:60]])
        else:
            synthetic.make_image(d / f"img_{i}.jpg", i, (rnd.choice([640, 1024]), 480))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=2000)
    ap.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="lfi-bench-"))
    os.environ["LFI_HOME"] = str(work / "home")
    os.environ.setdefault("LFI_MODELS", str(Path(os.environ["LOCALAPPDATA"]) / "LocalFileIntelligence" / "models"))
    from lfi import config, db, library
    from lfi.embeddings import create_backend
    from lfi.indexer import Indexer
    from lfi.search import SearchEngine
    from lfi.sysinfo import describe, rss_bytes

    config.enforce_offline_env()
    corpus = work / "corpus"
    t = time.perf_counter()
    build(corpus, a.docs)
    build_s = time.perf_counter() - t
    total_bytes = sum(f.stat().st_size for f in corpus.rglob("*") if f.is_file())
    s = config.Settings()
    s.embedding_model, s.device = a.model, a.device
    conn = db.connect()
    library.add_root(conn, str(corpus), validate=False)
    backend = create_backend(s)
    t = time.perf_counter()
    st = Indexer(s, backend=backend).run()
    full_s = time.perf_counter() - t
    t = time.perf_counter()
    st2 = Indexer(s, backend=backend).run()
    inc_s = time.perf_counter() - t
    se = SearchEngine(conn, backend)
    queries = ["eigenvalues of a matrix", "how much tax did I pay", "neural network training", "beach hotel trip",
               "paneer recipe", "docker deployment logs", "ancient empire history", "titration lab",
               "salary slip", "ext:pdf machine learning"] * 3
    lat = []
    for q in queries:
        t = time.perf_counter()
        se.search(q, 20)
        lat.append(time.perf_counter() - t)
    db_bytes = sum(p.stat().st_size for p in config.db_path().parent.glob("index.sqlite3*"))
    _, peak = rss_bytes()
    chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    out = {
        "system": describe(), "model": a.model, "device": backend.device, "fallback": backend.fallback_reason,
        "files": st.discovered, "corpus_mb": round(total_bytes / 2**20, 1), "chunks": chunks,
        "full_index_s": round(full_s, 1), "files_per_s": round(st.discovered / full_s, 1),
        "phases_s": st.phase_s, "embedded_files": st.embedded_files, "errors": st.errors,
        "incremental_rescan_s": round(inc_s, 2), "incremental_processed": st2.processed,
        "search_ms_p50": round(statistics.median(lat) * 1000, 1),
        "search_ms_p95": round(sorted(lat)[int(len(lat) * 0.95) - 1] * 1000, 1),
        "index_db_mb": round(db_bytes / 2**20, 1), "index_kb_per_file": round(db_bytes / 1024 / st.discovered, 1),
        "peak_rss_mb": round(peak / 2**20), "corpus_build_s": round(build_s, 1),
    }
    print(json.dumps(out, indent=2))
    conn.close()
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
