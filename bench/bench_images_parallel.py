"""Image phase: one image worker vs governed workers (up to 3), real nomic-q model on the CPU.

    python bench\\bench_images_parallel.py --photos 300
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", type=int, default=300)
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="lfi-imgpar-"))
    os.environ.setdefault("LFI_MODELS", str(Path(os.environ["LOCALAPPDATA"]) / "LocalFileIntelligence" / "models"))
    from lfi import config, db, library, resources, synthetic
    from lfi.config import Settings
    from lfi.embeddings import create_backend
    from lfi.indexer import Indexer

    config.enforce_offline_env()
    (work / "photos").mkdir(parents=True)
    for i in range(a.photos):
        synthetic.make_image(work / "photos" / f"IMG_{i:04d}.jpg", 1000 + i, (1600, 1200))
    for label, cap in (("1 worker", 1), ("governed", 3)):
        resources.COSTS["image"].maximum = cap
        os.environ["LFI_HOME"] = str(work / f"home-{cap}")
        s = Settings()
        conn = db.connect()
        library.add_root(conn, str(work / "photos"), validate=False)
        conn.close()
        backend = create_backend(s)
        t = time.perf_counter()
        st = Indexer(s, backend=backend).run()
        dt = time.perf_counter() - t
        print(f"{label:9s}: {st.images_embedded} images in {dt:.1f} s = {st.images_embedded / dt:.1f} images/s, "
              f"image workers {st.image_workers}, images phase {st.phase_s.get('images', 0):.1f} s", flush=True)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
