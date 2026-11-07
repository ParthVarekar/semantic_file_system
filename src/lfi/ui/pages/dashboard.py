"""Dashboard: every number here is a live query against the local index."""

from __future__ import annotations

import json
import os

from PySide6.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ... import library
from ..core import AppContext, fmt_size, fmt_time
from ..widgets import BarList, Card, heading, note

KIND_NAMES = {"document": "Documents", "code": "Code", "data": "Data & config", "image": "Images", "audio": "Audio",
              "video": "Video", "archive": "Archives", "installer": "Installers", "temporary": "Temporary",
              "other": "Other"}


class DashboardPage(QWidget):
    def __init__(self, ctx: AppContext, go):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Dashboard", 18))
        self.welcome = QGroupBox("Get started")
        wl = QVBoxLayout(self.welcome)
        wl.addWidget(note("Choose one or more folders to index. Indexing only reads files - nothing is moved, "
                          "renamed or deleted, and nothing leaves this computer."))
        b = QPushButton("Add a folder...")
        b.clicked.connect(lambda: go("folders"))
        wl.addWidget(b)
        lay.addWidget(self.welcome)

        grid = QGridLayout()
        self.c_files = Card("Files indexed")
        self.c_size = Card("Total size")
        self.c_dupes = Card("Exact duplicates")
        self.c_errors = Card("Problems")
        self.c_last = Card("Last index run")
        self.c_model = Card("Search mode")
        for i, c in enumerate([self.c_files, self.c_size, self.c_dupes, self.c_errors, self.c_last, self.c_model]):
            grid.addWidget(c, i // 3, i % 3)
        lay.addLayout(grid)

        row = QHBoxLayout()
        g1 = QGroupBox("By type (size)")
        self.kinds = BarList()
        QVBoxLayout(g1).addWidget(self.kinds)
        g2 = QGroupBox("Largest folders")
        self.folders = BarList()
        QVBoxLayout(g2).addWidget(self.folders)
        row.addWidget(g1, 1)
        row.addWidget(g2, 1)
        lay.addLayout(row)

        btns = QHBoxLayout()
        for label, page in (("Index now", "folders"), ("Review cleanup", "cleanup"), ("Find duplicates", "duplicates"),
                            ("Organize", "organize")):
            bb = QPushButton(label)
            bb.clicked.connect(lambda _=False, p=page: go(p))
            btns.addWidget(bb)
        btns.addStretch(1)
        lay.addLayout(btns)
        lay.addStretch(1)
        ctx.data_changed.connect(lambda _: self.refresh())
        ctx.backend_changed.connect(self.refresh)

    def refresh(self) -> None:
        c = self.ctx.conn
        has_roots = bool(library.roots(c))
        self.welcome.setVisible(not has_roots)
        n = c.execute("SELECT count(*) AS n, coalesce(sum(size),0) AS b FROM files").fetchone()
        emb = c.execute("SELECT count(*) FROM files WHERE stage=2").fetchone()[0]
        self.c_files.set(f"{n['n']:,}", f"{emb:,} with semantic vectors" if n["n"] else "nothing indexed yet")
        nroots = len(library.roots(c))
        self.c_size.set(fmt_size(n["b"]), f"across {nroots} selected folder(s)")
        d = c.execute("SELECT count(*) AS groups, coalesce(sum(extra),0) AS bytes FROM (SELECT hash, count(*) AS k,"
                      " (count(*)-1)*max(size) AS extra FROM files WHERE hash IS NOT NULL AND size>0 GROUP BY hash"
                      " HAVING count(*)>1)").fetchone()
        self.c_dupes.set(fmt_size(d["bytes"]), f"{d['groups']:,} groups of identical files (before hard-link check)")
        bad = dict(c.execute("SELECT status, count(*) FROM files WHERE status NOT IN ('ok','new','cloud',"
                             "'metadata_only') GROUP BY status").fetchall())
        self.c_errors.set(f"{sum(bad.values()):,}", ", ".join(f"{v} {k}" for k, v in bad.items()) or "none")
        job = c.execute("SELECT * FROM jobs WHERE kind='index' ORDER BY id DESC LIMIT 1").fetchone()
        if job:
            st = json.loads(job["stats"] or "{}")
            self.c_last.set(fmt_time(job["finished_at"] or job["started_at"]),
                            f"{job['status']} - {st.get('elapsed_s', 0)} s, {st.get('new', 0)} new, "
                            f"{st.get('changed', 0)} changed, {st.get('removed', 0)} removed")
        else:
            self.c_last.set("never")
        b = self.ctx.backend
        self.c_model.set("Semantic + keyword" if b else "Keyword only",
                         self.ctx.backend_status().split(": ", 1)[-1])
        rows = c.execute("SELECT coalesce(kind,'other') AS k, count(*) AS n, sum(size) AS b FROM files GROUP BY k"
                         " ORDER BY b DESC").fetchall()
        self.kinds.set_rows([(KIND_NAMES.get(r["k"], r["k"]), r["b"] or 0, f"{fmt_size(r['b'])} - {r['n']:,} files")
                             for r in rows])
        top = c.execute("SELECT parent_key, count(*) AS n, sum(size) AS b, min(path) AS p FROM files"
                        " GROUP BY parent_key ORDER BY b DESC LIMIT 8").fetchall()
        self.folders.set_rows([(os.path.basename(os.path.dirname(r["p"])) or r["p"], r["b"] or 0,
                                f"{fmt_size(r['b'])} - {r['n']:,}") for r in top])

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)

