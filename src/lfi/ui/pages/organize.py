"""Organization suggestions: review groups, edit them, preview destinations, apply, undo."""

from __future__ import annotations

import html
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import db, library
from ...organize import (ALL_CATEGORIES, build_plan, group_folder, merge_groups, refresh_destinations, rename_group,
                         set_category, split_group)
from ..core import AppContext, confirm, fmt_size
from ..widgets import SortItem, button_row, end_fill, heading, make_table, note

CONF_ORDER = {"high": 0, "medium": 1, "low": 2}


class OrganizePage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.plan = None
        self.last_batch = None
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Organize", 18))
        lay.addWidget(note("Suggestions only: nothing moves until you click Apply. Files already in coherent "
                           "folders and anything inside software projects stay where they are. Every applied "
                           "batch can be undone."))
        top = QHBoxLayout()
        self.gen = QPushButton("Generate suggestions")
        self.gen.setObjectName("primary")
        self.gen.clicked.connect(self.run)
        top.addWidget(self.gen)
        top.addWidget(QLabel("Destination base (empty = <folder>\\Organized):"))
        self.base = QLineEdit(ctx.settings.organize_base)
        bb = QPushButton("Browse...")
        bb.clicked.connect(self.browse)
        top.addWidget(self.base, 1)
        top.addWidget(bb)
        lay.addLayout(top)
        self.status = QLabel("")
        lay.addWidget(self.status)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Group / file", "Size", "Confidence"])
        self.tree.setColumnWidth(0, 380)
        self.tree.itemSelectionChanged.connect(self.show_group)
        self.tree.itemChanged.connect(self._toggled)
        ll.addWidget(self.tree, 1)
        b1, b2, b3, b4, b5 = (QPushButton("Rename group..."), QPushButton("Change category..."),
                              QPushButton("Merge into..."), QPushButton("Split checked..."),
                              QPushButton("Exclude file"))
        b1.clicked.connect(self.rename)
        b2.clicked.connect(self.change_category)
        b3.clicked.connect(self.merge)
        b4.clicked.connect(self.split)
        b5.clicked.connect(self.exclude)
        ll.addLayout(button_row(b1, b2, b3, b4, b5))
        split.addWidget(left)
        right = QSplitter(Qt.Orientation.Vertical)
        self.info = QTextBrowser()
        right.addWidget(self.info)
        self.preview = make_table(["From", "To", "Note"], stretch=1)
        right.addWidget(self.preview)
        split.addWidget(right)
        split.setSizes([520, 620])
        lay.addWidget(split, 1)
        self.apply_btn = QPushButton("Apply checked moves...")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self.apply)
        self.undo_btn = QPushButton("Undo last applied batch")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo)
        lay.addLayout(button_row(self.apply_btn, self.undo_btn))
        ctx.busy_changed.connect(lambda _: self._sync())
        ctx.data_changed.connect(lambda _: self._sync())  # e.g. the batch was undone from Activity

    def showEvent(self, e):
        self._sync()
        super().showEvent(e)

    def _last_status(self) -> str | None:
        if self.last_batch is None:
            return None
        row = self.ctx.conn.execute("SELECT status FROM batches WHERE id=?", (self.last_batch,)).fetchone()
        return row["status"] if row else None

    def _sync(self) -> None:
        busy = self.ctx.fileop_running
        self.apply_btn.setEnabled(not busy)
        st = self._last_status()
        self.undo_btn.setEnabled(not busy and st is not None and st != "undone")

    def browse(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "Destination base folder")
        if p:
            self.base.setText(os.path.normpath(p))

    def run(self) -> None:
        self.ctx.settings.organize_base = self.base.text().strip()
        self.ctx.save_settings()
        if self.ctx.settings.organize_base:
            pol = self.ctx.policy()
            if pol.check(self.ctx.settings.organize_base):
                QMessageBox.warning(self, "Destination not allowed",
                                    "The destination must be inside one of the indexed folders.")
                return
        self.gen.setEnabled(False)
        self.status.setText("Building suggestions (categorizing, clustering, checking collisions)...")
        settings, backend = self.ctx.settings, self.ctx.backend

        def work(job):
            conn = db.connect()
            try:
                return build_plan(conn, settings, backend)
            finally:
                conn.close()

        self.ctx.run(work, self.loaded, on_finished=lambda: self.gen.setEnabled(True))

    def loaded(self, plan) -> None:
        self.plan = plan
        self.fill()

    def fill(self) -> None:
        p = self.plan
        self.tree.blockSignals(True)
        self.tree.clear()
        conn = self.ctx.conn
        sizes = {}
        ids = [m.file_id for m in p.moves]
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            sizes.update(dict(conn.execute(f"SELECT id, size FROM files WHERE id IN ({','.join('?' * len(part))})",
                                           part).fetchall()))
        for key, g in sorted(p.groups.items(), key=lambda kv: (kv[1].category, kv[1].name)):
            ms = [m for m in p.moves if m.group_key == key]
            if not ms:
                continue
            top = QTreeWidgetItem([f"{group_folder(g)}  ({len(ms)})", fmt_size(sum(sizes.get(m.file_id, 0) for m in ms)),
                                   ""])
            top.setData(0, Qt.ItemDataRole.UserRole, ("group", key))
            for m in sorted(ms, key=lambda x: (CONF_ORDER.get(x.confidence, 3), x.src.lower())):
                it = QTreeWidgetItem([os.path.basename(m.src), fmt_size(sizes.get(m.file_id, 0)), m.confidence])
                it.setData(0, Qt.ItemDataRole.UserRole, ("file", m.file_id))
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Checked if m.selected else Qt.CheckState.Unchecked)
                it.setToolTip(0, m.src)
                top.addChild(it)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        self.tree.blockSignals(False)
        self.update_preview()

    def _toggled(self, item, col) -> None:
        kind, val = item.data(0, Qt.ItemDataRole.UserRole)
        if kind == "file" and self.plan:
            m = next(x for x in self.plan.moves if x.file_id == val)
            m.selected = item.checkState(0) == Qt.CheckState.Checked
            self.update_preview()

    def update_preview(self) -> None:
        p = self.plan
        sel = p.selected()
        t = self.preview
        t.setSortingEnabled(False)
        t.setRowCount(len(sel))
        for i, m in enumerate(sel):
            t.setItem(i, 0, SortItem(m.src))
            t.setItem(i, 1, SortItem(m.dst))
            t.setItem(i, 2, SortItem(m.note))
        end_fill(t)
        renamed = sum(1 for m in sel if m.note.startswith("renamed"))
        self.status.setText(f"{len(sel)} move(s) checked of {len(p.moves)} suggested; {len(p.kept)} file(s) stay in "
                            f"place" + (f"; {renamed} renamed to avoid collisions" if renamed else "")
                            + ". Nothing has been moved yet.")

    def _current(self):
        it = self.tree.currentItem()
        if it is None:
            return None, None
        kind, val = it.data(0, Qt.ItemDataRole.UserRole)
        if kind == "group":
            return val, None
        m = next(x for x in self.plan.moves if x.file_id == val)
        return m.group_key, m

    def show_group(self) -> None:
        key, m = self._current()
        if key is None:
            return
        g = self.plan.groups[key]
        reps = []
        for fid in g.representatives[:3]:
            r = self.ctx.conn.execute("SELECT name FROM files WHERE id=?", (fid,)).fetchone()
            if r:
                reps.append(html.escape(r["name"]))
        h = (f"<h3>{html.escape(group_folder(g))}</h3><p><b>Why:</b> {html.escape('; '.join(g.reasons))}</p>"
             f"<p><b>Representative files:</b> {', '.join(reps) or '-'}</p>")
        if g.keywords:
            h += f"<p><b>Distinctive terms:</b> {html.escape(', '.join(g.keywords[:8]))}</p>"
        if m is not None:
            h += (f"<hr><p><b>{html.escape(os.path.basename(m.src))}</b> ({m.confidence} confidence)<br>"
                  f"<small>{html.escape(m.src)}<br>&rarr; {html.escape(m.dst)}</small></p>"
                  f"<p>{html.escape('; '.join(m.reasons))}</p><p><i>{html.escape(m.note)}</i></p>")
        self.info.setHtml(h)

    def _need_plan(self) -> bool:
        if self.plan is None:
            QMessageBox.information(self, "No suggestions", "Generate suggestions first.")
            return False
        return True

    def rename(self) -> None:
        if not self._need_plan():
            return
        key, _ = self._current()
        if key is None:
            return
        g = self.plan.groups[key]
        name, ok = QInputDialog.getText(self, "Rename group", "Folder name (inside the category):", text=g.name)
        if ok:
            rename_group(self.ctx.conn, self.plan, key, name)
            self.fill()

    def change_category(self) -> None:
        if not self._need_plan():
            return
        key, m = self._current()
        if key is None:
            return
        cats = sorted(set(ALL_CATEGORIES) | {g.category for g in self.plan.groups.values()})
        cat, ok = QInputDialog.getItem(self, "Change category", "Category (you can type a new one):", cats,
                                       cats.index(self.plan.groups[key].category)
                                       if self.plan.groups[key].category in cats else 0, True)
        if not ok or not cat:
            return
        if m is not None:
            set_category(self.ctx.conn, self.plan, m.file_id, cat)  # learned for next time
        else:
            rename_group(self.ctx.conn, self.plan, key, self.plan.groups[key].name, cat)
            for mm in self.plan.moves:
                if mm.group_key == key:
                    library.set_pref(self.ctx.conn, mm.src, category=cat)
        self.fill()

    def merge(self) -> None:
        if not self._need_plan():
            return
        key, _ = self._current()
        if key is None:
            return
        others = {f"{group_folder(g)}": k for k, g in self.plan.groups.items() if k != key
                  and any(m.group_key == k for m in self.plan.moves)}
        if not others:
            return
        choice, ok = QInputDialog.getItem(self, "Merge", "Merge this group into:", sorted(others), 0, False)
        if ok:
            merge_groups(self.ctx.conn, self.plan, others[choice], key)
            self.fill()

    def split(self) -> None:
        if not self._need_plan():
            return
        key, _ = self._current()
        if key is None:
            return
        ids = [m.file_id for m in self.plan.moves if m.group_key == key and m.selected]
        if not ids or len(ids) == len([m for m in self.plan.moves if m.group_key == key]):
            QMessageBox.information(self, "Split", "Check some (not all) files of this group to split them off.")
            return
        name, ok = QInputDialog.getText(self, "Split group", "Name for the new group:")
        if ok and name:
            split_group(self.ctx.conn, self.plan, key, ids, name)
            self.fill()

    def exclude(self) -> None:
        if not self._need_plan():
            return
        _, m = self._current()
        if m is None:
            return
        library.set_pref(self.ctx.conn, m.src, never_suggest=1)
        library.record_feedback(self.ctx.conn, "excluded", m.src, "organize")
        self.plan.moves.remove(m)
        refresh_destinations(self.ctx.conn, self.plan)
        self.fill()

    def apply(self) -> None:
        if self.ctx.fileop_refused(self) or not self._need_plan():
            return
        sel = self.plan.selected()
        if not sel:
            QMessageBox.information(self, "Nothing checked", "Check the files you want to move.")
            return
        if not confirm(self, "Apply moves", f"Move {len(sel)} file(s) to the destinations shown?\n\nEach file is "
                       "verified first, nothing is overwritten, and you can undo the whole batch.",
                       "\n".join(f"{m.src} -> {m.dst}" for m in sel[:300])):
            return
        moves = [(m.file_id, m.dst) for m in sel]

        def done(res):
            self.last_batch = res.batch_id
            msg = f"Moved {len(res.ok)} file(s)."
            if res.failed:
                msg += f"\n\n{len(res.failed)} left in place:\n" + "\n".join(
                    f"- {os.path.basename(r.src)}: {r.message}" for r in res.failed[:15])
            QMessageBox.information(self, "Organize", msg)
            self.ctx.data_changed.emit("files")
            self.plan = None
            self.tree.clear()
            self.preview.setRowCount(0)
            self.status.setText(msg.split("\n")[0] + " Use 'Undo last applied batch' to reverse it.")

        if self.ctx.run_fileop(self, "Organize", "Moving files...",
                               lambda fo: fo.move_files(moves, f"organize: {len(moves)} file(s)"), done):
            self.status.setText(f"Moving {len(moves)} file(s)...")

    def undo(self) -> None:
        if self.last_batch is None or self.ctx.fileop_refused(self):
            return
        if self._last_status() == "undone":
            QMessageBox.information(self, "Undo", "The last batch has already been undone.")
            self._sync()
            return
        if not confirm(self, "Undo", "Move the files of the last batch back to where they were?"):
            return
        b = self.last_batch

        def done(res):
            QMessageBox.information(self, "Undo", f"Moved back {len(res.ok)} file(s); {len(res.failed)} could not "
                                                  "be moved back (see Activity).")
            self.ctx.data_changed.emit("files")

        if self.ctx.run_fileop(self, "Undo", "Undoing...", lambda fo: fo.undo_batch(b), done):
            self.status.setText("Moving files back...")
