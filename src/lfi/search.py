"""Hybrid retrieval: SQLite FTS5 BM25 (content + names) fused with vector KNN using
Reciprocal Rank Fusion (Cormack, Clarke & Buettcher, SIGIR 2009), plus metadata filters.

Query syntax (all optional, combinable with free text):
    ext:pdf  kind:image  after:2024-01-31  before:2023  larger:10MB  smaller:1GB  in:Downloads
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field

import numpy as np

from . import library
from .embeddings import EmbeddingBackend
from .textutil import STOPWORDS, keywords, words

RRF_K = 60
SEMANTIC_RELATIVE_FLOOR = 0.85
# A result supported only by embedding similarity must also stand out from the library: z-score
# of its similarity against the query's similarity to a sample of all files. Tuned with
# bench/search_eval (held-out half, scope All: MRR 0.775 -> 0.793, recall@10 0.833 -> 0.924,
# results for queries with no answer 46 -> 19). Skipped for tiny libraries (noisy statistics).
SEMANTIC_MIN_Z = 2.0
SEMANTIC_Z_MIN_FILES = 30
_FILTER = re.compile(r'(\w+):("[^"]+"|\S+)')
_UNITS = {"b": 1, "kb": 1024, "k": 1024, "mb": 1024**2, "m": 1024**2, "gb": 1024**3, "g": 1024**3}


KINDS = frozenset({"document", "image", "video", "audio", "archive", "installer", "code", "data", "temporary",
                   "other"})
_KIND_ALIASES = {
    "images": "image", "img": "image", "photo": "image", "photos": "image", "picture": "image", "pictures": "image",
    "pics": "image", "screenshot": "image", "screenshots": "image", "doc": "document", "docs": "document",
    "documents": "document", "videos": "video", "movie": "video", "movies": "video", "music": "audio",
    "song": "audio", "songs": "audio", "archives": "archive", "installers": "installer", "temp": "temporary",
}
_MAX_INT = 2**62
# Above this many filter-matching files, vector search takes a wide top-k and filters it instead
# of scoring every candidate exactly.
EXACT_FILTERED_MAX = 4000


@dataclass
class Filters:
    exts: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    after_ns: int | None = None
    before_ns: int | None = None
    min_size: int | None = None
    max_size: int | None = None
    folder: str | None = None
    problems: list[str] = field(default_factory=list)  # filters that were recognised but not understood

    def active(self) -> bool:
        return bool(self.exts or self.kinds or self.folder or self.after_ns is not None
                    or self.before_ns is not None or self.min_size is not None or self.max_size is not None)


@dataclass
class SearchHit:
    file_id: int
    path: str
    name: str
    kind: str
    size: int
    mtime_ns: int
    score: float
    snippet: str = ""
    semantic: float | None = None
    reasons: list[str] = field(default_factory=list)
    visual: float | None = None  # z-score of image-content match, if any


def _parse_size(s: str) -> int | None:
    m = re.fullmatch(r"(\d{1,15}(?:\.\d{1,6})?)\s*([kmg]?b?|[kmg])", s.lower())
    if not m:
        return None
    return min(int(float(m.group(1)) * _UNITS.get(m.group(2) or "b", 1)), _MAX_INT)


def _local_ns(d: dt.datetime) -> int:
    """Local wall-clock time to epoch nanoseconds. ``datetime.timestamp`` raises on Windows for
    dates around or before 1970, so those are measured from a safe reference date."""
    try:
        return int(d.timestamp() * 1e9)
    except (OSError, OverflowError, ValueError):
        ref = dt.datetime(2000, 1, 1)
        return int((ref.timestamp() + (d - ref).total_seconds()) * 1e9)


def _parse_date(s: str, end: bool = False) -> int | None:
    for fmt, step in (("%Y-%m-%d", "day"), ("%Y-%m", "month"), ("%Y", "year")):
        try:
            d = dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
        if end:
            if step == "day":
                d += dt.timedelta(days=1)
            elif step == "month":
                d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            else:
                d = d.replace(year=d.year + 1)
        return _local_ns(d)
    return None


def parse_query(q: str) -> tuple[str, Filters]:
    f = Filters()
    kept: list[str] = []
    pos = 0
    for m in _FILTER.finditer(q):
        key, val = m.group(1).lower(), m.group(2).strip('"')
        handled = True
        if key == "ext":
            f.exts += ["." + v.lstrip(".").lower() for v in val.split(",") if v.strip(". ")]
        elif key in ("kind", "type"):
            for v in val.lower().split(","):
                v = _KIND_ALIASES.get(v, v)
                if v in KINDS:
                    f.kinds.append(v)
                elif re.fullmatch(r"\.?[a-z0-9]{1,8}", v):  # type:pdf means the file type pdf
                    f.exts.append("." + v.lstrip("."))
                else:
                    f.problems.append(f"unknown kind '{v}'")
        elif key in ("after", "before"):
            ns = _parse_date(val)
            if ns is None:
                f.problems.append(f"could not read the date in '{m.group(0)}' (use 2024, 2024-05 or 2024-05-31)")
            elif key == "after":
                f.after_ns = ns
            else:
                f.before_ns = ns
        elif key in ("larger", "min", "smaller", "max"):
            n = _parse_size(val)
            if n is None:
                f.problems.append(f"could not read the size in '{m.group(0)}' (use e.g. 500KB, 10MB, 1.5GB)")
            elif key in ("larger", "min"):
                f.min_size = n
            else:
                f.max_size = n
        elif key in ("in", "folder"):
            f.folder = val.lower()
        else:
            handled = False
        if handled:  # cut out exactly this span (str.replace would also hit longer filters)
            kept.append(q[pos:m.start()])
            pos = m.end()
    kept.append(q[pos:])
    return " ".join(" ".join(kept).split()), f


def fts_query(text: str) -> str | None:
    ws = words(text)  # split exactly like the FTS tokenizer, so whole Devanagari words survive
    ws = [w for w in ws if w not in STOPWORDS or len(ws) == 1]
    if not ws:
        return None
    return " OR ".join(f'"{w}"' + ("*" if len(w) >= 4 else "") for w in ws[:16])


# Rank-fusion weight of each ranked list, per search scope. In the Images view the picture content
# is the primary signal; text lists over image *names/descriptors* (often "IMG_0042") are only
# tie-breakers. In the mixed view only images that stand out visually take part (within 1 z of the
# best visual match and z >= 1.5).
LIST_WEIGHTS = {
    "all": {"content": 1.0, "names": 1.0, "chunk_sem": 1.0, "file_sem": 0.8, "visual": 1.0},
    "documents": {"content": 1.0, "names": 1.0, "chunk_sem": 1.0, "file_sem": 0.8, "visual": 0.0},
    "images": {"content": 0.5, "names": 0.5, "chunk_sem": 0.3, "file_sem": 0.3, "visual": 3.0},
}


class SearchEngine:
    def __init__(self, conn: sqlite3.Connection, backend: EmbeddingBackend | None = None, vision=None):
        self.conn = conn
        self.backend = backend
        self.vision = vision  # text tower of the image-text model (lfi.vision.VisionBackend)
        self._samples: dict[str, tuple[int, np.ndarray]] = {}
        self.last_problems: list[str] = []  # filters in the last query that could not be applied

    def _vector_stores(self):
        space = library.active_space(self.conn)
        if space is None or self.backend is None or space["model"] != self.backend.model_id:
            return None
        return library.stores(self.conn, space)

    def _image_store(self):
        space = library.active_image_space(self.conn)
        if space is None or self.vision is None or space["model"] != self.vision.model_id:
            return None
        return library.image_store(self.conn, space)

    def _population(self, store) -> np.ndarray:
        """Random sample (<= 2000) of image vectors, used to turn a query's raw cosine scores into
        z-scores. Raw image-text cosines are tiny and model-specific (0.03-0.10 for nomic,
        0.15-0.32 for CLIP), so only "how much does this image stand out" is comparable."""
        n = store.count()
        cached = self._samples.get(store.table)
        if cached is None or cached[0] != n:
            rows = self.conn.execute(f"SELECT embedding FROM {store.table} ORDER BY random() LIMIT 2000").fetchall()
            mat = np.frombuffer(b"".join(r[0] for r in rows), dtype=np.float32).reshape(len(rows), store.dim) \
                if rows else np.zeros((0, store.dim), dtype=np.float32)
            cached = self._samples[store.table] = (n, mat)
        return cached[1]

    def visual_matches(self, text: str, k: int = 200, min_z: float = 1.0,
                       allowed: set[int] | None = None) -> list[tuple[int, float, float]]:
        """``(file_id, cosine, z)`` for images whose content matches a description."""
        store = self._image_store()
        if store is None or not text:
            return []
        qv = self.vision.embed_text(text)
        hits = _restricted_knn(store, qv, allowed, k, EXACT_FILTERED_MAX)
        pop = self._population(store)
        if len(pop) < 5:
            return [(fid, sim, 3.0) for fid, sim in hits]
        s = pop @ qv
        mu, sd = float(s.mean()), float(s.std()) or 1e-6
        return [(fid, sim, (sim - mu) / sd) for fid, sim in hits if (sim - mu) / sd >= min_z]

    def _allowed_ids(self, f: Filters, scope: str) -> set[int]:
        """Every indexed file that passes the filters. Computed up front so that each ranked list
        only contains candidates that can be shown; filtering a truncated top-300 afterwards
        silently lost matches in larger libraries."""
        conds, args = [], []
        if f.exts:
            conds.append(f"ext IN ({','.join('?' * len(f.exts))})")
            args += f.exts
        if f.kinds:
            conds.append(f"COALESCE(kind, 'other') IN ({','.join('?' * len(f.kinds))})")
            args += f.kinds
        if scope == "documents":
            conds.append("COALESCE(kind, 'other') <> 'image'")
        for cond, val in (("mtime_ns >= ?", f.after_ns), ("mtime_ns < ?", f.before_ns),
                          ("size >= ?", f.min_size), ("size <= ?", f.max_size)):
            if val is not None:
                conds.append(cond)
                args.append(val)
        where = " WHERE " + " AND ".join(conds) if conds else ""
        rows = self.conn.execute(f"SELECT id, path, root_id FROM files{where}", args).fetchall()
        if not f.folder:
            return {r["id"] for r in rows}
        roots = library.root_paths_by_id(self.conn)
        return {r["id"] for r in rows
                if f.folder in "\\".join(library.location_parts(r["path"], roots.get(r["root_id"])))}

    def search(self, query: str, limit: int = 50, scope: str = "all") -> list[SearchHit]:
        """``scope``: 'all', 'documents' (everything except images) or 'images'."""
        text, filters = parse_query(query)
        self.last_problems = list(filters.problems)
        user_filters = filters.active()
        if scope == "images":
            filters.kinds = ["image"]
        allowed = self._allowed_ids(filters, scope) if user_filters else None
        in_allowed = " AND {col} IN (SELECT value FROM json_each(?))"
        allowed_json = (json.dumps(sorted(allowed)),) if allowed is not None else ()
        W = LIST_WEIGHTS.get(scope, LIST_WEIGHTS["all"])
        ranked: list[list[int]] = []
        weights: list[float] = []
        snippets: dict[int, str] = {}
        sem: dict[int, float] = {}
        why: dict[int, set[str]] = {}
        visual: dict[int, float] = {}

        fq = fts_query(text) if text else None
        if fq:
            rows = self.conn.execute(
                "SELECT c.file_id AS fid, bm25(chunks_fts) AS s,"
                " snippet(chunks_fts, 0, '[', ']', ' ... ', 14) AS snip"
                " FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
                " WHERE chunks_fts MATCH ?" + (in_allowed.format(col="c.file_id") if allowed is not None else "")
                + " ORDER BY s LIMIT 300", (fq, *allowed_json)).fetchall()
            order = []
            for r in rows:
                if r["fid"] not in snippets:
                    snippets[r["fid"]] = r["snip"]
                    order.append(r["fid"])
                    why.setdefault(r["fid"], set()).add("keyword match in content")
            ranked.append(order)
            weights.append(W["content"])
            rows = self.conn.execute(
                "SELECT rowid AS fid, bm25(names_fts, 3.0, 1.0) AS s FROM names_fts"
                " WHERE names_fts MATCH ?" + (in_allowed.format(col="rowid") if allowed is not None else "")
                + " ORDER BY s LIMIT 300", (fq, *allowed_json)).fetchall()
            ranked.append([r["fid"] for r in rows])
            weights.append(W["names"])
            for r in rows:
                why.setdefault(r["fid"], set()).add("filename / folder match")

        stores = self._vector_stores() if text else None
        if stores is not None:
            chunk_store, file_store = stores
            qv = self.backend.embed_query(text)
            chunk_ids = None if allowed is None else {r[0] for r in self.conn.execute(
                "SELECT id FROM chunks WHERE file_id IN (SELECT value FROM json_each(?))", allowed_json)}
            hits = _restricted_knn(chunk_store, qv, chunk_ids, 300, 6 * EXACT_FILTERED_MAX)
            if hits:
                ids = [h[0] for h in hits]
                fmap = dict(self.conn.execute(
                    f"SELECT id, file_id FROM chunks WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall())
                order = []
                best_chunk: dict[int, int] = {}
                for cid, sim in hits:
                    fid = fmap.get(cid)
                    if fid is None:
                        continue
                    if fid not in sem:
                        order.append(fid)
                        sem[fid] = sim
                        best_chunk[fid] = cid
                ranked.append(order)
                weights.append(W["chunk_sem"])
                for fid, cid in best_chunk.items():
                    if fid not in snippets:
                        row = self.conn.execute("SELECT text FROM chunks WHERE id=?", (cid,)).fetchone()
                        if row and row["text"]:
                            snippets[fid] = row["text"][:240].replace("\n", " ") + " ..."
            fhits = _restricted_knn(file_store, qv, allowed, 200, EXACT_FILTERED_MAX)
            ranked.append([fid for fid, _ in fhits])
            weights.append(W["file_sem"])
            for fid, sim in fhits:
                sem[fid] = max(sem.get(fid, -1.0), sim)

        if text and scope != "documents":
            vis = self.visual_matches(text, 200 if scope == "images" else 40, 0.5 if scope == "images" else 1.5,
                                      allowed)
            if vis and scope != "images":
                top = max(z for _, _, z in vis)
                vis = [v for v in vis if v[2] >= top - 1.0]
            if vis:
                ranked.append([fid for fid, _, _ in vis])
                weights.append(W["visual"])
                visual = {fid: z for fid, _, z in vis}

        if not text:
            # Filter-only query: newest matching files.
            if allowed is not None:
                where, args = "WHERE id IN (SELECT value FROM json_each(?))", allowed_json
            else:
                where = {"images": "WHERE kind='image'", "documents": "WHERE COALESCE(kind,'other')<>'image'"}.get(
                    scope, "")
                args = ()
            rows = self.conn.execute(f"SELECT id FROM files {where} ORDER BY mtime_ns DESC LIMIT 2000", args).fetchall()
            ranked.append([r["id"] for r in rows])
            weights.append(1.0)

        fused: dict[int, float] = {}
        if scope == "images" and visual:
            # Images view: an interpretable score instead of rank fusion. RRF only sees ranks, so a
            # clear visual winner (z=2.5) could lose to weak matches (z=1.0) that happen to share
            # a folder name with the query. Score = visual z + 0.5 if the file/folder name matches
            # (independent, human-given evidence). Auto-tag keyword matches add nothing here: tags
            # are derived from the same image vector, so counting them again double-counts and
            # lets generic words mislead ("evening sky" -> a "night sky" tag beat the sunset).
            for fid in set(visual) | set(why):
                r = why.get(fid, set())
                fused[fid] = visual.get(fid, 0.0) + (0.5 if "filename / folder match" in r else 0.0)
        else:
            for lst, w in zip(ranked, weights):
                for rank, fid in enumerate(lst):
                    fused[fid] = fused.get(fid, 0.0) + w / (RRF_K + rank + 1)
        if not fused:
            return []
        if sem and not (scope == "images" and visual):
            # Rank fusion always returns *something*; drop semantic-only hits that are much
            # weaker than the best semantic match (cosine scales differ per model, so relative)
            # or that don't stand out from the library as a whole.
            floor = max(sem.values()) * SEMANTIC_RELATIVE_FLOOR
            pop = self._population(file_store)
            if len(pop) >= SEMANTIC_Z_MIN_FILES:
                s = pop @ qv
                mu, sd = float(s.mean()), float(s.std()) or 1e-6
            else:
                mu, sd = None, None
            for fid in [f for f in fused if f not in why and f not in visual]:
                v = sem.get(fid, 1.0)
                if v < floor or (mu is not None and (v - mu) / sd < SEMANTIC_MIN_Z):
                    del fused[fid]
        order = sorted(fused, key=fused.get, reverse=True)
        hits: list[SearchHit] = []
        for i in range(0, len(order), 400):
            part = order[i:i + 400]
            rows = {r["id"]: r for r in self.conn.execute(
                f"SELECT id, path, name, kind, ext, size, mtime_ns, img_tags FROM files"
                f" WHERE id IN ({','.join('?' * len(part))})",
                part)}
            for fid in part:
                r = rows.get(fid)
                if r is None or (allowed is not None and fid not in allowed) or not _passes(r, filters) \
                        or (scope == "documents" and r["kind"] == "image"):
                    continue
                reasons = sorted(why.get(fid, set()))
                if fid in visual:
                    z = visual[fid]
                    level = "strong" if z >= 3 else ("good" if z >= 2 else "possible")
                    reasons.insert(0, f"picture content matches ({level}, z={z:.1f})")
                if fid in sem:
                    reasons.append(f"semantic similarity {sem[fid]:.2f}")
                snip = snippets.get(fid, "")
                if r["img_tags"] and (not snip or snip.startswith("Image appears")):
                    snip = "Appears to show: " + r["img_tags"].replace(";", ",")
                hits.append(SearchHit(fid, r["path"], r["name"], r["kind"] or "other", r["size"], r["mtime_ns"],
                                      fused[fid], snip, sem.get(fid), reasons, visual.get(fid)))
                if len(hits) >= limit:
                    return hits
        return hits

    def related(self, file_id: int, limit: int = 15) -> list[SearchHit]:
        stores = self._vector_stores()
        if stores is None:
            return []
        _, file_store = stores
        v = file_store.get([file_id]).get(file_id)
        if v is None:
            return []
        me = self.conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        my_terms = set(self.file_keywords(file_id))
        out: list[SearchHit] = []
        for fid, sim in file_store.knn(v, limit + 1):
            if fid == file_id:
                continue
            r = self.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
            if r is None:
                continue
            reasons = [f"content similarity {sim:.2f}"]
            shared = [t for t in self.file_keywords(fid) if t in my_terms][:6]
            if shared:
                reasons.append("shared terms: " + ", ".join(shared))
            if r["parent_key"] == me["parent_key"]:
                reasons.append("same folder")
            if r["hash"] and r["hash"] == me["hash"]:
                reasons.append("identical content (same hash)")
            out.append(SearchHit(fid, r["path"], r["name"], r["kind"] or "other", r["size"], r["mtime_ns"],
                                 sim, "", sim, reasons))
        return out[:limit]

    def file_keywords(self, file_id: int, top: int = 15) -> list[str]:
        rows = self.conn.execute("SELECT text FROM chunks WHERE file_id=? ORDER BY ord LIMIT 6", (file_id,)).fetchall()
        name = self.conn.execute("SELECT name FROM files WHERE id=?", (file_id,)).fetchone()
        text = " ".join(r["text"] for r in rows) + " " + (name["name"] if name else "")
        return keywords(text, top)

    def preview(self, file_id: int, max_chars: int = 4000) -> str:
        rows = self.conn.execute("SELECT text FROM chunks WHERE file_id=? ORDER BY ord", (file_id,)).fetchall()
        out, n = [], 0
        for r in rows:
            if n >= max_chars:
                break
            out.append(r["text"])
            n += len(r["text"])
        return "\n\n".join(out)[:max_chars]


def _restricted_knn(store, qv: np.ndarray, ids: set[int] | None, k: int, exact_max: int) -> list[tuple[int, float]]:
    """Top-``k`` neighbours among ``ids`` (all vectors if ``None``): exact for small candidate
    sets, otherwise the widest native top-k, filtered."""
    if ids is None:
        return store.knn(qv, k)
    if not ids:
        return []
    if len(ids) <= exact_max:
        return store.knn_among(qv, ids, k)
    return [h for h in store.knn(qv, 4096) if h[0] in ids][:k]


def _passes(r: sqlite3.Row, f: Filters) -> bool:
    if f.exts and r["ext"] not in f.exts:
        return False
    if f.kinds and (r["kind"] or "other") not in f.kinds:
        return False
    if f.after_ns and r["mtime_ns"] < f.after_ns:
        return False
    if f.before_ns and r["mtime_ns"] >= f.before_ns:
        return False
    if f.min_size is not None and r["size"] < f.min_size:
        return False
    if f.max_size is not None and r["size"] > f.max_size:
        return False
    if f.folder and f.folder not in os.path.dirname(r["path"]).lower():
        return False
    return True

