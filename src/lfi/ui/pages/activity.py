"""Activity history: every batch and operation, undo, recovery report, audit log."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget

from ... import config
from ..core import AppContext, confirm, fmt_time, open_file
from ..widgets import SortItem, button_row, end_fill, heading, make_table, note

STATE_HELP = {"done": "completed", "failed": "not performed (file untouched)", "restored": "restored from quarantine",
              "undone": "reversed", "trashed": "sent to Recycle Bin", "purged": "permanently deleted",
              "conflict": "needs review", "lost": "needs review", "pending": "in progress / interrupted"}


class ActivityPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.batches = []
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Activity", 18))
        lay.addWidget(note("Recommendations and previews never appear here - only operations you approved. Each "
                           "operation is journaled before it runs, so interrupted work is reconciled at startup."))
        self.recovery = QLabel("")
        self.recovery.setWordWrap(True)
        lay.addWidget(self.recovery)
        split = QSplitter(Qt.Orientation.Vertical)
        self.bt = make_table(["Batch", "When", "Kind", "Status", "Done / total", "Description"], stretch=5)
        self.bt.itemSelectionChanged.connect(self.show_ops)
        self.bt.itemSelectionChanged.connect(self._sync)
        split.addWidget(self.bt)
        self.ot = make_table(["Op", "Kind", "State", "From", "To", "Detail"], stretch=5)
        split.addWidget(self.ot)
        lay.addWidget(split, 1)
        bu, bl, brf = QPushButton("Undo selected organize batch"), QPushButton("Open audit log"), QPushButton("Refresh")
        bu.clicked.connect(self.undo)
        bl.clicked.connect(lambda: open_file(str(config.logs_dir() / "operations.jsonl")))
        brf.clicked.connect(self.refresh)
        lay.addLayout(button_row(bu, bl, brf))
        self.undo_btn = bu
        ctx.data_changed.connect(lambda _: self.isVisible() and self.refresh())
        ctx.busy_changed.connect(lambda _: self._sync())
        self._sync()

    def _batch(self):
        bid = self._batch_id()
        return next((x for x in self.batches if x["id"] == bid), None)

    def _sync(self) -> None:
        b = self._batch()
        self.undo_btn.setEnabled(b is not None and b["kind"] == "organize" and b["status"] != "undone"
                                 and not self.ctx.fileop_running)

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)

    def refresh(self) -> None:
        if self.ctx.recovery_report:
            self.recovery.setText("<b>Recovered at startup:</b><br>" + "<br>".join(self.ctx.recovery_report[:20]))
        self.batches = self.ctx.fileops(self.ctx.conn).history(500)
        t = self.bt
        t.setSortingEnabled(False)
        t.setRowCount(len(self.batches))
        for i, b in enumerate(self.batches):
            t.setItem(i, 0, SortItem(str(b["id"]), b["id"], data=b["id"]))
            t.setItem(i, 1, SortItem(fmt_time(b["created_at"]), b["created_at"]))
            t.setItem(i, 2, SortItem(b["kind"]))
            t.setItem(i, 3, SortItem(b["status"]))
            t.setItem(i, 4, SortItem(f"{b['n_done']} / {b['n_ops']}", b["n_done"]))
            t.setItem(i, 5, SortItem(b["description"] or ""))
        end_fill(t)  # newest first
        self._sync()

    def _batch_id(self):
        r = self.bt.currentRow()
        return self.bt.item(r, 0).data(Qt.ItemDataRole.UserRole) if r >= 0 else None

    def show_ops(self) -> None:
        bid = self._batch_id()
        if bid is None:
            return
        ops = self.ctx.conn.execute("SELECT * FROM operations WHERE batch_id=? ORDER BY id", (bid,)).fetchall()
        t = self.ot
        t.setSortingEnabled(False)
        t.setRowCount(len(ops))
        for i, o in enumerate(ops):
            t.setItem(i, 0, SortItem(str(o["id"]), o["id"]))
            t.setItem(i, 1, SortItem(o["kind"]))
            t.setItem(i, 2, SortItem(STATE_HELP.get(o["state"], o["state"])))
            t.setItem(i, 3, SortItem(o["src"]))
            t.setItem(i, 4, SortItem(o["dst"]))
            t.setItem(i, 5, SortItem(o["error"] or ""))
        end_fill(t)

    def undo(self) -> None:
        b = self._batch()
        if b is None or self.ctx.fileop_refused(self):
            return
        bid = b["id"]
        if b["kind"] != "organize":
            QMessageBox.information(self, "Undo", "Only organize batches are undone here. Restore quarantined "
                                                  "files from the Quarantine page.")
            return
        if b["status"] == "undone":
            QMessageBox.information(self, "Undo", f"Batch {bid} has already been undone.")
            return
        if not confirm(self, "Undo batch", f"Move the files of batch {bid} back to where they were?"):
            return

        def done(res):
            QMessageBox.information(self, "Undo", f"Moved back {len(res.ok)}; {len(res.failed)} could not be moved "
                                                  "back (details in the operations list).")
            self.ctx.data_changed.emit("files")
            self.refresh()

        self.ctx.run_fileop(self, "Undo", f"Undoing batch {bid}...", lambda fo: fo.undo_batch(bid), done,
                            on_finished=self.refresh)
