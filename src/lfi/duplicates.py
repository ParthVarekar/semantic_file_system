"""Duplicate and near-duplicate detection. Deterministic: no LLM is involved.

* Exact duplicates: identical BLAKE3 content hash (size-grouped, prehash-filtered for large
  files). Hard links (same volume + file ID) are one physical file and reclaim nothing.
* Near-duplicate images: 64-bit perceptual hashes (pHash + dHash, ImageHash library) compared
  with multi-index hashing: split into 8 bands of 8 bits; by the pigeonhole principle any two
  hashes within Hamming distance <= 7 share at least one identical band, so candidates come
  from band buckets instead of all pairs. Both hashes must agree.
* Similar documents: cosine similarity of file embeddings, with text-overlap evidence from
  RapidFuzz. Always a manual-comparison basket: similar is not the same as duplicate.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field

import numpy as np

from . import library
from .safety import norm

# "report (1)", "report - Copy", "report - Copy (2)", "Copy of report", "report_copy", "report (copy)"
COPY_NAME = re.compile(r"(\s?\(\d+\)$|\s-\s?copy(\s\(\d+\))?$|^copy of\s|[\s_-]copy$|\(copy\)$)", re.IGNORECASE)
CLUTTER_DIRS = ("downloads", "desktop", "temp", "tmp")


@dataclass
class DupFile:
    id: int
    path: str
    name: str
    size: int
    mtime_ns: int
    ino: str | None
    dev: str | None
    keeper_points: float = 0.0
    keeper_reasons: list[str] = field(default_factory=list)
    hardlink_of: int | None = None  # another path in the group with the very same data
    width: int | None = None
    height: int | None = None
    root: str | None = None
    nlink: int = 1  # hard links to this data on the volume (the OS's count, not only indexed ones)
    shares_keeper: bool = False  # hard link of the kept copy: removing it frees nothing


@dataclass
class DupGroup:
    kind: str  # exact | image | document
    key: str
    files: list[DupFile]
    keeper_id: int
    evidence: list[str] = field(default_factory=list)
    keeper_uncertain: bool = False

    @property
    def keeper(self) -> DupFile:
        return next(f for f in self.files if f.id == self.keeper_id)

    @property
    def others(self) -> list[DupFile]:
        return [f for f in self.files if f.id != self.keeper_id]

    def reclaimable(self, removing: set[int] | None = None) -> int:
        """Bytes freed by removing ``removing`` (default: every non-keeper). Data is freed only
        when *all* links to it go: never for links of the kept copy, and not while a link outside
        this group (``nlink``) still holds it."""
        removing = {f.id for f in self.others} if removing is None else removing
        units: dict[tuple, list[DupFile]] = {}
        for f in self.files:
            units.setdefault(self._phys(f), []).append(f)
        keeper_unit = self._phys(self.keeper)
        return sum(fs[0].size for unit, fs in units.items()
                   if unit != keeper_unit and all(f.id in removing for f in fs)
                   and len(fs) >= max(f.nlink for f in fs))

    @staticmethod
    def _phys(f: DupFile) -> tuple:
        return (f.dev, f.ino) if f.ino and f.dev else ("path", f.id)


def rank_keeper(files: list[DupFile], prefs: dict, project_of: dict[int, str | None],
                prefer_quality: bool = False) -> tuple[int, bool]:
    """Pick the copy to keep using transparent signals. Returns (keeper_id, uncertain).
    Age is deliberately *not* used: neither the oldest nor the newest copy is assumed right."""
    for f in files:
        pts, why = 0.0, []
        p = prefs.get(norm(f.path))
        if p is not None and p["pinned"]:
            pts += 100
            why.append("pinned by you (+100)")
        if project_of.get(f.id):
            pts += 30
            why.append("part of a software project (+30)")
        parts = library.location_parts(f.path, f.root)
        if any(c in parts for c in CLUTTER_DIRS):
            pts -= 10
            why.append("in a Downloads/Desktop/Temp folder (-10)")
        else:
            pts += 10
            why.append("in an organized folder (+10)")
        if COPY_NAME.search(os.path.splitext(f.name)[0]):
            pts -= 15
            why.append("name looks like a copy (-15)")
        if prefer_quality and f.width and f.height:
            mp = f.width * f.height / 1e6
            pts += min(20.0, mp * 4)
            why.append(f"{f.width}x{f.height} resolution (+{min(20.0, mp * 4):.0f})")
        f.keeper_points, f.keeper_reasons = pts, why
    ordered = sorted(files, key=lambda f: (-f.keeper_points, len(f.path), f.path.lower()))
    uncertain = len(ordered) > 1 and ordered[0].keeper_points == ordered[1].keeper_points
    if uncertain:
        ordered[0].keeper_reasons.append("tie: chosen by shortest path - please review")
    return ordered[0].id, uncertain


def _row_file(r: sqlite3.Row, roots: dict[int, str]) -> DupFile:
    return DupFile(r["id"], r["path"], r["name"], r["size"], r["mtime_ns"], r["ino"], r["dev"],
                   width=r["width"], height=r["height"], root=roots.get(r["root_id"]), nlink=r["nlink"] or 1)


def _star_groups(pairs: dict[tuple[int, int], float], higher_is_closer: bool) -> list[tuple[int, list[int]]]:
    """Group items so every member is *directly* similar to its group's centre. Connected
    components chain: A~B~C...~Z can join two images that share nothing. Here the best-connected
    item becomes a centre and takes its unassigned neighbours; repeat. Returns (centre, members)."""
    nbrs: dict[int, dict[int, float]] = {}
    for (a, b), v in pairs.items():
        nbrs.setdefault(a, {})[b] = v
        nbrs.setdefault(b, {})[a] = v
    sign = -1.0 if higher_is_closer else 1.0
    order = sorted(nbrs, key=lambda x: (-len(nbrs[x]), sum(sign * v for v in nbrs[x].values()) / len(nbrs[x]), x))
    done: set[int] = set()
    out = []
    for c in order:
        if c in done:
            continue
        members = [c] + sorted((m for m in nbrs[c] if m not in done), key=lambda m: (sign * nbrs[c][m], m))
        if len(members) < 2:
            continue
        done.update(members)
        out.append((c, members))
    return out


def _featureless(dhash: int) -> bool:
    """Flat or nearly flat pictures (a solid colour, a blank page): their hashes carry no visual
    information, so a black and a white square would look 'identical'."""
    return dhash.bit_count() <= 2 or dhash.bit_count() >= 62


def exact_groups(conn: sqlite3.Connection, min_size: int = 1) -> list[DupGroup]:
    prefs = library.prefs_map(conn)
    roots = library.root_paths_by_id(conn)
    rows = conn.execute(
        "SELECT * FROM files WHERE hash IN (SELECT hash FROM files WHERE hash IS NOT NULL AND size>=?"
        " GROUP BY hash HAVING count(*)>1) ORDER BY hash, path", (min_size,)).fetchall()
    groups: list[DupGroup] = []
    by_hash: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_hash.setdefault(r["hash"], []).append(r)
    for h, rs in by_hash.items():
        files = [_row_file(r, roots) for r in rs]
        phys: dict[tuple, int] = {}
        for f in files:
            if f.ino and f.dev:
                key = (f.dev, f.ino)
                if key in phys:
                    f.hardlink_of = phys[key]
                else:
                    phys[key] = f.id
        keeper, unsure = rank_keeper(files, prefs, {r["id"]: r["project_root"] for r in rs})
        kf = next(f for f in files if f.id == keeper)
        for f in files:
            f.shares_keeper = f.id != keeper and bool(f.ino and f.dev) and (f.dev, f.ino) == (kf.dev, kf.ino)
        ev = [f"identical BLAKE3 content hash {h[:16]}...", f"{len(files)} copies of {files[0].size:,} bytes"]
        if any(f.hardlink_of for f in files):
            ev.append("some paths are hard links to the same data: space is freed only when every link is removed")
        groups.append(DupGroup("exact", h, files, keeper, ev, unsure))
    groups.sort(key=lambda g: g.reclaimable(), reverse=True)
    return groups


def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def similar_image_groups(conn: sqlite3.Connection, max_distance: int = 6, max_bucket: int = 2000) -> list[DupGroup]:
    rows = conn.execute("SELECT * FROM files WHERE phash IS NOT NULL AND dhash IS NOT NULL").fetchall()
    seen_hash: set[str] = set()
    items: list[tuple[int, int, int]] = []
    by_id: dict[int, sqlite3.Row] = {}
    for r in rows:
        if r["hash"] and r["hash"] in seen_hash:
            continue  # exact copies are handled by the exact-duplicate basket
        if r["hash"]:
            seen_hash.add(r["hash"])
        try:
            item = (r["id"], int(r["phash"], 16), int(r["dhash"], 16))
        except ValueError:
            continue
        if _featureless(item[2]):
            continue
        items.append(item)
        by_id[r["id"]] = r
    bands = 8
    buckets: dict[tuple[int, int], list[int]] = {}
    for idx, (_, ph, _) in enumerate(items):
        for b in range(bands):
            buckets.setdefault((b, (ph >> (8 * b)) & 0xFF), []).append(idx)
    dist: dict[tuple[int, int], tuple[int, int]] = {}
    checked: set[tuple[int, int]] = set()
    for members in buckets.values():
        if len(members) < 2 or len(members) > max_bucket:
            continue
        for i in range(len(members)):
            a = items[members[i]]
            for j in range(i + 1, len(members)):
                b = items[members[j]]
                pair = (min(a[0], b[0]), max(a[0], b[0]))
                if pair in checked:
                    continue
                checked.add(pair)
                dp = _hamming(a[1], b[1])
                dd = _hamming(a[2], b[2])
                if dp <= max_distance and dd <= max_distance + 4:
                    dist[pair] = (dp, dd)
    prefs = library.prefs_map(conn)
    roots = library.root_paths_by_id(conn)
    groups = []
    for centre, members in _star_groups({p: d[0] + d[1] for p, d in dist.items()}, higher_is_closer=False):
        rs = [by_id[i] for i in sorted(members)]
        files = [_row_file(r, roots) for r in rs]
        keeper, unsure = rank_keeper(files, prefs, {r["id"]: r["project_root"] for r in rs}, prefer_quality=True)
        ds = [dist[(min(centre, m), max(centre, m))] for m in members if m != centre]
        ev = [f"perceptual hash distance {min(d[0] for d in ds)}-{max(d[0] for d in ds)} of 64 bits (pHash), "
              f"{min(d[1] for d in ds)}-{max(d[1] for d in ds)} (dHash)",
              "visually similar, not byte-identical: check before removing (edits/crops may matter)"]
        groups.append(DupGroup("image", f"img-{min(members)}", files, keeper, ev, unsure))
    groups.sort(key=lambda g: g.reclaimable(), reverse=True)
    return groups


def similar_document_groups(conn: sqlite3.Connection, threshold: float = 0.93, min_chars: int = 200,
                            max_group: int = 8) -> list[DupGroup]:
    stores = library.stores(conn)
    if stores is None:
        return []
    _, file_store = stores
    ids, mat = file_store.all()
    if len(ids) < 2:
        return []
    docs = {r["id"]: r for r in conn.execute(
        "SELECT * FROM files WHERE kind IN ('document','code','data') AND text_chars>=?", (min_chars,))}
    mask = np.array([int(i) in docs for i in ids])
    ids, mat = ids[mask], mat[mask]
    if len(ids) < 2:
        return []
    sims: dict[tuple[int, int], float] = {}
    block = 1024
    for s in range(0, len(ids), block):
        sub = mat[s:s + block] @ mat.T
        for i in range(sub.shape[0]):
            gi = s + i
            sub[i, gi] = -1
            for j in np.nonzero(sub[i] >= threshold)[0]:
                a, b = int(ids[gi]), int(ids[j])
                if docs[a]["hash"] and docs[a]["hash"] == docs[b]["hash"]:
                    continue
                pair = (min(a, b), max(a, b))
                sims[pair] = float(sub[i, j])
    prefs = library.prefs_map(conn)
    roots = library.root_paths_by_id(conn)
    groups = []
    for centre, members in _star_groups(sims, higher_is_closer=True):
        members = members[:max_group]  # centre first, then its closest neighbours
        rs = [docs[i] for i in members]
        files = [_row_file(r, roots) for r in rs]
        keeper, unsure = rank_keeper(files, prefs, {r["id"]: r["project_root"] for r in rs})
        pair_sims = [sims[(min(centre, m), max(centre, m))] for m in members if m != centre]
        ev = [f"content embedding similarity {min(pair_sims):.2f}-{max(pair_sims):.2f}"]
        overlap = text_overlap(conn, centre, members[1])
        if overlap is not None:
            ev.append(f"text overlap between the two closest: {overlap:.0f}%")
        ev.append("similar content, different bytes: may be different versions - compare manually")
        groups.append(DupGroup("document", f"doc-{members[0]}", files, keeper, ev, True))
    groups.sort(key=lambda g: -len(g.files))
    return groups


def text_overlap(conn: sqlite3.Connection, a: int, b: int, chars: int = 6000) -> float | None:
    from rapidfuzz import fuzz

    def text(fid: int) -> str:
        return " ".join(r["text"] for r in conn.execute(
            "SELECT text FROM chunks WHERE file_id=? ORDER BY ord LIMIT 8", (fid,)))[:chars]

    ta, tb = text(a), text(b)
    if not ta or not tb:
        return None
    return float(fuzz.token_set_ratio(ta, tb))
