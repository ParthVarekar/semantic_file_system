"""Clean Up: one number ("you can free up X"), a few categories, a checklist per category, Undo.

Safe categories (exact duplicates, temporary and empty files) are pre-selected; the others are for
review only. Removing moves files to Recently Removed, never deletes them."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QProgressBar, QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from ... import db
from ..core import AppContext
from . import theme
from .search import thumbnail
from .widgets import (EmptyState, FileList, Row, SortButton, button, card, fmt_size, friendly_date, icon_label, label,
                      seconds, short_folder)

# (basket, title, icon, one-line explanation, pre-selected)
CATEGORIES = [
    ("exact_duplicates", "Duplicate files", "copy", "Exact copies of the same file. The best copy is always kept.", True),
    ("temporary", "Temporary files", "clock", "Leftovers such as unfinished downloads and lock files.", True),
    ("empty", "Empty files", "empty", "Files with nothing in them.", True),
    ("similar_images", "Similar photos", "photo", "Resized or re-saved versions of the same picture.", False),
    ("old_installers", "Old installers", "package", "Setup files and archives you have probably already used.", False),
    ("large_files", "Large files", "large", "The biggest files. Only you can tell if they're still needed.", False),
]
SAFE = {k for k, *_ in CATEGORIES if _[-1]}


class CategoryCard(QPushButton):
    def __init__(self, key: str, title: str, icon: str, text: str):
        super().__init__()
        self.key = key
        self.setObjectName("cardButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(118)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(icon_label(icon, 22, theme.current().accent))
        top.addStretch(1)
        self.size = label("", "title")
        top.addWidget(self.size)
        lay.addLayout(top)
        lay.addWidget(label(title, "headline"))
        self.detail = label(text, "caption", wrap=True)
        lay.addWidget(self.detail)
        for w in self.findChildren(QWidget):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_counts(self, n: int, size: int) -> None:
        self.size.setText((fmt_size(size) if size else f"{n} file{'s' * (n != 1)}") if n else "")
        self.setEnabled(n > 0)
        self.setToolTip("" if n else "Nothing to clean up here.")


class CleanUpPage(QWidget):
    def __init__(self, ctx: AppContext, toast):
        super().__init__()
        self.ctx, self.toast = ctx, toast
        self.report = None
        self.stale = True
        self.current_key = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        # overview
        ov = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(ov)
        lay = QVBoxLayout(ov)
        lay.setContentsMargins(32, 26, 32, 24)
        lay.setSpacing(14)
        lay.addWidget(label("Clean Up", "largeTitle"))
        hero, hl = card(margins=(22, 20, 22, 20), spacing=4)
        self.hero_num = label("", "hero")
        self.hero_text = label("", "secondary", wrap=True)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setTextVisible(False)
        self.busy.setFixedHeight(4)
        hl.addWidget(self.hero_num)
        hl.addWidget(self.hero_text)
        hl.addWidget(self.busy)
        hl.addWidget(label("Removed files go to Recently Removed first, so you can always put them back.", "caption"))
        lay.addWidget(hero)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.grid = grid
        self.cards: dict[str, CategoryCard] = {}
        for i, (key, title, icon, text, _safe) in enumerate(CATEGORIES):
            c = CategoryCard(key, title, icon, text)
            c.clicked.connect(lambda _=False, k=key: self.open_category(k))
            self.cards[key] = c
            grid.addWidget(c, i // 2, i % 2)
        lay.addLayout(grid)
        lay.addStretch(1)
        self.stack.addWidget(scroll)

        # category detail
        det = QWidget()
        dl = QVBoxLayout(det)
        dl.setContentsMargins(32, 18, 32, 18)
        dl.setSpacing(8)
        dl.addWidget(button("‹ Clean Up", "plain", slot=lambda: self.stack.setCurrentIndex(0)), 0,
                     Qt.AlignmentFlag.AlignLeft)
        self.d_title = label("", "largeTitle")
        self.d_text = label("", "secondary", wrap=True)
        dl.addWidget(self.d_title)
        dl.addWidget(self.d_text)
        srow = QHBoxLayout()
        self.sort = SortButton([("largest", "Largest first"), ("smallest", "Smallest first"), ("newest", "Newest"),
                                ("name", "Name"), ("best", "Suggested order")], "largest")
        self.sort.changed.connect(lambda k: self.list.model_.sort(k))
        srow.addStretch(1)
        srow.addWidget(self.sort)
        dl.addLayout(srow)
        self.list = FileList(checkable=True)
        self.list.model_.order = "largest"  # the point is to see what takes the room
        self.list.toggled.connect(self._update_bar)
        dl.addWidget(self.list, 1)
        self.d_empty = EmptyState("done", "All clear", "Nothing left in this category.")
        self.d_empty.hide()
        dl.addWidget(self.d_empty, 1)
        bar = QHBoxLayout()
        self.all_btn = button("Select All", "plain", slot=self._toggle_all)
        self.sel_label = label("", "secondary")
        self.remove_btn = button("Remove", "primary", "trash", self.remove)
        bar.addWidget(self.all_btn)
        bar.addWidget(self.sel_label)
        bar.addStretch(1)
        bar.addWidget(self.remove_btn)
        dl.addLayout(bar)
        self.stack.addWidget(det)

        ctx.data_changed.connect(self._changed)

    # ---- analysis --------------------------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        if self.stale:
            self.analyze()

    def _changed(self, what: str) -> None:
        if what in ("index", "files", "quarantine"):
            self.stale = True
            if self.isVisible():
                self.analyze()  # also refreshes an open category

    def analyze(self) -> None:
        if self.ctx.conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0:
            self.hero_num.setText("Nothing yet")
            self.hero_text.setText("Once your folders are indexed, this shows what can be cleaned up.")
            self.busy.hide()
            for c in self.cards.values():
                c.set_counts(0, 0)
            return
        self.stale = False
        self.busy.show()
        self.hero_text.setText("Looking for things you can clean up...")
        settings = self.ctx.settings

        def work(job):
            from ...cleanup import analyze

            conn = db.connect()
            try:
                rep = analyze(conn, settings, include_similar_documents=False)
                for c in rep.baskets.get("similar_images", [])[:300]:
                    c.thumb = thumbnail(c.path, 72)
                return rep
            finally:
                conn.close()

        self.ctx.run(work, self._loaded, lambda msg: self._failed(msg))

    def _failed(self, msg: str) -> None:
        self.busy.hide()
        self.hero_text.setText("Couldn't check right now: " + msg.split("\n\n")[0][:140])

    def _loaded(self, rep) -> None:
        self.report = rep
        self.busy.hide()
        safe_ids = {c.file_id for k in SAFE for c in rep.baskets.get(k, [])}
        review_ids = {c.file_id for k, *_ in CATEGORIES if k not in SAFE for c in rep.baskets.get(k, [])} - safe_ids
        safe = rep.reclaimable(selected=safe_ids) if safe_ids else 0
        review = rep.reclaimable(selected=review_ids) if review_ids else 0
        if safe:
            self.hero_num.setText(fmt_size(safe))
            self.hero_text.setText("can be freed right away" + (f", and {fmt_size(review)} more is worth a look."
                                                                 if review else "."))
        elif review:
            self.hero_num.setText(fmt_size(review))
            self.hero_text.setText("worth a look - nothing is clearly unnecessary.")
        else:
            self.hero_num.setText("All clear")
            self.hero_text.setText("No duplicates or leftovers found.")
        sizes = {}
        for key, c in self.cards.items():
            items = rep.baskets.get(key, [])
            sizes[key] = rep.reclaimable(key) if items else 0
            c.set_counts(len(items), sizes[key])
        self._rank_cards(sizes)
        if self.current_key and self.stack.currentIndex() == 1:
            self.open_category(self.current_key)

    # ---- one category ----------------------------------------------------------------------
    def open_category(self, key: str) -> None:
        if self.report is None:
            return
        self.current_key = key
        _, title, _icon, text, safe = next(c for c in CATEGORIES if c[0] == key)
        self.d_title.setText(title)
        self.d_text.setText(text + ("" if safe else " Nothing is selected for you - tick what you don't need."))
        rows = []
        for c in self.report.baskets.get(key, []):
            sub = short_folder(c.path)
            if c.keeper_path:
                import os

                kept = os.path.basename(c.keeper_path)
                sub = (f"Copy of {kept} - that one is kept" if key == "exact_duplicates"
                       else f"Similar to {kept} (kept)") + f"  ·  {sub}"
            rows.append(Row(c.file_id, c.name, sub, fmt_size(c.size), friendly_date(c.mtime_ns), c.path, safe,
                            getattr(c, "thumb", None), data=c, size=c.size or 0, when=seconds(c.mtime_ns)))
        self.list.set_rows(rows)
        self.list.setVisible(bool(rows))
        self.d_empty.setVisible(not rows)
        self._update_bar()
        self.stack.setCurrentIndex(1)

    def _update_bar(self) -> None:
        sel = self.list.model_.checked()
        if self.report is not None and sel:
            freed = self.report.reclaimable(selected={r.key for r in sel})
            self.sel_label.setText(f"{len(sel)} selected · {fmt_size(freed)}")
        else:
            self.sel_label.setText("")
        self.remove_btn.setEnabled(bool(sel))
        self.remove_btn.setText(f"Remove {len(sel)} Item{'s' * (len(sel) != 1)}" if sel else "Remove")
        rows = self.list.model_.rows
        self.all_btn.setText("Deselect All" if rows and len(sel) == len(rows) else "Select All")
        self.all_btn.setVisible(bool(rows))

    def _rank_cards(self, sizes: dict[str, int]) -> None:
        """The category that would free the most space comes first."""
        order = [k for k, *_ in CATEGORIES]
        ranked = sorted(self.cards, key=lambda k: (-sizes.get(k, 0), order.index(k)))
        for c in self.cards.values():
            self.grid.removeWidget(c)
        for i, k in enumerate(ranked):
            self.grid.addWidget(self.cards[k], i // 2, i % 2)

    def _toggle_all(self) -> None:
        rows = self.list.model_.rows
        self.list.model_.set_all(len(self.list.model_.checked()) != len(rows))
        self._update_bar()

    def remove(self) -> None:
        sel = self.list.model_.checked()
        if not sel:
            return
        key = self.current_key
        ids = [r.key for r in sel]
        keepers = {r.key: r.data.keeper_id for r in sel if key == "exact_duplicates" and r.data.keeper_id}

        def done(res) -> None:
            ok = [r.op_id for r in res.ok]
            gone = {r.key for r in sel}  # take them off the list now; the re-check refreshes the rest
            self.list.set_rows([r for r in self.list.model_.rows if r.key not in gone])
            self._update_bar()
            msg = f"Moved {len(ok)} item{'s' * (len(ok) != 1)} to Recently Removed"
            if res.failed:
                msg += f" · {len(res.failed)} skipped (changed since they were checked)"
            self.toast.show_message(msg, "Undo" if ok else "", lambda: self._undo(ok))
            self.ctx.data_changed.emit("quarantine")

        self.ctx.run_fileop(self, "Clean Up", "Removing...",
                            lambda fo: fo.quarantine(ids, f"clean up: {key}", key, keepers or None), done)

    def _undo(self, op_ids: list[int]) -> None:
        def done(res) -> None:
            self.toast.show_message(f"Put back {len(res.ok)} item{'s' * (len(res.ok) != 1)}")
            self.ctx.data_changed.emit("quarantine")

        self.ctx.run_fileop(self, "Undo", "Putting back...", lambda fo: fo.restore(op_ids), done)

    def sizeHint(self) -> QSize:
        return QSize(900, 640)
