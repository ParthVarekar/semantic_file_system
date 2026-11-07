"""Exact duplicates, near-duplicate images, and similar documents, with keeper selection."""

from __future__ import annotations

import html
import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter, QTabWidget, QTextBrowser,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import db
from ...duplicates import exact_groups, similar_document_groups, similar_image_groups
from ..core import AppContext, confirm, fmt_size, fmt_time, open_file, reveal
from ..widgets import button_row, heading, note

KINDS = [("exact", "Exact duplicates", "Byte-identical (same BLAKE3 hash). Non-keepers are pre-checked; the keeper "
                                        "is re-verified before anything moves."),
         ("image", "Similar images", "Visually similar (perceptual hash). Nothing is pre-checked - edits and crops "
                                     "can matter."),
         ("document", "Similar documents", "Related text, different bytes: possibly different versions. Compare "
                                           "before deciding. Nothing is pre-checked.")]


class GroupTree(QTreeWidget):
    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind
        self.setHeaderLabels(["File", "Size", "Modified", "Keeper signals"])
        self.setColumnWidth(0, 460)
        self.setColumnWidth(1, 90)
        self.setColumnWidth(2, 130)
        self.setAlternatingRowColors(True)
        if kind == "image":
            self.setIconSize(QSize(64, 64))
        self.groups = []

    def load(self, groups) -> None:
        self.clear()
        self.groups = groups
        for gi, g in enumerate(groups):
            top = QTreeWidgetItem()
            top.setData(0, Qt.ItemDataRole.UserRole, (gi, None))
            self.fill_group(top, {})
            self.addTopLevelItem(top)
            top.setExpanded(gi < 30)

    def fill_group(self, top: QTreeWidgetItem, states: dict) -> None:
        """(Re)build one group's rows; ``states`` maps file id -> check state to keep."""
        gi = top.data(0, Qt.ItemDataRole.UserRole)[0]
        g = self.groups[gi]
        top.takeChildren()
        top.setText(0, f"{len(g.files)} files - reclaim {fmt_size(g.reclaimable())}"
                    + ("  (keeper is a tie - review)" if g.keeper_uncertain and g.kind != "document" else ""))
        top.setText(3, "; ".join(g.evidence[:2]))
        for f in g.files:
                keeper = f.id == g.keeper_id
                label = ("KEEP  " if keeper else "") + f.path
                if f.shares_keeper:
                    label += "   [hard link of the kept copy: frees no space]"
                elif f.hardlink_of or f.nlink > 1:
                    label += "   [hard link: space is freed only when all its links are removed]"
                it = QTreeWidgetItem([label, fmt_size(f.size), fmt_time(f.mtime_ns), "; ".join(f.keeper_reasons)])
                it.setData(0, Qt.ItemDataRole.UserRole, (gi, f.id))
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                pre = self.kind == "exact" and not keeper and not f.shares_keeper
                it.setCheckState(0, states.get(f.id, Qt.CheckState.Checked if pre else Qt.CheckState.Unchecked))
                if keeper:
                    font = it.font(0)
                    font.setBold(True)
                    it.setFont(0, font)
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                    it.setData(0, Qt.ItemDataRole.CheckStateRole, None)
                if self.kind == "image" and os.path.exists(f.path) and f.size < 30 * 1024 * 1024:
                    pm = QPixmap(f.path)
                    if not pm.isNull():
                        it.setIcon(0, QIcon(pm.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio)))
                top.addChild(it)

    def checked(self) -> list[tuple[int, int]]:
        """(file_id, keeper_id) for checked non-keeper files."""
        out = []
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            for j in range(top.childCount()):
                it = top.child(j)
                gi, fid = it.data(0, Qt.ItemDataRole.UserRole)
                g = self.groups[gi]
                if fid != g.keeper_id and it.checkState(0) == Qt.CheckState.Checked:
                    out.append((fid, g.keeper_id))
        return out

    def current_file(self):
        it = self.currentItem()
        if it is None:
            return None, None
        gi, fid = it.data(0, Qt.ItemDataRole.UserRole)
        return self.groups[gi], fid


class DuplicatesPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Duplicates", 18))
        top = QHBoxLayout()
        self.scan = QPushButton("Find duplicates")
        self.scan.setObjectName("primary")
        self.scan.clicked.connect(self.run)
        self.status = QLabel("Uses hashes and perceptual hashes already computed during indexing.")
        top.addWidget(self.scan)
        top.addWidget(self.status, 1)
        lay.addLayout(top)
        self.tabs = QTabWidget()
        self.trees: dict[str, GroupTree] = {}
        for kind, label, desc in KINDS:
            w = QWidget()
            wl = QVBoxLayout(w)
            wl.addWidget(note(desc))
            split = QSplitter(Qt.Orientation.Vertical)
            tree = GroupTree(kind)
            tree.itemSelectionChanged.connect(lambda k=kind: self.show(k))
            split.addWidget(tree)
            if kind == "document":
                cmp_ = QSplitter(Qt.Orientation.Horizontal)
                self.left, self.right = QTextBrowser(), QTextBrowser()
                cmp_.addWidget(self.left)
                cmp_.addWidget(self.right)
                split.addWidget(cmp_)
            wl.addWidget(split, 1)
            self.trees[kind] = tree
            self.tabs.addTab(w, label)
        lay.addWidget(self.tabs, 1)
        bk, bq, bo, br = (QPushButton("Make selected the keeper"), QPushButton("Quarantine checked..."),
                          QPushButton("Open"), QPushButton("Show in Explorer"))
        bq.setObjectName("primary")
        bk.clicked.connect(self.make_keeper)
        bq.clicked.connect(self.quarantine)
        bo.clicked.connect(lambda: self._path() and open_file(self._path()))
        br.clicked.connect(lambda: self._path() and reveal(self._path()))
        lay.addLayout(button_row(bk, bq, bo, br))
        self.keeper_btn, self.quarantine_btn = bk, bq
        self.has_results = False
        ctx.data_changed.connect(self._data_changed)
        ctx.busy_changed.connect(lambda _: self._sync())
        self._sync()

    def _sync(self) -> None:
        self.keeper_btn.setEnabled(self.has_results)
        self.quarantine_btn.setEnabled(self.has_results and not self.ctx.fileop_running)

    def _data_changed(self, what: str) -> None:
        if what in ("index", "files", "quarantine") and self.has_results:
            self.mark_stale()

    def mark_stale(self) -> None:
        """Files were moved, restored or re-indexed elsewhere: these groups no longer hold."""
        self.has_results = False
        for tree in self.trees.values():
            tree.load([])
        self.left.clear()
        self.right.clear()
        self.status.setText("Files changed - Find duplicates again to see up-to-date groups.")
        self._sync()

    def _tree(self) -> GroupTree:
        return self.trees[KINDS[self.tabs.currentIndex()][0]]

    def _path(self):
        g, fid = self._tree().current_file()
        if g is None or fid is None:
            return None
        return next(f.path for f in g.files if f.id == fid)

    def run(self) -> None:
        self.scan.setEnabled(False)
        self.status.setText("Grouping...")
        s = self.ctx.settings

        def work(job):
            conn = db.connect()
            try:
                return (exact_groups(conn), similar_image_groups(conn, s.image_hash_distance),
                        similar_document_groups(conn, s.similar_doc_threshold))
            finally:
                conn.close()

        self.ctx.run(work, self.loaded, on_finished=lambda: self.scan.setEnabled(True))

    def loaded(self, res) -> None:
        ex, im, doc = res
        self.trees["exact"].load(ex)
        self.trees["image"].load(im)
        self.trees["document"].load(doc)
        rec = sum(g.reclaimable() for g in ex)
        self.status.setText(f"{len(ex)} exact groups ({fmt_size(rec)} reclaimable), {len(im)} similar-image groups, "
                            f"{len(doc)} similar-document groups.")
        self.has_results = True
        self._sync()

    def show(self, kind: str) -> None:
        if kind != "document":
            return
        g, fid = self.trees["document"].current_file()
        if g is None:
            return
        other = fid if fid is not None else g.others[0].id
        conn = self.ctx.conn

        def txt(i):
            rows = conn.execute("SELECT text FROM chunks WHERE file_id=? ORDER BY ord LIMIT 12", (i,)).fetchall()
            return "\n\n".join(r["text"] for r in rows)

        k = g.keeper
        o = next(f for f in g.files if f.id == other)
        self.left.setHtml(f"<b>{html.escape(k.name)}</b><br><small>{html.escape(k.path)}</small><pre>"
                          f"{html.escape(txt(k.id))[:20000]}</pre>")
        self.right.setHtml(f"<b>{html.escape(o.name)}</b><br><small>{html.escape(o.path)}</small><pre>"
                           f"{html.escape(txt(o.id))[:20000]}</pre>")

    def make_keeper(self) -> None:
        """Keep the selected copy instead; only this group changes, other check marks stay as they are."""
        tree = self._tree()
        g, fid = tree.current_file()
        if g is None or fid is None or fid == g.keeper_id:
            return
        top = tree.currentItem().parent()
        states = {top.child(j).data(0, Qt.ItemDataRole.UserRole)[1]: top.child(j).checkState(0)
                  for j in range(top.childCount())}
        states[g.keeper_id] = Qt.CheckState.Unchecked  # the previous keeper is never checked by surprise
        g.keeper_id = fid
        for f in g.files:
            if f.id == fid:
                f.keeper_reasons = ["chosen by you"] + [r for r in f.keeper_reasons if "chosen" not in r]
        tree.fill_group(top, states)
        tree.setCurrentItem(next(top.child(j) for j in range(top.childCount())
                                 if top.child(j).data(0, Qt.ItemDataRole.UserRole)[1] == fid))

    def quarantine(self) -> None:
        if self.ctx.fileop_refused(self):
            return
        tree = self._tree()
        pairs = tree.checked()
        if not pairs:
            QMessageBox.information(self, "Nothing checked", "Check the copies you want to quarantine.")
            return
        by_id = {f.id: f for g in tree.groups for f in g.files}
        total = sum(by_id[f].size for f, _ in pairs)
        if not confirm(self, "Quarantine copies",
                       f"Move {len(pairs)} file(s) ({fmt_size(total)}) into quarantine?\n\n"
                       + ("Each kept copy is re-hashed first to prove it is still identical. " if tree.kind == "exact"
                          else "These are similar, not identical - make sure you have compared them. ")
                       + "You can restore them later.", "\n".join(by_id[f].path for f, _ in pairs[:200])):
            return
        keepers = {f: k for f, k in pairs} if tree.kind == "exact" else None
        basket = {"exact": "exact_duplicates", "image": "similar_images", "document": "similar_documents"}[tree.kind]
        ids = [f for f, _ in pairs]

        def done(res):
            msg = f"Quarantined {len(res.ok)} file(s)."
            if res.failed:
                msg += f"\n\n{len(res.failed)} left untouched:\n" + "\n".join(
                    f"- {os.path.basename(r.src)}: {r.message}" for r in res.failed[:15])
            QMessageBox.information(self, "Quarantine", msg)
            self.ctx.data_changed.emit("quarantine")
            self.run()

        if self.ctx.run_fileop(self, "Quarantine", "Quarantining files...",
                               lambda fo: fo.quarantine(ids, f"duplicates: {tree.kind}", basket, keepers), done):
            self.status.setText(f"Quarantining {len(ids)} file(s)...")
