"""Where does indexing time go? Times each stage on its own over the same synthetic corpus.

    python bench\\bench_stages.py --docs 2000 [--workers 2,4,6,8]

1. extraction only (reading + hashing + text, in the isolated worker processes), per worker count
2. embedding only (the GPU/CPU model over the chunks produced by 1)
The slower of the two bounds the whole pipeline, since they run concurrently.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=2000)
    ap.add_argument("--workers", default="2,4,6,8")
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="lfi-stages-"))
    os.environ["LFI_HOME"] = str(work / "home")
    os.environ.setdefault("LFI_MODELS", str(Path(os.environ["LOCALAPPDATA"]) / "LocalFileIntelligence" / "models"))
    from bench_index import build

    from lfi import config, db, library
    from lfi.config import Settings
    from lfi.embeddings import create_backend
    from lfi.indexer import Indexer, embed_pending

    config.enforce_offline_env()
    build(work / "corpus", a.docs)
    for w in map(int, a.workers.split(",")):
        shutil.rmtree(work / "home", ignore_errors=True)
        s = Settings()
        s.extract_workers = w
        s.image_search = False
        conn = db.connect()
        library.add_root(conn, str(work / "corpus"), validate=False)
        conn.close()
        t = time.perf_counter()
        st = Indexer(s, embed=False).run()
        dt = time.perf_counter() - t
        print(f"extraction  workers={w}: {st.processed / dt:6.1f} files/s ({dt:.1f} s)", flush=True)
    s = Settings()
    backend = create_backend(s)
    conn = db.connect()
    chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    t = time.perf_counter()
    n = embed_pending(s, backend=backend).embedded_files
    dt = time.perf_counter() - t
    print(f"embedding   {backend.device}: {n / dt:6.1f} files/s, {chunks / dt:6.1f} chunks/s ({dt:.1f} s, "
          f"{chunks} chunks)", flush=True)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
