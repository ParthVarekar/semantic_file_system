"""Quarantine: restore, send to Recycle Bin, or (separately confirmed) delete permanently."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ... import config
from ...fileops import CONFIRM_PURGE
from ..core import AppContext, confirm, fmt_size, fmt_time, open_file
from ..widgets import SortItem, TypedConfirmDialog, button_row, check_item, checked_rows, end_fill, heading, \
    make_table, note, size_item


class QuarantinePage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.rows = []
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Quarantine", 18))
        lay.addWidget(note("Quarantined files were moved (not deleted) to a quarantine folder on the same drive. "
                           "They keep their content and dates and can be restored to their original location. "
                           "Space is only freed after you send them to the Recycle Bin or delete them."))
        self.summary = QLabel("")
        lay.addWidget(self.summary)
        self.table = make_table(["", "Original location", "Size", "Quarantined", "Reason"], stretch=1)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("If the original location is occupied:"))
        self.conflict = QComboBox()
        self.conflict.addItem("restore next to it as 'name (restored)'", "rename")
        self.conflict.addItem("leave it in quarantine", "skip")
        row.addWidget(self.conflict)
        row.addStretch(1)
        lay.addLayout(row)
        br, bt, bp, bo, bf = (QPushButton("Restore checked"), QPushButton("Move checked to Recycle Bin..."),
                              QPushButton("Delete checked permanently..."), QPushButton("Open quarantined copy"),
                              QPushButton("Open quarantine folder"))
        br.setObjectName("primary")
        bp.setObjectName("danger")
        br.clicked.connect(self.restore)
        bt.clicked.connect(self.trash)
        bp.clicked.connect(self.purge)
        bo.clicked.connect(self.open_copy)
        bf.clicked.connect(lambda: open_file(str(config.default_quarantine_dir())))
        ball = QPushButton("Check all")
        ball.clicked.connect(self.check_all)
        lay.addLayout(button_row(br, bt, bp))
        lay.addLayout(button_row(ball, bo, bf))
        self.op_buttons = [br, bt, bp]
        ctx.data_changed.connect(lambda what: what in ("quarantine", "files") and self.refresh())
        ctx.busy_changed.connect(lambda _: self._sync())

    def _sync(self) -> None:
        for b in self.op_buttons:
            b.setEnabled(not self.ctx.fileop_running)

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)

    def refresh(self) -> None:
        self.rows = self.ctx.fileops(self.ctx.conn).quarantined()
        t = self.table
        t.setSortingEnabled(False)
        t.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            t.setItem(i, 0, check_item("", False, data=i, key=i))
            t.setItem(i, 1, SortItem(r["src"]))
            t.setItem(i, 2, size_item(r["size"] or 0))
            t.setItem(i, 3, SortItem(fmt_time(r["created_at"]), r["created_at"]))
            t.setItem(i, 4, SortItem(r["reason"] or r["description"] or ""))
        end_fill(t)  # newest first, as listed
        total = sum(r["size"] or 0 for r in self.rows)
        self.summary.setText(f"{len(self.rows)} file(s) in quarantine, {fmt_size(total)}.")

    def check_all(self) -> None:
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.CheckState.Checked)

    def _checked_ops(self) -> list[int]:
        return [self.rows[self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)]["id"] for r in checked_rows(self.table)]

    def _run(self, fn, title: str, label: str) -> None:
        def done(res):
            msg = f"{title}: {len(res.ok)} done."
            if res.failed:
                msg += f"\n\n{len(res.failed)} not done:\n" + "\n".join(
                    f"- {os.path.basename(r.src or r.dst)}: {r.message}" for r in res.failed[:15])
            QMessageBox.information(self, title, msg)
            self.ctx.data_changed.emit("quarantine")
            self.refresh()

        if self.ctx.run_fileop(self, title, label, fn, done):
            self.summary.setText(label)

    def restore(self) -> None:
        ops = self._checked_ops()
        if not ops or self.ctx.fileop_refused(self):
            return
        mode = self.conflict.currentData()
        self._run(lambda fo: fo.restore(ops, on_conflict=mode), "Restore", f"Restoring {len(ops)} file(s)...")

    def trash(self) -> None:
        ops = self._checked_ops()
        if not ops or self.ctx.fileop_refused(self):
            return
        if confirm(self, "Recycle Bin", f"Send {len(ops)} quarantined file(s) to the Windows Recycle Bin?\n\n"
                   "You can still recover them from the Recycle Bin until it is emptied."):
            self._run(lambda fo: fo.trash_quarantined(ops), "Recycle Bin",
                      f"Sending {len(ops)} file(s) to the Recycle Bin...")

    def purge(self) -> None:
        ops = self._checked_ops()
        if not ops or self.ctx.fileop_refused(self):
            return
        dlg = TypedConfirmDialog(self, "Delete permanently",
                                 f"Permanently delete {len(ops)} quarantined file(s)? This cannot be undone - they "
                                 "will not go to the Recycle Bin.", CONFIRM_PURGE)
        if dlg.exec():
            phrase = dlg.typed()
            self._run(lambda fo: fo.purge_quarantined(ops, phrase), "Permanent deletion",
                      f"Deleting {len(ops)} file(s)...")

    def open_copy(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            op = self.rows[self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)]
            if os.path.exists(op["dst"]):
                open_file(op["dst"])
