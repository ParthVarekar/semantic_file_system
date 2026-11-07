"""Organize: confident suggestions only, one card per destination folder, one button, Undo.
Editing groups and low-confidence moves live in the Advanced tools."""

from __future__ import annotations

import json
import os

from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from ... import db
from ..core import AppContext
from . import theme
from .widgets import EmptyState, SortButton, Switch, button, card, fmt_size, icon_label, label, short_folder


class GroupCard(QWidget):
    def __init__(self, title: str, moves: list, reason: str, size: int = 0):
        super().__init__()
        self.moves = moves
        self.title, self.size = title, size
        c, lay = card(spacing=4)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(c)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(icon_label("folder", 22, theme.current().accent))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(title, "headline"))
        sources = sorted({os.path.basename(os.path.dirname(m.src)) or m.src for m in moves})
        n = len(moves)
        col.addWidget(label(f"{n} file{'s' * (n != 1)}" + (f" · {fmt_size(size)}" if size else "")
                            + f" from {', '.join(sources[:3])}"
                            + (f" and {len(sources) - 3} more folders" if len(sources) > 3 else ""), "caption", wrap=True))
        top.addLayout(col, 1)
        self.switch = Switch(True)
        top.addWidget(self.switch)
        lay.addLayout(top)
        names = [os.path.basename(m.src) for m in moves[:4]]
        more = f"  and {n - 4} more" if n > 4 else ""
        lay.addWidget(label(",  ".join(names) + more, "secondary", wrap=True))
        dest = short_folder(os.path.join(os.path.dirname(moves[0].dst), "x")).split(" › ")
        lay.addWidget(label("Into " + " › ".join((["…"] if len(dest) > 3 else []) + dest[-3:]), "caption",
                            wrap=True))


class OrganizePage(QWidget):
    def __init__(self, ctx: AppContext, toast):
        super().__init__()
        self.ctx, self.toast = ctx, toast
        self.plan = None
        self.stale = True
        self.cards: list[GroupCard] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 26, 32, 18)
        lay.setSpacing(10)
        lay.addWidget(label("Organize", "largeTitle"))
        head = QHBoxLayout()
        head.addWidget(label("Suggestions for loose files, based on what's in them. Nothing moves until you click "
                             "Tidy Up, and you can undo it.", "secondary", wrap=True), 1)
        self.sort = SortButton([("files", "Most files"), ("largest", "Largest first"),
                                ("smallest", "Smallest first"), ("name", "Folder name")], "files")
        self.sort.changed.connect(lambda _: self._reorder())
        head.addWidget(self.sort)
        lay.addLayout(head)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setTextVisible(False)
        self.busy.setFixedHeight(4)
        self.busy.hide()
        lay.addWidget(self.busy)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        self.list_lay = QVBoxLayout(holder)
        self.list_lay.setContentsMargins(0, 4, 4, 4)
        self.list_lay.setSpacing(10)
        self.list_lay.addStretch(1)
        scroll.setWidget(holder)
        self.stack.addWidget(scroll)
        self.empty = EmptyState("done", "Everything looks tidy", "There's nothing to sort right now.")
        self.stack.addWidget(self.empty)
        bar = QHBoxLayout()
        self.summary = label("", "secondary")
        self.apply_btn = button("Tidy Up", "primary", "sparkle", self.apply)
        bar.addWidget(self.summary)
        bar.addStretch(1)
        bar.addWidget(self.apply_btn)
        lay.addLayout(bar)
        ctx.data_changed.connect(self._changed)

    def showEvent(self, e):
        super().showEvent(e)
        if self.stale:
            self.build()

    def _changed(self, what: str) -> None:
        if what in ("index", "files", "quarantine"):
            self.stale = True
            if self.isVisible():
                self.build()

    def build(self) -> None:
        if self.ctx.conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0:
            self._show([])
            return
        self.stale = False
        self.busy.show()
        self.apply_btn.setEnabled(False)
        self.summary.setText("Looking for files that could be sorted...")
        settings, backend = self.ctx.settings, self.ctx.backend

        def work(job):
            from ...organize import build_plan

            conn = db.connect()
            try:
                return build_plan(conn, settings, backend)
            finally:
                conn.close()

        self.ctx.run(work, self._loaded, lambda msg: self.summary.setText("Couldn't check right now."),
                     on_finished=self.busy.hide)

    def _loaded(self, plan) -> None:
        from ...organize import group_folder

        self.plan = plan
        groups = []
        for key, g in plan.groups.items():
            moves = [m for m in plan.selected() if m.group_key == key]
            if moves:
                groups.append((group_folder(g), moves, (g.reasons or [""])[0]))
        ids = [m.file_id for _, moves, _ in groups for m in moves]
        sizes = {r[0]: r[1] or 0 for r in self.ctx.conn.execute(
            "SELECT id, size FROM files WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(ids),))}
        self._show([(t, moves, why, sum(sizes.get(m.file_id, 0) for m in moves)) for t, moves, why in groups])

    def _show(self, groups: list) -> None:
        for c in self.cards:
            c.setParent(None)
        self.cards = []
        for title, moves, reason, size in groups:
            c = GroupCard(title, moves, "", size)
            c.switch.toggled.connect(lambda _: self._update())
            self.cards.append(c)
            self.list_lay.insertWidget(self.list_lay.count() - 1, c)
        self._reorder()
        self.stack.setCurrentIndex(0 if groups else 1)
        self._update()

    def _reorder(self) -> None:
        """Put the folder cards in the chosen order, keeping each card's on/off switch."""
        key = {"largest": lambda c: -c.size, "smallest": lambda c: c.size,
               "name": lambda c: c.title.lower()}.get(self.sort.value(), lambda c: -len(c.moves))
        self.cards.sort(key=key)
        for c in self.cards:
            self.list_lay.removeWidget(c)
        for i, c in enumerate(self.cards):
            self.list_lay.insertWidget(i, c)

    def _update(self) -> None:
        n = sum(len(c.moves) for c in self.cards if c.switch.isChecked())
        self.apply_btn.setEnabled(n > 0)
        self.apply_btn.setText(f"Tidy Up {n} File{'s' * (n != 1)}" if n else "Tidy Up")
        on = sum(c.switch.isChecked() for c in self.cards)
        self.summary.setText(f"{on} of {len(self.cards)} folders selected" if self.cards else "")

    def apply(self) -> None:
        moves = [(m.file_id, m.dst) for c in self.cards if c.switch.isChecked() for m in c.moves]
        if not moves:
            return

        def done(res) -> None:
            n = len(res.ok)
            msg = f"Moved {n} file{'s' * (n != 1)} into folders"
            if res.failed:
                msg += f" · {len(res.failed)} skipped"
            self.toast.show_message(msg, "Undo" if n else "", lambda: self._undo(res.batch_id))
            self.ctx.data_changed.emit("files")

        self.ctx.run_fileop(self, "Organize", "Moving files...",
                            lambda fo: fo.move_files(moves, f"tidy up: {len(moves)} file(s)"), done)

    def _undo(self, batch_id: int) -> None:
        def done(res) -> None:
            self.toast.show_message(f"Moved {len(res.ok)} file{'s' * (len(res.ok) != 1)} back")
            self.ctx.data_changed.emit("files")

        self.ctx.run_fileop(self, "Undo", "Moving files back...", lambda fo: fo.undo_batch(batch_id), done)
