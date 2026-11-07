"""Command-line interface over the same engine the desktop app uses.

    lfi roots add "D:\\Documents"        lfi index            lfi search "tax receipts 2023"
    lfi dupes                            lfi cleanup          lfi organize [--apply --yes]
    lfi quarantine list|add|restore ...  lfi history          lfi undo BATCH_ID
    lfi models list|download NAME        lfi recover          lfi status

Exit codes: 0 success, 1 some items failed (each is listed), 2 the command could not run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import multiprocessing
import os
import sys
import threading

from . import config, db, library


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def _safe_streams() -> None:
    """File names can contain any character. A console prints them fine, but redirected or piped
    output uses the ANSI code page on Windows and would crash mid-way: write UTF-8 there, and
    never fail on a character the stream cannot hold."""
    for st in (sys.stdout, sys.stderr):
        if st is None or not hasattr(st, "reconfigure"):
            continue
        try:
            if st.isatty():
                st.reconfigure(errors="backslashreplace")
            else:
                st.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            pass


def _backend(settings, required: bool = False):
    from .embeddings import ModelUnavailable, create_backend

    try:
        return create_backend(settings)
    except ModelUnavailable as e:
        if required:
            raise SystemExit(str(e))
        print(f"note: {e}\n      (continuing with keyword + filename search only)", file=sys.stderr)
        return None


def _vision(settings, announce: bool = False):
    from .vision import VISION_PAIRS, VisionBackend, pair_is_cached

    pair = VISION_PAIRS.get(settings.vision_pair)
    reason = None
    if not settings.image_search:
        reason = "image search is turned off in Settings"
    elif pair is None:
        reason = f"unknown image model '{settings.vision_pair}'"
    elif not pair_is_cached(pair.key):
        reason = f"image model {pair.key} is not downloaded (lfi models download-vision {pair.key})"
    if reason:
        if announce:
            print(f"note: {reason}; matching images by file name and folder only", file=sys.stderr)
        return None
    return VisionBackend(pair, load_image=False)


def _report(results) -> int:
    for r in results:
        print(f"  {'ok  ' if r.ok else 'FAIL'} {r.src} -> {r.dst}  {r.message}")
    return sum(not r.ok for r in results)


def cmd_roots(a, conn, s):
    failed = 0
    if a.action == "add":
        for p in a.paths:
            try:
                root_id = library.add_root(conn, p)
            except ValueError as e:
                print(f"error: {p}: {e}", file=sys.stderr)
                failed += 1
                continue
            added = conn.execute("SELECT path FROM roots WHERE id=?", (root_id,)).fetchone()[0]
            print(f"added {added}")
    elif a.action == "remove":
        for p in a.paths:
            if library.remove_root(conn, p):
                print(f"removed {p} from index (files on disk untouched)")
            else:
                print(f"error: {p} is not a selected folder", file=sys.stderr)
                failed += 1
    for r in library.roots(conn, enabled_only=False):
        print(f"  [{r['id']}] {r['path']}")
    return failed


def cmd_index(a, conn, s):
    from .indexer import Indexer

    if not library.roots(conn):
        print("error: no folders selected yet; add one with: lfi roots add <folder>", file=sys.stderr)
        return 1
    cancel = threading.Event()
    last = {"phase": None}

    def progress(phase, done, total, msg):
        if phase != last["phase"]:
            print(f"\n[{phase}]", end="", flush=True)
            last["phase"] = phase
        if total:
            print(f"\r[{phase}] {done}/{total}  ", end="", flush=True)

    from .indexer import IndexBusy

    ix = Indexer(s, progress=progress, cancel=cancel, embed=not a.no_embed)
    try:
        st = ix.run()
    except IndexBusy as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        cancel.set()
        print("\ncancelled; progress is saved and the next run resumes")
        return 1
    print()
    print(json.dumps({k: v for k, v in st.__dict__.items() if v not in (0, None, [], "", {})}, indent=2))
    return 0


def cmd_search(a, conn, s):
    from .search import SearchEngine

    vision = _vision(s, announce=a.scope == "images") if a.scope != "documents" else None
    se = SearchEngine(conn, _backend(s), vision)
    hits = se.search(" ".join(a.query), max(1, a.n), scope=a.scope)
    for p in se.last_problems:
        print(f"note: ignored filter - {p}", file=sys.stderr)
    for h in hits:
        print(f"{h.score:.4f}  {h.path}")
        print(f"        {'; '.join(h.reasons)}")
        if h.snippet:
            print(f"        {h.snippet[:160]}")
    return 0


def cmd_related(a, conn, s):
    from .safety import norm
    from .search import SearchEngine

    row = conn.execute("SELECT id FROM files WHERE path_key=?", (norm(a.path),)).fetchone()
    if not row:
        raise SystemExit("file is not indexed")
    for h in SearchEngine(conn, _backend(s, True)).related(row["id"], a.n):
        print(f"{h.score:.2f}  {h.path}\n        {'; '.join(h.reasons)}")
    return 0


def cmd_dupes(a, conn, s):
    from .duplicates import exact_groups, similar_image_groups

    total = 0
    for g in exact_groups(conn):
        total += g.reclaimable()
        print(f"\n{len(g.files)} identical copies, {_fmt_size(g.files[0].size)} each "
              f"(reclaimable {_fmt_size(g.reclaimable())})")
        for f in g.files:
            tag = "KEEP " if f.id == g.keeper_id else "     "
            print(f"  {tag}#{f.id} {f.path}   [{'; '.join(f.keeper_reasons)}]")
    print(f"\nExact duplicates reclaimable: {_fmt_size(total)}")
    for g in similar_image_groups(conn, s.image_hash_distance):
        print(f"\nNear-duplicate images: {g.evidence[0]}")
        for f in g.files:
            print(f"  {'KEEP ' if f.id == g.keeper_id else '     '}#{f.id} {f.path} ({f.width}x{f.height})")
    return 0


def cmd_cleanup(a, conn, s):
    from .cleanup import BASKETS, analyze

    rep = analyze(conn, s)
    for key, label, desc in BASKETS:
        if a.basket and key != a.basket:
            continue
        items = rep.baskets[key]
        print(f"\n== {label} ({len(items)}; {_fmt_size(rep.reclaimable(key))}) - {desc}")
        for c in items[: a.n]:
            print(f"  [{c.score:5.1f}] #{c.file_id} {c.path} ({_fmt_size(c.size)}, {c.confidence})")
            if a.explain:
                print("        " + c.explanation().replace("\n", "\n        "))
    print(f"\nPotentially reclaimable (unique files, excluding 'similar documents'): {_fmt_size(rep.reclaimable())}")
    if rep.hidden_by_preference:
        print(f"{rep.hidden_by_preference} candidate(s) hidden by your pins/exclusions")
    return 0


def cmd_organize(a, conn, s):
    from .fileops import FileOps
    from .organize import build_plan, group_folder

    plan = build_plan(conn, s, _backend(s))
    for key, g in plan.groups.items():
        ms = [m for m in plan.moves if m.group_key == key]
        if not ms:
            continue
        print(f"\n# {group_folder(g)}  ({len(ms)} files) - {'; '.join(g.reasons[:2])}")
        for m in ms:
            flag = "x" if m.selected else " "
            print(f"  [{flag}] {m.src}\n        -> {m.dst}   ({m.confidence}; {'; '.join(m.reasons[:2])}) {m.note}")
    print(f"\n{len(plan.kept)} file(s) stay where they are (already organized, projects, or pinned).")
    sel = plan.selected()
    if not a.apply:
        print(f"Preview only. {len(sel)} move(s) selected. Re-run with --apply --yes to perform them.")
        return 0
    if not a.yes:
        raise SystemExit("refusing to move files without --yes")
    ops = FileOps(conn, s, library.scope_policy(conn, s))
    res = ops.move_files([(m.file_id, m.dst) for m in sel], "organize (cli)")
    print(f"batch {res.batch_id}: moved {len(res.ok)}, failed {len(res.failed)}")
    for r in res.failed:
        print(f"  FAILED {r.src}: {r.message}")
    print(f"Undo with: lfi undo {res.batch_id}")
    return len(res.failed)


def _file_ids(conn, items: list[str]) -> list[int]:
    """File ids as printed by dupes/cleanup (#123 or 123), or indexed file paths."""
    from .safety import norm

    out = []
    for x in items:
        v = x.lstrip("#")
        if v.isdigit():
            out.append(int(v))
            continue
        row = conn.execute("SELECT id FROM files WHERE path_key=?", (norm(x),)).fetchone()
        if row is None:
            raise ValueError(f"'{x}' is neither a file id nor an indexed file path")
        out.append(row["id"])
    return out


def cmd_quarantine(a, conn, s):
    from .fileops import FileOps

    ops = FileOps(conn, s, library.scope_policy(conn, s))
    if a.action == "list":
        for q in ops.quarantined():
            when = dt.datetime.fromtimestamp(q["created_at"]).strftime("%Y-%m-%d %H:%M")
            print(f"  [{q['id']}] {when}  {q['src']}  ({_fmt_size(q['size'] or 0)})")
        return 0
    if not a.ids:
        raise ValueError(f"quarantine {a.action} needs at least one id")
    if a.action == "add":
        return _report(ops.quarantine(_file_ids(conn, a.ids), "quarantined from CLI").results)
    ids = [int(x.lstrip("#")) for x in a.ids if x.lstrip("#").isdigit()]
    if len(ids) != len(a.ids):
        raise ValueError("restore takes the numbers shown by 'lfi quarantine list'")
    return _report(ops.restore(ids, on_conflict=a.on_conflict).results)


def cmd_history(a, conn, s):
    from .fileops import FileOps

    for b in FileOps(conn, s, library.scope_policy(conn, s)).history():
        when = dt.datetime.fromtimestamp(b["created_at"]).strftime("%Y-%m-%d %H:%M")
        print(f"  batch {b['id']:>4}  {when}  {b['kind']:<10} {b['status']:<16} {b['n_done']}/{b['n_ops']}  "
              f"{b['description'] or ''}")
    return 0


def cmd_undo(a, conn, s):
    from .fileops import FileOps

    return _report(FileOps(conn, s, library.scope_policy(conn, s)).undo_batch(a.batch).results)


def cmd_recover(a, conn, s):
    from .fileops import FileOps

    report = FileOps(conn, s, library.scope_policy(conn, s)).recover()
    print("\n".join(report) if report else "nothing to recover")
    return 0


def cmd_models(a, conn, s):
    from .embeddings import CATALOG, download_model, model_is_cached
    from .vision import VISION_PAIRS, download_pair, pair_is_cached

    if a.action == "download":
        if not a.name:
            raise ValueError("name the model to download, e.g.: lfi models download \"BAAI/bge-small-en-v1.5\" "
                             "(see: lfi models list)")
        print(f"Downloading {a.name} from Hugging Face into {config.models_dir()} (one-time, needs internet)...")
        try:
            download_model(a.name)
        except Exception as e:
            raise ValueError(f"download failed: {str(e).splitlines()[0] if str(e) else type(e).__name__}")
        print("done; the model now works offline")
        return 0
    if a.action == "download-vision":
        key = a.name or s.vision_pair
        if key not in VISION_PAIRS:
            raise ValueError(f"unknown image model '{key}'; choose one of: {', '.join(VISION_PAIRS)}")
        print(f"Downloading image model '{key}' (~{VISION_PAIRS[key].size_mb} MB) from Hugging Face...")
        try:
            download_pair(key)
        except Exception as e:
            raise ValueError(f"download failed: {str(e).splitlines()[0] if str(e) else type(e).__name__}")
        print("done; image search now works offline")
        return 0
    print("Image search models (download with: lfi models download-vision <key>):")
    for vp in VISION_PAIRS.values():
        mark = "cached" if pair_is_cached(vp.key) else "not downloaded"
        cur = " (selected)" if vp.key == s.vision_pair else ""
        print(f"  {vp.key}{cur}: {vp.image_model} + {vp.text_model}")
        print(f"      ~{vp.size_mb} MB, {vp.license}; {mark}. {vp.note}")
    print("Document models:")
    for m in CATALOG.values():
        mark = "cached" if model_is_cached(m.name) else "not downloaded"
        cur = " (selected)" if m.name == s.embedding_model else ""
        print(f"  {m.name}{cur}\n      {m.dim}-d, ~{m.size_mb} MB, {m.license}, {m.languages}; {mark}. {m.note}")
    return 0


def cmd_status(a, conn, s):
    from .sysinfo import describe

    n = conn.execute("SELECT count(*) AS n, coalesce(sum(size),0) AS b FROM files").fetchone()
    by_stage = dict(conn.execute("SELECT stage, count(*) FROM files GROUP BY stage").fetchall())
    space = library.active_space(conn)
    print(f"data folder: {config.app_home()}")
    print(f"files indexed: {n['n']} ({_fmt_size(n['b'])}); stages: {by_stage}")
    print(f"embedding space: {space['model'] + ' / ' + str(space['dim']) + '-d' if space else 'none yet'}")
    ispace = library.active_image_space(conn)
    nimg = conn.execute("SELECT count(*) FROM files WHERE img_stage=1").fetchone()[0]
    print(f"image search: {ispace['model'] + f' ({nimg} images understood)' if ispace else 'not built yet'}")
    print(f"sqlite-vec: {'loaded' if db.vec_available() else 'unavailable (NumPy fallback)'}")
    print(f"system: {describe()}")
    return 0


def cmd_clear(a, conn, s):
    if not a.yes:
        raise SystemExit("this removes indexed data (not your files); re-run with --yes")
    if a.text_only:
        db.clear_text_cache(conn)
        print("extracted text removed; metadata, hashes and vectors kept")
    else:
        db.clear_index(conn)
        print("index cleared; quarantine records and operation history kept")
    return 0


def cmd_demo(a, conn, s):
    from pathlib import Path

    from .synthetic import build_corpus

    build_corpus(Path(a.dir))
    print(f"synthetic demo corpus created at {os.path.abspath(a.dir)}")
    return 0


def main(argv: list[str] | None = None) -> None:
    multiprocessing.freeze_support()
    _safe_streams()
    config.enforce_offline_env()
    from .cleanup import BASKETS
    from .fileops import FileOps, OpRefused

    p = argparse.ArgumentParser(prog="lfi", description="Local File Intelligence (offline)",
                                epilog="Exit codes: 0 success, 1 some items failed, 2 the command could not run.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("roots", help="list, add or remove the folders to index")
    r.add_argument("action", choices=["list", "add", "remove"])
    r.add_argument("paths", nargs="*")
    i = sub.add_parser("index", help="index the selected folders (read-only; resumable)")
    i.add_argument("--no-embed", action="store_true")
    q = sub.add_parser("search", help="search by keywords or a description, with filters like ext:pdf")
    q.add_argument("query", nargs="+")
    q.add_argument("-n", type=int, default=10)
    q.add_argument("--scope", choices=["all", "documents", "images"], default="all")
    rl = sub.add_parser("related", help="files similar to one file")
    rl.add_argument("path")
    rl.add_argument("-n", type=int, default=10)
    sub.add_parser("dupes", help="exact duplicates and near-duplicate images")
    c = sub.add_parser("cleanup", help="cleanup review baskets with review-priority scores")
    c.add_argument("--basket", choices=[k for k, _, _ in BASKETS])
    c.add_argument("-n", type=int, default=15)
    c.add_argument("--explain", action="store_true")
    o = sub.add_parser("organize", help="preview (or apply) an organization plan")
    o.add_argument("--apply", action="store_true")
    o.add_argument("--yes", action="store_true")
    qa = sub.add_parser("quarantine", help="list quarantined files, quarantine files (#id or path), restore them")
    qa.add_argument("action", choices=["list", "add", "restore"])
    qa.add_argument("ids", nargs="*", help="add: file ids shown as #123 by dupes/cleanup, or indexed paths; "
                                           "restore: numbers from 'quarantine list'")
    qa.add_argument("--on-conflict", choices=["rename", "skip"], default="rename",
                    help="restore when the original path is occupied: 'name (restored).ext' or leave it in quarantine")
    sub.add_parser("history", help="file-operation batches")
    u = sub.add_parser("undo", help="undo an organize batch")
    u.add_argument("batch", type=int)
    sub.add_parser("recover", help="reconcile operations interrupted by a crash")
    m = sub.add_parser("models", help="list or download local models")
    m.add_argument("action", choices=["list", "download", "download-vision"])
    m.add_argument("name", nargs="?")
    sub.add_parser("status", help="index and system summary")
    cl = sub.add_parser("clear-index", help="remove indexed data (never your files)")
    cl.add_argument("--text-only", action="store_true")
    cl.add_argument("--yes", action="store_true")
    dm = sub.add_parser("demo", help="create a synthetic demo folder to try things safely")
    dm.add_argument("dir")
    a = p.parse_args(argv)
    s = config.Settings.load()
    conn = db.connect()
    if a.cmd not in ("recover", "demo"):
        report = FileOps(conn, s, library.scope_policy(conn, s)).recover()
        for line in report:
            print(f"recovery: {line}", file=sys.stderr)
    try:
        failed = globals()[f"cmd_{a.cmd.replace('-index', '').replace('-', '_')}"](a, conn, s)
    except (OpRefused, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
