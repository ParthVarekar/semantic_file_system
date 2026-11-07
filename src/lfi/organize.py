"""Organization suggestions: categories, sub-groups, and collision-safe destination plans.

Category assignment combines transparent evidence, strongest first:
1. the user's own past category choices (explicit override, then nearest-neighbour votes),
2. structural facts (software project, installer, archive, audio/video, screenshot, photo),
3. for documents: zero-shot similarity between the file embedding and a short description of
   each category, plus keyword evidence from the name/title/text.

Within a category, files are clustered (scikit-learn agglomerative clustering, cosine) and each
cluster is labelled from its dominant existing folder name or its top c-TF-IDF terms (the
labelling idea popularised by BERTopic). Only files sitting in clutter locations (Downloads,
Desktop, top of a selected folder) or in folders with mixed content get move proposals; folders
that are already coherent are left alone, and software projects are never split up.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from . import library
from .config import Settings
from .embeddings import EmbeddingBackend
from .safety import is_within, norm
from .textutil import STOPWORDS, folder_tokens, name_tokens

CLUTTER = {"downloads", "desktop", "temp", "tmp", "new folder", "misc", "unsorted", "stuff"}


@dataclass(frozen=True)
class Category:
    name: str
    prototype: str
    keywords: str = ""


DOC_CATEGORIES = [
    Category("College & Coursework",
             "university course lecture notes, class assignment, homework solutions, syllabus, exam preparation, "
             "tutorial sheet, semester project, lab report",
             r"\blecture|syllabus|assignment|homework|\bhw\s?\d|semester|\bexam|quiz|tutorial|\bcourse|lab report|"
             r"\bunit\s?\d|\bnotes\b|experiment"),
    Category("Research & Papers",
             "academic research paper with abstract, introduction, related work, experiments, results and "
             "references; arXiv preprint; journal or conference publication; survey",
             r"\babstract\b|arxiv|\bdoi\b|et al\.|\breferences\b|proceedings|journal|conference|\bieee\b|\bsurvey\b"),
    Category("Finance & Admin",
             "invoice, receipt, bank statement, tax return, salary slip, insurance policy, utility bill payment, "
             "rent or lease agreement, loan, government form, identity document",
             r"invoice|receipt|\bbank\b|statement|\btax\b|salary|payslip|insurance|\bbill\b|\brent\b|lease|"
             r"\bloan\b|\bemi\b|\bgst\b|passport|aadhaar|pan card|policy no"),
    Category("Technical Documentation",
             "software documentation, user manual, installation guide, API reference, datasheet, specification, "
             "configuration instructions",
             r"\bmanual\b|user guide|documentation|datasheet|specification|\bapi\b|readme|installation"),
    Category("Personal & Work Documents",
             "resume or curriculum vitae, cover letter, personal letter, meeting notes, report, plan, itinerary, "
             "recipe, personal records",
             r"resume|curriculum vitae|\bcv\b|cover letter|itinerary|meeting|minutes|recipe|letter"),
]
FIXED = {
    "project": "Projects", "installer": "Installers", "archive": "Archives", "audio": "Music & Audio",
    "video": "Videos", "temporary": "Temporary & Disposable", "screenshot": "Screenshots", "photo": "Photos",
    "image": "Images & Graphics", "scan": "Scans & Photographed Documents", "code": "Code & Scripts", "data": "Data & Config", "other": "Other",
}
ALL_CATEGORIES = [c.name for c in DOC_CATEGORIES] + list(dict.fromkeys(FIXED.values()))
# Folder coherence is judged at family level: a "Pictures" folder holding photos, screenshots and
# graphics is coherent even though its files fall into three categories.
FAMILY = {c.name: "documents" for c in DOC_CATEGORIES} | {
    FIXED["photo"]: "images", FIXED["screenshot"]: "images", FIXED["image"]: "images", FIXED["scan"]: "images",
    FIXED["code"]: "code", FIXED["data"]: "code", FIXED["project"]: "code",
    FIXED["installer"]: "software", FIXED["archive"]: "software",
}
PHOTO_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".raw", ".cr2", ".nef", ".arw", ".dng"}


@dataclass
class Assignment:
    file_id: int
    category: str
    confidence: str
    reasons: list[str]
    margin: float = 0.0


@dataclass
class PlannedMove:
    file_id: int
    src: str
    dst: str
    category: str
    group_key: str
    selected: bool
    confidence: str
    reasons: list[str]
    note: str = ""


@dataclass
class Group:
    key: str
    category: str
    name: str
    file_ids: list[int]
    representatives: list[int]
    reasons: list[str]
    keywords: list[str] = field(default_factory=list)


@dataclass
class OrganizePlan:
    groups: dict[str, Group]
    moves: list[PlannedMove]
    kept: list[tuple[int, str]]
    base_dirs: dict[str, str]

    def selected(self) -> list[PlannedMove]:
        return [m for m in self.moves if m.selected and not m.note.startswith("skip")]


class Categorizer:
    def __init__(self, conn: sqlite3.Connection, backend: EmbeddingBackend | None):
        self.conn = conn
        self.backend = backend
        self.file_vecs: dict[int, np.ndarray] = {}
        self.proto: np.ndarray | None = None
        stores = library.stores(conn)
        if stores is not None and backend is not None and library.active_space(conn)["model"] == backend.model_id:
            ids, mat = stores[1].all()
            self.file_vecs = {int(i): mat[k] for k, i in enumerate(ids)}
            self.proto = backend.embed_documents([c.prototype for c in DOC_CATEGORIES])
        self.prefs = library.prefs_map(conn)
        # learned examples: files the user explicitly categorized
        self.examples: list[tuple[np.ndarray, str]] = []
        for r in conn.execute("SELECT f.id, p.category FROM prefs p JOIN files f ON f.path_key=p.path_key"
                              " WHERE p.category IS NOT NULL"):
            v = self.file_vecs.get(r["id"])
            if v is not None:
                self.examples.append((v, r["category"]))

    def text_of(self, fid: int, chars: int = 3000) -> str:
        rows = self.conn.execute("SELECT text FROM chunks WHERE file_id=? ORDER BY ord LIMIT 3", (fid,)).fetchall()
        return " ".join(r["text"] for r in rows)[:chars]

    def assign(self, row: sqlite3.Row) -> Assignment:
        fid = row["id"]
        pref = self.prefs.get(row["path_key"])
        if pref is not None and pref["category"]:
            return Assignment(fid, pref["category"], "high", ["you chose this category"], 1.0)
        if row["project_root"]:
            return Assignment(fid, FIXED["project"], "high",
                              [f"part of software project '{os.path.basename(row['project_root'])}'"], 1.0)
        kind = row["kind"] or "other"
        if kind == "image":
            tags = (row["img_tags"] or "").lower()
            if row["is_screenshot"]:
                return Assignment(fid, FIXED["screenshot"], "high", ["screenshot (name or screen-sized PNG)"], 1.0)
            if "screenshot" in tags:
                return Assignment(fid, FIXED["screenshot"], "medium", [f"looks like {tags.split(';')[0]} (auto-tag)"], 1.0)
            if any(t in tags for t in ("receipt", "invoice", "scanned document", "printed text", "handwritten",
                                       "whiteboard", "id card", "ticket", "certificate")):
                return Assignment(fid, FIXED["scan"], "medium", [f"looks like {tags.split(';')[0]} (auto-tag)"], 1.0)
            if row["camera"] or row["exif_time"] or row["ext"] in PHOTO_EXT:
                why = f"camera: {row['camera']}" if row["camera"] else "photo format / EXIF date"
                return Assignment(fid, FIXED["photo"], "high" if row["camera"] else "medium", [why], 1.0)
            return Assignment(fid, FIXED["image"], "medium", ["image file"], 1.0)
        if kind in ("installer", "archive", "audio", "video", "temporary"):
            return Assignment(fid, FIXED[kind], "high", [f"{kind} file type ({row['ext']})"], 1.0)
        if kind == "data" or (kind == "code" and not row["text_chars"]):
            return Assignment(fid, FIXED[kind], "medium", [f"{kind} file type ({row['ext']})"], 1.0)

        learned = self._knn_vote(fid)
        if learned:
            cat, votes, k = learned
            return Assignment(fid, cat, "high" if votes >= 3 else "medium",
                              [f"similar to {votes} of {k} files you filed under '{cat}'"], 0.5)
        if kind == "code":
            return Assignment(fid, FIXED["code"], "medium", [f"source code ({row['ext']})"], 1.0)
        if row["status"] in ("error", "timeout", "locked"):
            return Assignment(fid, FIXED["other"], "low", [f"content could not be read ({row['status']})"], 0.0)
        has_text = bool(row["text_chars"])
        text = " ".join(filter(None, [row["title"], name_tokens(row["name"]), folder_tokens(row["path"], 2),
                                      self.text_of(fid)])).lower()
        scores = np.zeros(len(DOC_CATEGORIES))
        reasons: dict[int, list[str]] = defaultdict(list)
        v = self.file_vecs.get(fid)
        sem_best = None
        # Without extracted text the file vector only encodes the file name: too weak to use.
        if has_text and v is not None and self.proto is not None:
            sims = self.proto @ v
            scores += sims
            sem_best = int(np.argmax(sims))
            reasons[sem_best].append(
                f"content resembles '{DOC_CATEGORIES[sem_best].name}' (similarity {sims[sem_best]:.2f})")
        kw_hit: set[int] = set()
        for i, c in enumerate(DOC_CATEGORIES):
            hits = sorted({m.group(0).strip(" .") for m in re.finditer(c.keywords, text)})
            if hits:
                kw_hit.add(i)
                scores[i] += min(0.3, 0.12 * len(hits))
                reasons[i].append("keywords: " + ", ".join(hits[:5]))
        if not kw_hit and sem_best is None:
            if kind == "data":
                return Assignment(fid, FIXED["data"], "low", [f"data file ({row['ext']})"], 0.0)
            return Assignment(fid, FIXED["other"], "low", ["no strong evidence"], 0.0)
        order = np.argsort(-scores)
        margin = float(scores[order[0]] - scores[order[1]])
        best = int(order[0])
        agree = best in kw_hit and (sem_best is None or sem_best == best)
        if agree and (sem_best is not None or len(kw_hit) == 1):
            conf = "high" if margin > 0.1 else "medium"
        elif best in kw_hit or margin > 0.08:
            conf = "medium" if has_text else "low"
        else:
            conf = "low"
        return Assignment(fid, DOC_CATEGORIES[best].name, conf, reasons[best] or ["closest category"], margin)

    def _knn_vote(self, fid: int, k: int = 5, min_sim: float = 0.75):
        v = self.file_vecs.get(fid)
        if v is None or not self.examples:
            return None
        sims = [(float(ev @ v), cat) for ev, cat in self.examples]
        sims.sort(reverse=True)
        top = [c for s, c in sims[:k] if s >= min_sim]
        if not top:
            return None
        cat, votes = Counter(top).most_common(1)[0]
        return (cat, votes, len(top)) if votes / len(top) >= 0.6 else None


_RESERVED = re.compile(r"(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]|conin\$|conout\$)(\..*)?", re.I)


def _clean_label(s: str) -> str:
    """A folder name Windows accepts as typed: no illegal characters, no trailing dot or space
    (Win32 silently drops them), and never a reserved device name such as CON or NUL."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s)
    s = re.sub(r"\s+", " ", s)[:60].strip(" .")
    if _RESERVED.fullmatch(s):
        s += " folder"
    return s or "Misc"


