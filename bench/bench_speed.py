"""End-to-end indexing time for each speed setting (gentle = one model, balanced/fast = governed
parallel model copies) on the same synthetic corpus, real models, fresh index each time.

    python bench\\bench_speed.py --docs 2000 [--speeds gentle,balanced,fast]
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
    ap.add_argument("--speeds", default="gentle,balanced,fast")
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="lfi-speed-"))
    os.environ.setdefault("LFI_MODELS", str(Path(os.environ["LOCALAPPDATA"]) / "LocalFileIntelligence" / "models"))
    from bench_index import build

    from lfi import config, db, library
    from lfi.config import Settings
    from lfi.embeddings import create_backend
    from lfi.indexer import Indexer
    from lfi.resources import governor

    config.enforce_offline_env()
    build(work / "corpus", a.docs)
    for speed in a.speeds.split(","):
        os.environ["LFI_HOME"] = str(work / f"home-{speed}")
        s = Settings()
        s.speed = speed
        s.image_search = False
        conn = db.connect()
        library.add_root(conn, str(work / "corpus"), validate=False)
        conn.close()
        backend = create_backend(s)  # loaded once, outside the timing (the app keeps it loaded)
        print(f"[{speed}] before: {governor(speed).describe()}", flush=True)
        t = time.perf_counter()
        st = Indexer(s, backend=backend).run()
        dt = time.perf_counter() - t
        print(f"[{speed}] {st.embedded_files} files in {dt:.1f} s = {st.embedded_files / dt:.1f} files/s; "
              f"model copies {max(1, st.gpu_workers)}, readers {st.reader_workers}, device {st.embed_device}", flush=True)
        del backend
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
