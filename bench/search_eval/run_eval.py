"""Search-quality evaluation: 75 vague queries with graded answers over a synthetic library.

    python bench\\search_eval\\run_eval.py                         # build + index once, then evaluate
    python bench\\search_eval\\run_eval.py --set search.SEMANTIC_MIN_Z=2.5 --set search.RRF_K=30
    python bench\\search_eval\\run_eval.py --detail               # per-query results
    python bench\\search_eval\\run_eval.py --rebuild              # regenerate the corpus and index

Everything it creates (corpus, index, manifest) goes to bench\\search_eval\\work (or LFI_EVAL_DIR).
It uses the real models from the normal model cache, read-only (or LFI_MODELS), on the CPU.

Queries are split into a *tuning* half and a *held-out* half (queries_def.split). Tune on the
first, and keep a change only if the held-out half improves too - otherwise you are fitting this
particular test set. Metrics, per scope: MRR@10 (where the first right answer lands), recall@k
(share of right answers in the top k), nDCG@10 (graded ranking quality), n (results returned),
prec_all (share of returned results that are right) and, for queries with no right answer,
junk_n (how many results were returned anyway).
"""

from __future__ import annotations

import argparse
import ast
import importlib
import json
import math
import os
import shutil
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))
WORK = Path(os.environ.get("LFI_EVAL_DIR", HERE / "work"))
# The evaluation gets its own index; the models come from the normal cache, read-only.
os.environ.setdefault("LFI_MODELS", str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LocalFileIntelligence" / "models"))
os.environ["LFI_HOME"] = str(WORK / "home")
os.environ.setdefault("OMP_NUM_THREADS", "4")


# ------------------------------------------------------------------------------------ corpus
def _paras(s: str) -> list[str]:
    return [" ".join(p.split()) for p in s.strip().split("\n\n") if p.strip()]


def _write_doc(path: Path, content) -> None:
    from lfi import synthetic

    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    if ext == ".pdf":
        lines: list[str] = []
        for p in content.strip().split("\n"):
            if p.strip():
                lines += textwrap.wrap(p.strip(), 95) or [""]
        synthetic.make_pdf(path, [lines[i:i + 48] for i in range(0, len(lines), 48)])
    elif ext == ".docx":
        synthetic.make_docx(path, _paras(content))
    elif ext == ".pptx":
        synthetic.make_pptx(path, content)
    elif ext == ".xlsx":
        synthetic.make_xlsx(path, content)
    else:
        path.write_text(content.strip() + "\n", encoding="utf-8")


def build_corpus(root: Path) -> dict[str, str]:
    """Write every document and picture; returns id -> path relative to ``root``."""
    from corpus_def import DOCS
    from scenes import DISTRACTORS, SCENES

    from lfi import synthetic

    man: dict[str, str] = {}
    for did, rel, content in DOCS:
        _write_doc(root / rel, content)
        man[did] = rel
    for key, (p, _q) in synthetic.make_semantic_images(root / "Pictures" / "Phone").items():
        man["sem_" + key] = str(p.relative_to(root)).replace("\\", "/")
    for sid, (fn, rel) in SCENES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        im = fn()
        im.save(p, "JPEG", quality=90) if p.suffix.lower() == ".jpg" else im.save(p)
        man[sid] = rel
    for i, rel in enumerate(DISTRACTORS):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        synthetic.make_image(p, 900 + i, (1024, 768))
        man[f"distimg_{i}"] = rel
    return man


def prepare(rebuild: bool) -> dict[str, str]:
    man_path = WORK / "manifest.json"
    if man_path.exists() and not rebuild:
        return json.loads(man_path.read_text(encoding="utf-8"))
    from lfi import config, db, library
    from lfi.config import Settings
    from lfi.embeddings import create_backend
    from lfi.indexer import Indexer

    if WORK.exists():
        shutil.rmtree(WORK)
    (WORK / "corpus").mkdir(parents=True)
    config.enforce_offline_env()
    man = build_corpus(WORK / "corpus")
    s = Settings()
    s.device, s.embed_threads, s.vision_threads, s.extract_workers = "cpu", 4, 4, 2
    conn = db.connect()
    library.add_root(conn, str(WORK / "corpus"), validate=False)
    conn.close()
    print(f"indexing {len(man)} files with the real models (one-time, a few minutes)...", flush=True)
    st = Indexer(s, backend=create_backend(s)).run()
    print(f"  embedded {st.embedded_files} files, {st.images_embedded} images, errors {st.errors}")
    if st.embed_error or st.image_error:
        raise SystemExit(f"models unavailable: {st.embed_error or st.image_error}")
    man_path.write_text(json.dumps(man, indent=1), encoding="utf-8")
    return man


# ------------------------------------------------------------------------------------ metrics
def scopes_for(kind: str) -> tuple[str, str]:
    return {"doc": ("documents", "all"), "img": ("images", "all"),
            "none-doc": ("documents", "all"), "none-img": ("images", "all")}[kind]


def metrics_one(ranked: list[str], rel: dict[str, int]) -> dict[str, float]:
    top = ranked[:10]
    relset = {k for k, g in rel.items() if g >= 1}
    rr = next((1.0 / (i + 1) for i, x in enumerate(top) if x in relset), 0.0)
    dcg = sum((2 ** rel.get(x, 0) - 1) / math.log2(i + 2) for i, x in enumerate(top))
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(sorted(rel.values(), reverse=True)[:10]))
    n = len(ranked)
    return {"mrr": rr, **{f"r{k}": len(relset & set(ranked[:k])) / len(relset) for k in (1, 5, 10)},
            "ndcg": dcg / idcg if idcg else 0.0, "n": n,
            "prec_all": sum(x in relset for x in ranked) / n if n else 0.0}