def label_clusters(texts_by_cluster: dict[int, list[str]], top: int = 2) -> dict[int, list[str]]:
    """c-TF-IDF: treat each cluster as one document, rank terms distinctive for it."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    keys = list(texts_by_cluster)
    docs = [" ".join(texts_by_cluster[k]) for k in keys]
    if not any(d.strip() for d in docs):
        return {k: [] for k in keys}
    vec = TfidfVectorizer(stop_words=list(STOPWORDS), token_pattern=r"(?u)\b[^\W\d_]{3,}\b", max_features=5000,
                          sublinear_tf=True)
    try:
        m = vec.fit_transform(docs)
    except ValueError:
        return {k: [] for k in keys}
    terms = np.array(vec.get_feature_names_out())
    out = {}
    for i, k in enumerate(keys):
        row = m[i].toarray().ravel()
        idx = [j for j in np.argsort(-row)[: top * 3] if row[j] > 0]
        out[k] = [str(t) for t in terms[idx][:max(top, 6)]]
    return out


def _clutter_anchor(path: str, root: str) -> tuple[str | None, str]:
    """If ``path`` lives under a clutter folder (or directly in the root), return
    (anchor_dir, relative_path_from_anchor). Otherwise (None, '')."""
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    if len(parts) == 1:
        return root, rel
    for i, p in enumerate(parts[:-1]):
        if p.lower() in CLUTTER:
            anchor = os.path.join(root, *parts[: i + 1])
            return anchor, os.path.join(*parts[i + 1:])
    return None, ""


def build_plan(conn: sqlite3.Connection, settings: Settings, backend: EmbeddingBackend | None,
               root_paths: list[str] | None = None, distance_threshold: float = 0.45) -> OrganizePlan:
    cat = Categorizer(conn, backend)
    roots = [r["path"] for r in library.roots(conn)]
    if root_paths:
        keys = {norm(p) for p in root_paths}
        roots = [r for r in roots if norm(r) in keys]
    rows = conn.execute("SELECT * FROM files WHERE status NOT IN ('cloud')").fetchall()
    root_of = {}
    for r in rows:
        for rp in roots:
            if is_within(r["path_key"], norm(rp)):
                root_of[r["id"]] = rp
                break
    rows = [r for r in rows if r["id"] in root_of]
    by_id = {r["id"]: r for r in rows}
    assign = {r["id"]: cat.assign(r) for r in rows}
    from .duplicates import exact_groups

    dup_copies = {f.id: g.keeper.path for g in exact_groups(conn) for f in g.others}

    # folder purity: how coherent is each existing folder (by family)?
    folder_cats: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        folder_cats[r["parent_key"]][FAMILY.get(assign[r["id"]].category, assign[r["id"]].category)] += 1

    # --- decide which files are candidates for moving --------------------------------------
    kept: list[tuple[int, str]] = []
    movable: list[tuple[int, str, str]] = []  # (file id, anchor dir, relative path under anchor)
    unit_cat: dict[tuple[str, str], Counter] = defaultdict(Counter)  # (anchor, top folder) -> categories
    for r in rows:
        a = assign[r["id"]]
        pref = cat.prefs.get(r["path_key"])
        if pref is not None and (pref["pinned"] or pref["never_suggest"]):
            kept.append((r["id"], "pinned / excluded by you"))
            continue
        if r["project_root"]:
            kept.append((r["id"], f"inside project '{os.path.basename(r['project_root'])}' (structure preserved)"))
            continue
        if r["id"] in dup_copies:
            kept.append((r["id"], f"duplicate of {dup_copies[r['id']]} - review it under Duplicates instead"))
            continue
        if (r["kind"] == "temporary") or r["size"] == 0:
            kept.append((r["id"], "temporary or empty file - review it under Cleanup instead"))
            continue
        root = root_of[r["id"]]
        anchor, rel = _clutter_anchor(r["path"], root)
        if anchor is not None:
            movable.append((r["id"], anchor, rel))
            top = rel.split(os.sep)[0] if os.sep in rel else ""
            if top:
                unit_cat[(anchor, top)][a.category] += 1
            continue
        fc = folder_cats[r["parent_key"]]
        purity = fc[FAMILY.get(a.category, a.category)] / max(1, sum(fc.values()))
        if purity >= 0.5 or sum(fc.values()) < 3:
            kept.append((r["id"], f"already in a coherent folder ({os.path.basename(os.path.dirname(r['path']))})"))
        else:
            movable.append((r["id"], os.path.dirname(r["path"]), r["name"]))

    # --- group movable files: folder units keep their relative structure --------------------
    groups: dict[str, Group] = {}
    loose_by_cat: dict[str, list[int]] = defaultdict(list)
    move_info: dict[int, tuple[str, str, str]] = {}
    for fid, anchor, rel in movable:
        top = rel.split(os.sep)[0] if os.sep in rel else ""
        if top:
            c = unit_cat[(anchor, top)].most_common(1)[0][0]
            key = f"unit:{norm(os.path.join(anchor, top))}"
            g = groups.get(key)
            if g is None:
                g = groups[key] = Group(key, c, _clean_label(top), [], [],
                                        [f"folder '{top}' moved as a unit (internal structure preserved)"])
            g.file_ids.append(fid)
            move_info[fid] = (anchor, rel, key)
        else:
            loose_by_cat[assign[fid].category].append(fid)
            move_info[fid] = (anchor, rel, "")

    for category, fids in loose_by_cat.items():
        clusters = _cluster(fids, cat.file_vecs, distance_threshold)
        texts = {ci: [name_tokens(by_id[f]["name"]) + " " + (by_id[f]["title"] or "") + " " + cat.text_of(f, 600)
                      for f in members] for ci, members in clusters.items()}
        labels = label_clusters(texts) if len(clusters) > 1 else {ci: [] for ci in clusters}
        for ci, members in clusters.items():
            terms = labels.get(ci, [])
            name = ""
            if len(clusters) > 1 and len(members) >= 2:
                folder_names = Counter(os.path.basename(os.path.dirname(by_id[f]["path"])) for f in members)
                fname, n = folder_names.most_common(1)[0]
                name = fname if n / len(members) >= 0.6 and fname.lower() not in CLUTTER else " ".join(
                    t.title() for t in terms[:2])
            key = f"cat:{category}:{ci}"
            reasons = [f"{len(members)} file(s) assigned to '{category}'"]
            if terms:
                reasons.append("distinctive terms: " + ", ".join(terms[:6]))
            groups[key] = Group(key, category, _clean_label(name) if name else "", members,
                                _representatives(members, cat.file_vecs), reasons, terms)
            for f in members:
                a, rel, _ = move_info[f]
                move_info[f] = (a, rel, key)
    for g in groups.values():
        if not g.representatives:
            g.representatives = _representatives(g.file_ids, cat.file_vecs)

    base_dirs = {rp: (settings.organize_base or os.path.join(rp, "Organized")) for rp in roots}
    moves: list[PlannedMove] = []
    for fid, (anchor, rel, gkey) in move_info.items():
        r = by_id[fid]
        a = assign[fid]
        g = groups[gkey]
        in_clutter = _clutter_anchor(r["path"], root_of[fid])[0] is not None
        reasons = list(a.reasons)
        if not in_clutter:
            reasons.append("its folder has mixed content")
        selected = in_clutter and a.confidence in ("high", "medium")
        moves.append(PlannedMove(fid, r["path"], "", g.category, gkey, selected, a.confidence, reasons))
    plan = OrganizePlan(groups, moves, kept, base_dirs)
    refresh_destinations(conn, plan, root_of)
    return plan


def _cluster(fids: list[int], vecs: dict[int, np.ndarray], threshold: float) -> dict[int, list[int]]:
    have = [f for f in fids if f in vecs]
    if len(have) < 4:
        return {0: list(fids)}
    from sklearn.cluster import AgglomerativeClustering

    mat = np.vstack([vecs[f] for f in have])
    labels = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                     distance_threshold=threshold).fit_predict(mat)
    out: dict[int, list[int]] = defaultdict(list)
    for f, lab in zip(have, labels):
        out[int(lab)].append(f)
    # tiny clusters merge into a shared remainder group
    rest = [f for lab in list(out) if len(out[lab]) < 2 for f in out.pop(lab)]
    rest += [f for f in fids if f not in vecs]
    if rest:
        out[-1].extend(rest)
    return dict(out)


def _representatives(fids: list[int], vecs: dict[int, np.ndarray], n: int = 3) -> list[int]:
    have = [f for f in fids if f in vecs]
    if not have:
        return fids[:n]
    mat = np.vstack([vecs[f] for f in have])
    c = mat.mean(axis=0)
    order = np.argsort(-(mat @ c))
    return [have[i] for i in order[:n]]


def group_folder(g: Group) -> str:
    return os.path.join(_clean_label(g.category), g.name) if g.name else _clean_label(g.category)


def refresh_destinations(conn: sqlite3.Connection, plan: OrganizePlan, root_of: dict[int, str] | None = None) -> None:
    """(Re)compute destination paths after edits: never collide with existing files or with each
    other; flag identical files that already exist at the destination."""
    if root_of is None:
        root_of = {}
        roots = [r["path"] for r in library.roots(conn)]
        for m in plan.moves:
            root_of[m.file_id] = next((rp for rp in roots if is_within(norm(m.src), norm(rp))), roots[0] if roots else "")
    taken: set[str] = set()
    for m in sorted(plan.moves, key=lambda x: x.src.lower()):
        g = plan.groups[m.group_key]
        base = plan.base_dirs.get(root_of.get(m.file_id, ""), "") or os.path.join(root_of.get(m.file_id, ""), "Organized")
        if g.key.startswith("unit:"):
            anchor, rel = _clutter_anchor(m.src, root_of[m.file_id])
            dst = os.path.join(base, _clean_label(g.category), rel)
        else:
            dst = os.path.join(base, group_folder(g), os.path.basename(m.src))
        m.note = ""
        if norm(dst) == norm(m.src):
            m.note = "skip: already in place"
        else:
            cand = dst
            i = 2
            while norm(cand) in taken or os.path.lexists(cand):
                if os.path.lexists(cand) and norm(cand) not in taken:
                    row = conn.execute("SELECT hash FROM files WHERE path_key=?", (norm(cand),)).fetchone()
                    me = conn.execute("SELECT hash FROM files WHERE id=?", (m.file_id,)).fetchone()
                    if row and me and row["hash"] and row["hash"] == me["hash"]:
                        m.note = "skip: identical file already at destination (see Duplicates)"
                        break
                stem, ext = os.path.splitext(dst)
                cand = f"{stem} ({i}){ext}"
                i += 1
            if not m.note and cand != dst:
                m.note = f"renamed to avoid a name collision: {os.path.basename(cand)}"
            dst = cand
        m.dst = dst
        taken.add(norm(dst))


# ---- plan editing --------------------------------------------------------------------------

def rename_group(conn, plan: OrganizePlan, key: str, new_name: str, new_category: str | None = None) -> None:
    g = plan.groups[key]
    g.name = _clean_label(new_name) if new_name else ""
    if new_category:
        g.category = _clean_label(new_category)
        for m in plan.moves:
            if m.group_key == key:
                m.category = g.category
    refresh_destinations(conn, plan)


def merge_groups(conn, plan: OrganizePlan, into: str, other: str) -> None:
    a, b = plan.groups[into], plan.groups.pop(other)
    a.file_ids += b.file_ids
    a.reasons.append(f"merged with '{b.name or b.category}'")
    for m in plan.moves:
        if m.group_key == other:
            m.group_key, m.category = into, a.category
    refresh_destinations(conn, plan)


def split_group(conn, plan: OrganizePlan, key: str, file_ids: list[int], new_name: str) -> str:
    g = plan.groups[key]
    nk = f"{key}:split{len(plan.groups)}"
    moved = [f for f in g.file_ids if f in set(file_ids)]
    g.file_ids = [f for f in g.file_ids if f not in set(file_ids)]
    plan.groups[nk] = Group(nk, g.category, _clean_label(new_name), moved, moved[:3], [f"split from '{g.name or g.category}'"])
    for m in plan.moves:
        if m.file_id in set(moved):
            m.group_key = nk
    refresh_destinations(conn, plan)
    return nk


def set_category(conn, plan: OrganizePlan, file_id: int, category: str) -> None:
    """Record the user's category choice (learned for future suggestions) and move the file
    into a group of that category in the current plan."""
    m = next(x for x in plan.moves if x.file_id == file_id)
    library.set_pref(conn, m.src, category=category)
    library.record_feedback(conn, "category", m.src, None, category)
    target = next((k for k, g in plan.groups.items() if g.category == category and not k.startswith("unit:")), None)
    if target is None:
        target = f"cat:{category}:user"
        plan.groups[target] = Group(target, category, "", [], [], ["chosen by you"])
    old = plan.groups[m.group_key]
    if file_id in old.file_ids:
        old.file_ids.remove(file_id)
    plan.groups[target].file_ids.append(file_id)
    m.group_key, m.category = target, category
    m.reasons = ["you chose this category"]
    m.confidence = "high"
    refresh_destinations(conn, plan)
