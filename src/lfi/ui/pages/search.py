"""Natural-language + keyword search over documents and images, safe previews, related files.

Images are matched by what they *show* (vision-language embeddings), so a vague description such
as "the receipt from the grocery store" finds a photo named IMG_0042.jpg.
"""

from __future__ import annotations

import html
import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from ... import library
from ...search import SearchEngine
from ..core import AppContext, fmt_size, fmt_time, open_file, reveal
from ..widgets import PreviewPane, SortItem, button_row, end_fill, heading, make_table, note, size_item

HELP = ("Describe what you are looking for in plain words - <i>notes about eigenvalues</i>, "
        "<i>the receipt from the grocery store</i>, <i>screenshot of some code</i>, <i>sunset at the beach</i>. "
        "Filters: <code>ext:pdf</code> <code>kind:image</code> <code>after:2024-01</code> <code>before:2023</code> "
        "<code>larger:10MB</code> <code>in:Downloads</code>")
SCOPES = [("All files", "all"), ("Documents", "documents"), ("Images - describe what's in the picture", "images")]
THUMB = 150


class SearchPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.hits = []
        self.related_to = None  # name of the file whose related files are shown (not a query)
        self._thumb_gen = 0
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Search", 18))
        row = QHBoxLayout()
        self.q = QLineEdit()
        self.q.setPlaceholderText("Search your files by meaning, description or keyword...")
        self.q.returnPressed.connect(self.run)
        self.scope = QComboBox()
        for label, key in SCOPES:
            self.scope.addItem(label, key)
        self.scope.currentIndexChanged.connect(lambda _: self.q.text().strip() and self.run())
        go = QPushButton("Search")
        go.setObjectName("primary")
        go.clicked.connect(self.run)
        row.addWidget(self.q, 1)
        row.addWidget(self.scope)
        row.addWidget(go)
        lay.addLayout(row)
        h = QLabel(HELP)
        h.setObjectName("note")
        h.setWordWrap(True)
        lay.addWidget(h)
        self.mode = note("")
        lay.addWidget(self.mode)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.stack = QStackedWidget()
        self.table = make_table(["Name", "Folder", "Type", "Size", "Modified", "Why it matched"], stretch=5)
        self.table.itemSelectionChanged.connect(self.show_selected)
        self.table.doubleClicked.connect(lambda _: self._open())
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setIconSize(QSize(THUMB, THUMB))
        self.grid.setGridSize(QSize(THUMB + 24, THUMB + 44))
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setWordWrap(True)
        self.grid.setUniformItemSizes(True)
        self.grid.currentRowChanged.connect(lambda _: self.show_selected())
        self.grid.itemDoubleClicked.connect(lambda _: self._open())
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.grid)
        split.addWidget(self.stack)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        self.preview = PreviewPane()
        rl.addWidget(self.preview, 1)
        bo, br, brel, bpin = (QPushButton("Open"), QPushButton("Show in Explorer"), QPushButton("Related files"),
                              QPushButton("Pin as important"))
        bo.clicked.connect(self._open)
        br.clicked.connect(lambda: self._cur() and reveal(self._cur().path))
        brel.clicked.connect(self.related)
        bpin.clicked.connect(self.pin)
        rl.addLayout(button_row(bo, br, brel, bpin))
        split.addWidget(right)
        split.setSizes([720, 420])
        lay.addWidget(split, 1)
        ctx.backend_changed.connect(self._mode)
        self._mode()

    def _mode(self) -> None:
        self.mode.setText(self.ctx.backend_status() + "  |  " + self.ctx.vision_status())

    def _engine(self) -> SearchEngine:
        return SearchEngine(self.ctx.conn, self.ctx.backend, self.ctx.vision)

    def run(self) -> None:
        q = self.q.text().strip()
        if not q:
            return
        self.related_to = None
        scope = self.scope.currentData()
        engine = self._engine()
        self.fill(engine.search(q, 200, scope=scope), grid=scope == "images")
        notes = []  # rebuilt for every search, so an earlier search's note never lingers
        if engine.last_problems:
            notes.append("Ignored: " + "; ".join(engine.last_problems) + ".")
        if scope == "images" and self.ctx.vision is None:
            notes.append("Image content search needs the image model (Settings > Image search). "
                         "Showing matches by file name and folder only.")
        if notes:
            self.mode.setText("  ".join(notes))
        else:
            self._mode()

    def related(self) -> None:
        h = self._cur()
        if h is None:
            return
        if self.ctx.backend is None:
            self.mode.setText("Related files need the embedding model (Settings > Models).")
            return
        rel = self._engine().related(h.file_id, 30)
        self.related_to = h.name  # the query box keeps the user's own query
        self.fill(rel, grid=False)
        self.mode.setText(f"Showing files related to {h.name}. Search again (or change the scope) to go back.")

    def fill(self, hits, grid: bool) -> None:
        self.hits = hits
        self.stack.setCurrentIndex(1 if grid else 0)
        if grid:
            self._fill_grid(hits)
        else:
            self._fill_table(hits)
        if not hits:
            self.preview.clear()
            self.preview.text.setPlainText("No matches.")

    def _fill_table(self, hits) -> None:
        t = self.table
        t.setSortingEnabled(False)
        t.setRowCount(len(hits))
        for i, h in enumerate(hits):
            t.setItem(i, 0, SortItem(h.name, data=i))  # sorts by name; the rank stays as data
            t.setItem(i, 1, SortItem(os.path.dirname(h.path)))
            t.setItem(i, 2, SortItem(h.kind))
            t.setItem(i, 3, size_item(h.size))
            t.setItem(i, 4, SortItem(fmt_time(h.mtime_ns), h.mtime_ns))
            t.setItem(i, 5, SortItem("; ".join(h.reasons), i))
        end_fill(t)
        t.resizeColumnToContents(0)
        if hits:
            t.selectRow(0)

    def _fill_grid(self, hits) -> None:
        self.grid.clear()
        placeholder = QPixmap(THUMB, THUMB)
        placeholder.fill(Qt.GlobalColor.lightGray)
        for i, h in enumerate(hits):
            it = QListWidgetItem(QIcon(placeholder), h.name)
            it.setData(Qt.ItemDataRole.UserRole, i)
            it.setToolTip(f"{h.path}\n" + "; ".join(h.reasons))
            self.grid.addItem(it)
        if hits:
            self.grid.setCurrentRow(0)
        self._thumb_gen += 1
        gen = self._thumb_gen
        paths = [(i, h.path) for i, h in enumerate(hits[:240]) if h.size < 60 * 1024 * 1024]

        def work(job):  # QImage decoding is thread-safe; keeps the GUI responsive on big folders
            out = []
            for i, p in paths:
                if job.cancel.is_set():
                    break
                r = QImageReader(p)
                r.setAutoTransform(True)
                s = r.size()
                if s.isValid() and s.width() * s.height() < 80_000_000:
                    r.setScaledSize(s.scaled(THUMB, THUMB, Qt.AspectRatioMode.KeepAspectRatio))
                    img = r.read()
                    if not img.isNull():
                        out.append((i, img))
            return out

        def done(images):
            if gen != self._thumb_gen:
                return  # a newer search replaced this grid
            for i, img in images:
                item = self.grid.item(i)
                if item is not None:
                    item.setIcon(QIcon(QPixmap.fromImage(img)))

        self.ctx.run(work, done)

    def _cur(self):
        if not self.hits:
            return None
        if self.stack.currentIndex() == 1:
            it = self.grid.currentItem()
            return self.hits[it.data(Qt.ItemDataRole.UserRole)] if it else None
        r = self.table.currentRow()
        if r < 0:
            return None
        it = self.table.item(r, 0)
        return self.hits[it.data(Qt.ItemDataRole.UserRole)] if it else None

    def show_selected(self) -> None:
        h = self._cur()
        if h is None:
            return
        row = self.ctx.conn.execute("SELECT * FROM files WHERE id=?", (h.file_id,)).fetchone()
        extra, tags = "", ""
        if row is not None:
            bits = [row["kind"] or "", fmt_size(row["size"]), "modified " + fmt_time(row["mtime_ns"])]
            if row["width"]:
                bits.append(f"{row['width']}x{row['height']}")
            if row["status"] not in ("ok",):
                bits.append(f"status: {row['status']}")
            extra = " | ".join(b for b in bits if b)
            if row["img_tags"]:
                tags = f"<br><small>Auto-tags (may be wrong): {html.escape(row['img_tags'].replace(';', ','))}</small>"
        head = (f"<b>{html.escape(h.name)}</b><br><small>{html.escape(h.path)}</small><br><small>{extra}</small>"
                f"{tags}<br><small><i>{html.escape('; '.join(h.reasons))}</i></small>")
        body = self._engine().preview(h.file_id) or h.snippet or "(no text content - preview not available)"
        self.preview.show_file(h.path, head, body)

    def _open(self) -> None:
        h = self._cur()
        if h and os.path.exists(h.path):
            open_file(h.path)

    def pin(self) -> None:
        h = self._cur()
        if h:
            library.set_pref(self.ctx.conn, h.path, pinned=1)
            library.record_feedback(self.ctx.conn, "pinned", h.path)
            self.mode.setText(f"Pinned {h.name}: it will never be suggested for cleanup or moved.")
