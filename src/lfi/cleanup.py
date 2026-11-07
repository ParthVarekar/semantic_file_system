"""Cleanup review baskets and the explainable 0-100 *review-priority* score.

The score ranks files for human review. It is a weighted sum of named signals, each shown to
the user with its evidence and point contribution. It is NOT a probability that deletion is
safe: there is no labelled validation data to calibrate such a probability, so none is claimed.
Weights are user-configurable (Settings.weights). Age is capped at a few points and never
decisive. User feedback shifts scores per (basket, extension) with Beta(1,1) smoothing.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field

from . import library
from .config import Settings
from .duplicates import COPY_NAME, DupGroup, exact_groups, similar_document_groups, similar_image_groups

BASKETS: list[tuple[str, str, str]] = [
    ("exact_duplicates", "Exact duplicates", "Byte-identical copies (verified by content hash)."),
    ("similar_images", "Near-duplicate images", "Visually similar images: resized, re-compressed or re-saved."),
    ("similar_documents", "Similar documents", "Related content that needs manual comparison; never auto-selected."),
    ("large_files", "Large files", "Big files worth a look. Size alone does not mean unnecessary."),
    ("old_installers", "Old installers & downloads", "Setup files and archives that may already be installed or extracted."),
    ("temporary", "Temporary files & artifacts", "Recognizable temporary, partial-download and lock files."),
    ("empty", "Empty files", "Zero-byte files (placeholders such as __init__.py and .gitkeep are excluded)."),
]
BASKET_LABEL = {k: v for k, v, _ in BASKETS}

TEMP_STRONG = [
    (re.compile(r"^~\$"), "Office owner/lock file (~$...)"),
    (re.compile(r"^\.~lock\..*#$"), "LibreOffice lock file"),
    (re.compile(r"\.(crdownload|part|partial|download|opdownload)$", re.I), "incomplete browser download"),
    (re.compile(r"^(thumbs\.db|ehthumbs\.db|\.ds_store)$", re.I), "thumbnail/folder cache file"),
]
TEMP_WEAK = [
    (re.compile(r"\.(tmp|temp)$", re.I), "temporary-file extension"),
    (re.compile(r"\.(dmp|mdmp)$", re.I), "crash dump"),
    (re.compile(r"\.(bak|old|swp|swo)$|~$", re.I), "backup/swap copy (may still be wanted)"),
]
EMPTY_ALLOWED = {"__init__.py", ".gitkeep", ".keep", ".placeholder", ".nojekyll", "py.typed", ".gitignore"}
INSTALLER_EXT = {".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle", ".iso", ".img", ".dmg", ".pkg"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"}


@dataclass
class Signal:
    name: str
    points: float
    evidence: str


@dataclass
class Candidate:
    file_id: int
    path: str
    name: str
    size: int
    mtime_ns: int
    basket: str
    score: float
    confidence: str
    signals: list[Signal] = field(default_factory=list)
    group_key: str | None = None
    keeper_id: int | None = None
    keeper_path: str | None = None
    phys: tuple = ()
    nlink: int = 1

    def explanation(self) -> str:
        lines = [f"Review priority {self.score:.0f}/100 ({self.confidence} confidence) - {BASKET_LABEL[self.basket]}"]
        for s in self.signals:
            lines.append(f"  {s.points:+.0f}  {s.name}: {s.evidence}")
        lines.append("This is a ranking for review, not a probability that removal is safe.")
        return "\n".join(lines)


@dataclass
class CleanupReport:
    baskets: dict[str, list[Candidate]]
    groups: dict[str, list[DupGroup]]
    hidden_by_preference: int
    generated_at: float

    def reclaimable(self, basket: str | None = None, selected: set[int] | None = None) -> int:
        """Unique-physical-file total: a file in two baskets counts once, and hard-linked data
        counts only when every one of its links (``nlink``, as Windows reports it) is included."""
        units: dict[tuple, dict[int, Candidate]] = {}
        for b, cands in self.baskets.items():
            if basket and b != basket:
                continue
            if b == "similar_documents" and basket is None and selected is None:
                continue  # manual-comparison basket is not a reclaimable estimate
            for c in cands:
                if selected is None or c.file_id in selected:
                    units.setdefault(c.phys, {})[c.file_id] = c
        return sum(next(iter(cs.values())).size for cs in units.values()
                   if len(cs) >= max(c.nlink for c in cs.values()))


def _phys(row) -> tuple:
    return (row["dev"], row["ino"]) if row["ino"] and row["dev"] else ("id", row["id"])


class Scorer:
    def __init__(self, conn: sqlite3.Connection, settings: Settings):
        self.w = settings.weights
        self.settings = settings
        self.now = time.time()
        self.roots = library.root_paths_by_id(conn)
        # Replayed in order: a restore takes back its own quarantine ("removed") and counts double
        # as "kept" in the basket the file was quarantined from - strong evidence of a wrong suggestion.
        self.feedback: dict[tuple[str, str], tuple[int, int]] = {}
        quarantined_from: dict[str, tuple[str, str]] = {}

        def bump(key, da, dk):
            a, k = self.feedback.get(key, (0, 0))
            self.feedback[key] = (max(0, a + da), k + dk)

        for r in conn.execute("SELECT path_key, basket, ext, action FROM feedback ORDER BY id"):
            key = (r["basket"], r["ext"])
            if r["action"] == "quarantined":
                bump(key, 1, 0)
                if r["path_key"]:
                    quarantined_from[r["path_key"]] = key
            elif r["action"] in ("kept", "not_junk"):
                bump(key, 0, 1)
            elif r["action"] == "restored":
                origin = quarantined_from.pop(r["path_key"], None)
                if origin is not None:
                    bump(origin, -1, 2)
                else:
                    bump(key, 0, 2)
        self.restored_ext: dict[str | None, int] = {}
        for r in conn.execute("SELECT ext, count(*) AS n FROM feedback WHERE action='restored' GROUP BY ext"):
            self.restored_ext[r["ext"]] = r["n"]

    def shared(self, row: sqlite3.Row, basket: str) -> list[Signal]:
        out: list[Signal] = []
        parts = library.location_parts(row["path"], self.roots.get(row["root_id"]))
        loc = next((p for p in parts if p in ("downloads", "desktop", "temp", "tmp")), None)
        if loc:
            out.append(Signal("clutter location", self.w.clutter_location, f"inside a '{loc}' folder"))
        age_days = (self.now - row["mtime_ns"] / 1e9) / 86400
        if age_days > 365:
            pts = min(self.w.age_max, self.w.age_max * (age_days / 365) / 3)
            out.append(Signal("age (weak signal)", pts, f"not modified for {age_days / 365:.1f} years"))
        elif age_days < 7:
            out.append(Signal("recently modified", self.w.recently_modified,
                              f"modified {max(age_days, 0):.1f} days ago; may be in use"))
        if row["project_root"]:
            out.append(Signal("inside a project", self.w.in_project,
                              f"part of {os.path.basename(row['project_root'])}; project files are kept together"))
        a, k = self.feedback.get((basket, row["ext"]), (0, 0))
        if a + k:
            pts = self.w.feedback_max * (a - k) / (a + k + 2)
            out.append(Signal("your past decisions", pts,
                              f"you removed {a} and kept/restored {k} similar '{row['ext'] or 'no-ext'}' files here"))
        return out

    def finish(self, row: sqlite3.Row, basket: str, primary: list[Signal], confidence: str, **kw) -> Candidate:
        sig = primary + self.shared(row, basket)
        score = max(0.0, min(100.0, sum(s.points for s in sig)))
        return Candidate(row["id"], row["path"], row["name"], row["size"], row["mtime_ns"], basket, round(score, 1),
                         confidence, sig, phys=_phys(row), nlink=(row["nlink"] or 1) if row["ino"] else 1, **kw)


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def analyze(conn: sqlite3.Connection, settings: Settings, include_similar_documents: bool = True) -> CleanupReport:
    sc = Scorer(conn, settings)
    w = settings.weights
    prefs = library.prefs_map(conn)
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM files WHERE status NOT IN ('cloud')")}
    hidden = 0

    def blocked(row) -> bool:
        p = prefs.get(row["path_key"])
        return bool(p and (p["pinned"] or p["never_suggest"]))

    baskets: dict[str, list[Candidate]] = {k: [] for k, _, _ in BASKETS}
    groups: dict[str, list[DupGroup]] = {}

    # 1. exact duplicates -------------------------------------------------------------
    groups["exact_duplicates"] = exact_groups(conn)
    for g in groups["exact_duplicates"]:
        keeper = g.keeper
        for f in g.others:
            row = rows.get(f.id)
            if row is None:
                continue
            if blocked(row):
                hidden += 1
                continue
            if f.shares_keeper:
                continue  # hard link of the kept copy: removing it reclaims nothing
            sig = [Signal("exact duplicate", w.exact_duplicate,
                          f"same content hash as {keeper.path} (kept: {'; '.join(keeper.keeper_reasons[:2])})")]
            if COPY_NAME.search(os.path.splitext(f.name)[0]):
                sig.append(Signal("copy-style name", w.copy_name, "name looks like a copy"))
            baskets["exact_duplicates"].append(sc.finish(row, "exact_duplicates", sig, "high", group_key=g.key,
                                                         keeper_id=keeper.id, keeper_path=keeper.path))

    # 2. near-duplicate images ----------------------------------------------------------
    groups["similar_images"] = similar_image_groups(conn, settings.image_hash_distance)
    for g in groups["similar_images"]:
        keeper = g.keeper
        for f in g.others:
            row = rows.get(f.id)
            if row is None or blocked(row):
                hidden += row is not None
                continue
            kp = keeper.width * keeper.height if keeper.width and keeper.height else 0
            fp = f.width * f.height if f.width and f.height else 0
            sig = [Signal("near-duplicate image", w.similar_image, g.evidence[0] + f"; keeper: {keeper.name}")]
            if kp and fp and fp < kp:
                sig.append(Signal("lower resolution", 5, f"{f.width}x{f.height} vs {keeper.width}x{keeper.height}"))
            baskets["similar_images"].append(sc.finish(row, "similar_images", sig, "medium", group_key=g.key,
                                                       keeper_id=keeper.id, keeper_path=keeper.path))

    # 3. similar documents (manual comparison) -----------------------------------------
    groups["similar_documents"] = (similar_document_groups(conn, settings.similar_doc_threshold)
                                   if include_similar_documents else [])
    for g in groups["similar_documents"]:
        for f in g.others:
            row = rows.get(f.id)
            if row is None or blocked(row):
                continue
            sig = [Signal("similar document", 15, "; ".join(g.evidence[:2]) + f"; compare with {g.keeper.name}")]
            baskets["similar_documents"].append(sc.finish(row, "similar_documents", sig, "low", group_key=g.key,
                                                          keeper_id=g.keeper.id, keeper_path=g.keeper.path))

    # 4-7. per-file baskets -------------------------------------------------------------
    for row in rows.values():
        if blocked(row):
            continue
        name, ext, size = row["name"], row["ext"], row["size"]
        age_days = (sc.now - row["mtime_ns"] / 1e9) / 86400

        if size >= settings.large_file_bytes:
            pts = w.large_file_max * min(1.0, math.log2(size / settings.large_file_bytes + 1) / 3)
            sig = [Signal("large file", 20 + pts, f"{_fmt_size(size)} (threshold {_fmt_size(settings.large_file_bytes)})")]
            if row["kind"] in ("video", "archive", "installer"):
                sig.append(Signal("bulky media/archive type", 5, f"{row['kind']} files are often redundant copies"))
            baskets["large_files"].append(sc.finish(row, "large_files", sig, "low"))

        in_dl = "downloads" in library.location_parts(row["path"], sc.roots.get(row["root_id"]))
        if (ext in INSTALLER_EXT or ext in ARCHIVE_EXT) and age_days >= settings.installer_min_age_days:
            if ext in INSTALLER_EXT:
                sig = [Signal("old installer", w.old_installer if in_dl else w.old_installer / 2,
                              f"{ext} setup file, {age_days:.0f} days old" + (" in Downloads" if in_dl else ""))]
            else:
                stem = os.path.join(os.path.dirname(row["path"]), os.path.splitext(name)[0])
                extracted = os.path.isdir(stem)
                if not (in_dl or extracted):
                    sig = []
                else:
                    sig = [Signal("old archive", 10 if in_dl else 5, f"{ext} archive, {age_days:.0f} days old")]
                    if extracted:
                        sig.append(Signal("already extracted", 15, f"folder '{os.path.basename(stem)}' exists next to it"))
            if sig:
                baskets["old_installers"].append(sc.finish(row, "old_installers", sig, "medium"))

        temp_sig = None
        for rx, why in TEMP_STRONG:
            if rx.search(name):
                temp_sig = Signal("temporary file", w.temp_strong, why)
                conf = "high"
                break
        else:
            for rx, why in TEMP_WEAK:
                if rx.search(name):
                    temp_sig = Signal("temporary file", w.temp_weak, why)
                    conf = "medium"
                    break
        if temp_sig is not None:
            baskets["temporary"].append(sc.finish(row, "temporary", [temp_sig], conf))

        if size == 0 and name.lower() not in EMPTY_ALLOWED and row["status"] != "cloud":
            baskets["empty"].append(sc.finish(row, "empty", [Signal("empty file", w.empty_file, "0 bytes")], "medium"))

    for b in baskets.values():
        b.sort(key=lambda c: (-c.score, -c.size))
    return CleanupReport(baskets, groups, hidden, time.time())


def mark_kept(conn: sqlite3.Connection, cands: list[Candidate], never_again: bool = False) -> None:
    """User says 'not junk': learn from it, optionally never suggest these files again."""
    for c in cands:
        library.record_feedback(conn, "not_junk", c.path, c.basket)
        if never_again:
            library.set_pref(conn, c.path, never_suggest=1)

