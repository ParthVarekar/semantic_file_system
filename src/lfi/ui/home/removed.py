"""Recently Removed: everything Clean Up took away, ready to restore or send to the Recycle Bin.
Permanent deletion is deliberately not offered here; it lives in the Advanced tools."""

from __future__ import annotations

import datetime as dt
import os

from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from ..core import AppContext
from .widgets import EmptyState, FileList, Row, SortButton, button, fmt_size, label, short_folder


class RemovedPage(QWidget):
    def __init__(self, ctx: AppContext, toast):
        super().__init__()
        self.ctx, self.toast = ctx, toast
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 26, 32, 18)
        lay.setSpacing(10)
        lay.addWidget(label("Recently Removed", "largeTitle"))
        head = QHBoxLayout()
        head.addWidget(label("Files you remove stay here until you restore them or move them to the Recycle Bin.",
                             "secondary", wrap=True), 1)
        self.sort = SortButton([("best", "Recently removed"), ("largest", "Largest first"),
                                ("smallest", "Smallest first"), ("name", "Name")], "best")
        self.sort.changed.connect(lambda k: self.list.model_.sort(k))
        head.addWidget(self.sort)
        lay.addLayout(head)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.list = FileList(checkable=True)
        self.list.toggled.connect(self._update)
        self.stack.addWidget(self.list)
        self.empty = EmptyState("trash", "Nothing here", "Files you remove in Clean Up will appear here.")
        self.stack.addWidget(self.empty)
        bar = QHBoxLayout()
        self.all_btn = button("Select All", "plain", slot=self._toggle_all)
        self.sel = label("", "secondary")
        self.bin_btn = button("Move to Recycle Bin...", "danger", slot=self.to_bin)
        self.restore_btn = button("Restore", "primary", "restore", self.restore)
        for w in (self.all_btn, self.sel):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.bin_btn)
        bar.addWidget(self.restore_btn)
        lay.addLayout(bar)
        ctx.data_changed.connect(lambda what: what in ("quarantine", "files") and self.refresh())

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()

    def refresh(self) -> None:
        rows = []
        for q in self.ctx.fileops(self.ctx.conn).quarantined():
            when = dt.datetime.fromtimestamp(q["created_at"]).strftime("%d %b %Y").lstrip("0")
            rows.append(Row(q["id"], os.path.basename(q["src"]), "From " + (short_folder(q["src"]) or q["src"]),
                            fmt_size(q["size"] or 0), when, q["src"], False, size=q["size"] or 0,
                            when=float(q["created_at"] or 0)))
        self.list.set_rows(rows)
        self.stack.setCurrentIndex(0 if rows else 1)
        self._update()

    def _update(self) -> None:
        sel = self.list.model_.checked()
        n = len(sel)
        self.sel.setText(f"{n} selected" if n else "")
        self.restore_btn.setEnabled(bool(n))
        self.bin_btn.setEnabled(bool(n))
        rows = self.list.model_.rows
        self.all_btn.setVisible(bool(rows))
        self.all_btn.setText("Deselect All" if rows and n == len(rows) else "Select All")

    def _toggle_all(self) -> None:
        self.list.model_.set_all(len(self.list.model_.checked()) != len(self.list.model_.rows))
        self._update()

    def restore(self) -> None:
        ids = [r.key for r in self.list.model_.checked()]

        def done(res) -> None:
            n = len(res.ok)
            renamed = sum(1 for r in res.ok if "restored as" in r.message)
            msg = f"Restored {n} item{'s' * (n != 1)}"
            if renamed:
                msg += f" · {renamed} renamed because a file with that name already exists"
            if res.failed:
                msg += f" · {len(res.failed)} couldn't be restored"
            self.toast.show_message(msg)
            self.ctx.data_changed.emit("quarantine")

        self.ctx.run_fileop(self, "Restore", "Restoring...", lambda fo: fo.restore(ids, on_conflict="rename"), done)

    def to_bin(self) -> None:
        ids = [r.key for r in self.list.model_.checked()]
        box = QMessageBox(QMessageBox.Icon.Question, "Move to Recycle Bin",
                          f"Move {len(ids)} item{'s' * (len(ids) != 1)} to the Recycle Bin?",
                          QMessageBox.StandardButton.Cancel, self)
        box.setInformativeText("You can still get them back from the Recycle Bin until it is emptied.")
        go = box.addButton("Move to Recycle Bin", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not go:
            return

        def done(res) -> None:
            n = len(res.ok)
            self.toast.show_message(f"Moved {n} item{'s' * (n != 1)} to the Recycle Bin"
                                    + (f" · {len(res.failed)} couldn't be moved" if res.failed else ""))
            self.ctx.data_changed.emit("quarantine")

        self.ctx.run_fileop(self, "Recycle Bin", "Moving to the Recycle Bin...", lambda fo: fo.trash_quarantined(ids),
                            done)
