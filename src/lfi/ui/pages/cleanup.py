"""Cleanup review baskets with explainable review-priority scores and safe quarantine."""

from __future__ import annotations

import html
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from ... import db, library
from ...cleanup import BASKETS, analyze, mark_kept
from ..core import AppContext, confirm, fmt_size, fmt_time, open_file, reveal
from ..widgets import SortItem, button_row, check_item, checked_rows, end_fill, heading, make_table, note, \
    size_item


class CleanupPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.report = None
        self.cands = []
        self._basket = 0
        self._pending_note = ""  # shown above the next analysis result ("Noted: ...")
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Cleanup review", 18))
        lay.addWidget(note("Each file gets a review-priority score (0-100) built from the signals shown. It ranks "
                           "files for your review; it is not a probability that removal is safe. Nothing is "
                           "selected for you except verified exact duplicates and lock/partial files. Quarantined "
                           "files can be restored at any time."))
        top = QHBoxLayout()
        self.analyze_btn = QPushButton("Analyze")
        self.analyze_btn.setObjectName("primary")
        self.analyze_btn.clicked.connect(self.run)
        self.summary = QLabel("Run an analysis to see candidates.")
        top.addWidget(self.analyze_btn)
        top.addWidget(self.summary, 1)
        lay.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.baskets = QListWidget()
        self.baskets.setMaximumWidth(280)
        self.baskets.currentRowChanged.connect(self.show_basket)
        split.addWidget(self.baskets)
        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(0, 0, 0, 0)
        self.desc = note("")
        ml.addWidget(self.desc)
        self.table = make_table(["", "Score", "Name", "Size", "Modified", "Confidence", "Main reason"], stretch=6)
        self.table.itemSelectionChanged.connect(self.show_detail)
        self.table.itemChanged.connect(lambda _: self._update_selected())
        ml.addWidget(self.table, 1)
        sel = QHBoxLayout()
        self.sel_label = QLabel("")
        self.sel_label.setWordWrap(True)
        sel.addWidget(self.sel_label, 1)
        bq, bk, bn, bp = (QPushButton("Quarantine checked..."), QPushButton("Not junk"),
                          QPushButton("Never suggest again"), QPushButton("Pin as important"))
        bq.setObjectName("primary")
        bq.clicked.connect(self.quarantine)
        bk.clicked.connect(lambda: self.feedback(False))
        bn.clicked.connect(lambda: self.feedback(True))
        bp.clicked.connect(self.pin)
        for b in (bk, bn, bp):
            b.setToolTip("Applies to the selected row(s), not the checked ones")
        ball, bnone = QPushButton("Check all"), QPushButton("Uncheck all")
        ball.clicked.connect(lambda: self._check_all(True))
        bnone.clicked.connect(lambda: self._check_all(False))
        sel.addWidget(ball)
        sel.addWidget(bnone)
        ml.addLayout(sel)
        ml.addLayout(button_row(bq, bk, bn, bp))
        self.quarantine_btn = bq
        self.actions = [bq, bk, bn, bp, ball, bnone]
        split.addWidget(mid)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.detail = QTextBrowser()
        rl.addWidget(self.detail, 1)
        bo, br = QPushButton("Open"), QPushButton("Show in Explorer")
        bo.clicked.connect(lambda: self._cur() and open_file(self._cur().path))
        br.clicked.connect(lambda: self._cur() and reveal(self._cur().path))
        rl.addLayout(button_row(bo, br))
        split.addWidget(right)
        split.setSizes([240, 640, 360])
        lay.addWidget(split, 1)
        ctx.data_changed.connect(self._data_changed)
        ctx.busy_changed.connect(lambda _: self._sync())
        self._sync()

    def _sync(self) -> None:
        has = self.report is not None
        for b in self.actions:
            b.setEnabled(has)
        self.quarantine_btn.setEnabled(has and not self.ctx.fileop_running)

    def _data_changed(self, what: str) -> None:
        if what in ("index", "files", "quarantine") and self.report is not None:
            self.mark_stale()

    def mark_stale(self) -> None:
        """Files were moved, restored or re-indexed elsewhere: these results no longer hold."""
        self.report, self.cands = None, []
        self.baskets.clear()
        self.table.setRowCount(0)
        self.desc.setText("")
        self.detail.clear()
        self.sel_label.setText("")
        self.summary.setText("Files changed - Analyze again to see up-to-date results.")
        self._sync()

    def run(self) -> None:
        self.analyze_btn.setEnabled(False)
        note_ = self._pending_note + "<br>" if self._pending_note else ""
        self.summary.setText(note_ + "Analyzing (exact duplicates, similar images, similar documents, baskets)...")
        settings = self.ctx.settings

        def work(job):
            conn = db.connect()
            try:
                return analyze(conn, settings)
            finally:
                conn.close()

        self.ctx.run(work, self.loaded, on_finished=lambda: self.analyze_btn.setEnabled(True))

    def loaded(self, rep) -> None:
        self.report = rep
        cur = self._basket
        self.baskets.blockSignals(True)
        self.baskets.clear()
        for key, label, _ in BASKETS:
            items = rep.baskets[key]
            it = QListWidgetItem(f"{label}\n{len(items)} files - {fmt_size(rep.reclaimable(key))}")
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.baskets.addItem(it)
        self.baskets.blockSignals(False)
        hidden = f"; {rep.hidden_by_preference} hidden by your pins/exclusions" if rep.hidden_by_preference else ""
        note_, self._pending_note = (self._pending_note + "<br>" if self._pending_note else ""), ""
        self.summary.setText(f"{note_}Potentially reclaimable: <b>{fmt_size(rep.reclaimable())}</b> (each physical "
                             f"file counted once; similar documents excluded){hidden}")
        self.baskets.setCurrentRow(cur)
        self.show_basket(cur)
        self._sync()

    def show_basket(self, row: int) -> None:
        if self.report is None or row < 0:
            return
        self._basket = row
        key, label, desc = BASKETS[row]
        self.desc.setText(f"<b>{label}</b> - {desc}")
        self.cands = self.report.baskets[key]
        t = self.table
        t.blockSignals(True)
        t.setSortingEnabled(False)
        t.setRowCount(len(self.cands))
        for i, c in enumerate(self.cands):
            preselect = c.confidence == "high" and key in ("exact_duplicates", "temporary") and c.score >= 50
            t.setItem(i, 0, check_item("", preselect, data=i, key=i))
            t.setItem(i, 1, SortItem(f"{c.score:.0f}", c.score))
            t.setItem(i, 2, SortItem(c.name))
            t.setItem(i, 3, size_item(c.size))
            t.setItem(i, 4, SortItem(fmt_time(c.mtime_ns), c.mtime_ns))
            t.setItem(i, 5, SortItem(c.confidence))
            main = max(c.signals, key=lambda s: s.points) if c.signals else None
            t.setItem(i, 6, SortItem(f"{main.name}: {main.evidence}" if main else ""))
        end_fill(t)  # review-priority order, highest first
        t.blockSignals(False)
        t.resizeColumnsToContents()
        self._update_selected()
        if self.cands:
            t.selectRow(0)
        else:
            self.detail.setPlainText("Nothing in this basket.")

    def _checked(self):
        return [self.cands[self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)] for r in checked_rows(self.table)]

    def _selected(self):
        rows = sorted({ix.row() for ix in self.table.selectionModel().selectedRows()})
        return [self.cands[self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)] for r in rows]

    def _check_all(self, on: bool) -> None:
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._update_selected()

    def _update_selected(self) -> None:
        if self.report is None:
            return
        ch = self._checked()
        sel = self.report.reclaimable(selected={c.file_id for c in ch})
        self.sel_label.setText(f"{len(ch)} checked - frees about {fmt_size(sel)} once removed from quarantine")

    def _cur(self):
        r = self.table.currentRow()
        if r < 0 or not self.cands:
            return None
        return self.cands[self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)]

    def show_detail(self) -> None:
        c = self._cur()
        if c is None:
            return
        rows = "".join(f"<tr><td align=right><b>{s.points:+.0f}</b></td><td>{html.escape(s.name)}</td>"
                       f"<td>{html.escape(s.evidence)}</td></tr>" for s in c.signals)
        keeper = (f"<p>Copy that would be kept: <code>{html.escape(c.keeper_path)}</code></p>" if c.keeper_path else "")
        self.detail.setHtml(
            f"<h3>{html.escape(c.name)}</h3><p><small>{html.escape(c.path)}</small></p>"
            f"<p>Review priority <b>{c.score:.0f}/100</b>, {c.confidence} confidence</p>"
            f"<table cellspacing=4>{rows}</table>{keeper}"
            "<p><small>Score = sum of signal points, clamped to 0-100. Weights are adjustable in Settings. "
            "It is a ranking for review, not a probability.</small></p>")

    def quarantine(self) -> None:
        if self.ctx.fileop_refused(self):
            return
        ch = self._checked()
        if not ch:
            QMessageBox.information(self, "Nothing checked", "Check the files you want to quarantine.")
            return
        total = sum(c.size for c in ch)
        if not confirm(self, "Quarantine files",
                       f"Move {len(ch)} file(s) ({fmt_size(total)}) into quarantine?\n\nThey are moved, not "
                       "deleted, and can be restored to their original location from the Quarantine page. "
                       "Each file is re-verified first; anything that changed is left untouched.",
                       "\n".join(c.path for c in ch[:200])):
            return
        keepers = {c.file_id: c.keeper_id for c in ch if c.basket == "exact_duplicates" and c.keeper_id}
        basket = ch[0].basket
        ids = [c.file_id for c in ch]

        def done(res):
            msg = f"Quarantined {len(res.ok)} file(s)."
            if res.failed:
                msg += f"\n\n{len(res.failed)} left untouched:\n" + "\n".join(
                    f"- {os.path.basename(r.src)}: {r.message}" for r in res.failed[:15])
            QMessageBox.information(self, "Quarantine", msg)
            self.ctx.data_changed.emit("quarantine")
            self.run()

        if self.ctx.run_fileop(self, "Quarantine", "Quarantining files...",
                               lambda fo: fo.quarantine(ids, f"cleanup: {basket}", basket, keepers), done):
            self.sel_label.setText(f"Quarantining {len(ids)} file(s)...")

    def feedback(self, never: bool) -> None:
        """'Not junk' / 'Never suggest again' for the selected row(s); check marks are only for quarantine."""
        ch = self._selected()
        if not ch:
            QMessageBox.information(self, "Nothing selected", "Select the file(s) you want to keep first.")
            return
        mark_kept(self.ctx.conn, ch, never_again=never)
        self._pending_note = (f"Noted: {len(ch)} file(s) kept. Similar suggestions will rank lower."
                              + (" They will not be suggested again." if never else ""))
        self.run()

    def pin(self) -> None:
        c = self._cur()
        if c:
            library.set_pref(self.ctx.conn, c.path, pinned=1)
            library.record_feedback(self.ctx.conn, "pinned", c.path, c.basket)
            self._pending_note = f"Pinned {c.name}: it will never be suggested for cleanup or moved."
            self.run()