def evaluate(search, queries) -> dict[str, dict[str, float]]:
    acc: dict[str, dict[str, list]] = {}
    for q in queries:
        for sc in scopes_for(q["kind"]):
            ids = search(q["text"], sc)
            slot = acc.setdefault(sc, {"m": [], "none": []})
            if q["kind"].startswith("none"):
                slot["none"].append(len(ids))
            else:
                slot["m"].append(metrics_one(ids, q["rel"]))
    out = {}
    for sc, d in acc.items():
        res = {k: sum(m[k] for m in d["m"]) / len(d["m"]) for k in d["m"][0]} if d["m"] else {}
        if d["none"]:
            res["junk_n"] = sum(d["none"]) / len(d["none"])
        out[sc] = res
    return out


def fmt(res: dict, keys=("mrr", "r1", "r5", "r10", "ndcg", "n", "prec_all", "junk_n")) -> str:
    return "\n".join(f"  {sc:9s} " + "  ".join(f"{k}={res[sc][k]:.3f}" for k in keys if k in res[sc])
                     for sc in ("all", "documents", "images") if sc in res)


def apply_overrides(items: list[str]) -> None:
    """--set search.SEMANTIC_MIN_Z=2.5 sets lfi.search.SEMANTIC_MIN_Z for this run only."""
    for it in items:
        target, value = it.split("=", 1)
        mod, name = target.rsplit(".", 1)
        m = importlib.import_module(mod if mod.startswith("lfi.") else "lfi." + mod)
        if not hasattr(m, name):
            raise SystemExit(f"{mod} has no setting {name}")
        setattr(m, name, ast.literal_eval(value))
        print(f"override: {m.__name__}.{name} = {getattr(m, name)!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--set", action="append", default=[], metavar="MODULE.NAME=VALUE")
    ap.add_argument("--detail", action="store_true")
    a = ap.parse_args()
    man = prepare(a.rebuild)
    apply_overrides(a.set)
    from queries_def import Q, split

    from lfi import config, db
    from lfi.config import Settings
    from lfi.embeddings import create_backend
    from lfi.search import SearchEngine
    from lfi.vision import VISION_PAIRS, VisionBackend

    config.enforce_offline_env()
    s = Settings()
    s.device = "cpu"
    engine = SearchEngine(db.connect(), create_backend(s), VisionBackend(VISION_PAIRS[s.vision_pair], load_image=False))
    root = WORK / "corpus"
    id_of = {os.path.normcase(str(root / rel)): k for k, rel in man.items()}

    def search(q: str, scope: str) -> list[str]:
        return [id_of.get(os.path.normcase(h.path), "?") for h in engine.search(q, 200, scope=scope)]

    tune, held = split()
    print(f"{len(Q)} queries: tuning {len(tune)}, held-out {len(held)}")
    for name, qs in (("TUNING HALF", tune), ("HELD-OUT HALF", held)):
        print(f"== {name}\n{fmt(evaluate(search, qs))}")
    if a.detail:
        print("\nper query: id | scope | first right answer at | top 5")
        for q in Q:
            for sc in scopes_for(q["kind"]):
                ids = search(q["text"], sc)
                rank = next((i + 1 for i, x in enumerate(ids) if q["rel"].get(x)), None)
                print(f"  {q['id']:16s} {sc:9s} {('-' if rank is None else rank)!s:>3}  {ids[:5]}")


if __name__ == "__main__":
    main()
