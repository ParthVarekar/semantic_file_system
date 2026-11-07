"""Search: a greeting, one glowing field, a row of chips, results as cards with real previews (or a
compact list), and a preview panel with Summary / Related / Details.

The greeting folds away as soon as you type, so results get the room; clearing the field brings it
back with your most recent files. Previews load in the background and fade in."""

from __future__ import annotations

import datetime as dt
import os

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QKeySequence, QPainter, QPainterPath, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QGridLayout, QHBoxLayout, QLineEdit, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from ... import db
from ..core import AppContext, open_file, reveal
from ..quick import _short_reason
from . import theme
from .service import IndexService
from .thumbs import ThumbLoader, decode_image
from .widgets import (Chips, EmptyState, FileList, Glass, GlyphLabel, IconButton, Orb, Row, Segmented, SortButton,
                      button, chip, draw_preview, fmt_size, friendly_date, label, seconds, short_folder)

KIND_NAME = {"document": "Document", "image": "Photo", "video": "Video", "audio": "Audio", "archive": "Archive",
             "installer": "Installer", "code": "Code", "data": "Data", "temporary": "Temporary file", "other": "File"}

# chip -> (search scope, filter added to the words typed, the same filter for "recent files")
CHIPS = [("all", "All"), ("documents", "Documents"), ("pdf", "PDF"), ("images", "Images"), ("videos", "Videos"),
         ("code", "Code")]
CHIP_FILTER = {
    "all": ("all", "", "COALESCE(kind,'other') <> 'temporary'"),
    "documents": ("documents", "", "COALESCE(kind,'other') NOT IN ('image','temporary')"),
    "pdf": ("documents", "ext:pdf", "ext='.pdf'"),
    "images": ("images", "", "kind='image'"),
    "videos": ("all", "kind:video", "kind='video'"),
    "code": ("all", "kind:code", "kind='code'"),
}
SORTS = [("best", "Best match"), ("newest", "Newest"), ("largest", "Largest first"),
         ("smallest", "Smallest first"), ("name", "Name")]
# with nothing typed the list is ranked in the database itself, so "Largest first" shows the biggest
# files in your whole library, not just the biggest of the recent ones
RECENT_ORDER = {"best": "mtime_ns DESC", "newest": "mtime_ns DESC", "largest": "size DESC", "smallest": "size ASC",
                "name": "name COLLATE NOCASE"}
SECTION_NAME = {"best": "RECENT", "newest": "NEWEST", "largest": "LARGEST FILES", "smallest": "SMALLEST FILES",
                "name": "A TO Z"}
RECENT_DAYS = 90
CARD_PX = 360  # thumbnail size for cards (enough for high-DPI screens)
ART_PX = 760  # the preview panel's picture
FIRST_THUMBS = 90  # previews requested per search; the rest keep their symbol


def thumbnail(path: str, px: int) -> QImage | None:
    return decode_image(path, px)


def _row(fid, path, name, kind, size, mtime_ns, why: str = "") -> Row:
    return Row(fid, name, short_folder(path), friendly_date(mtime_ns), fmt_size(size), path,
               extra={"kind": kind or "other", "size": size, "mtime": mtime_ns, "why": why},
               size=size or 0, when=seconds(mtime_ns))


def _type_name(kind: str, path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if kind in ("document", "code", "data", "archive", "installer", "other") and ext:
        return ext[1:].upper()
    return KIND_NAME.get(kind, "File")


def greeting(now: dt.datetime) -> str:
    h = now.hour
    return ("Good night" if h < 5 else "Good morning" if h < 12 else "Good afternoon" if h < 17 else
            "Good evening" if h < 22 else "Good night")


class PreviewArt(QWidget):
    """The preview panel's picture: the image or rendered page, or the file type's symbol."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(200)
        self.img: QImage | None = None
        self.glyph, self.ext, self.kind = "document", "", "other"

    def set_file(self, kind: str, path: str, img: QImage | None) -> None:
        self.img, self.kind = img, kind
        self.ext = os.path.splitext(path)[1].lower()
        self.glyph = theme.kind_glyph(kind, self.ext)
        self.update()

    def paintEvent(self, _e) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, theme.qcolor(t.tile))
        if self.img is not None:
            draw_preview(p, self.img, self.rect(), self.kind, 14)
        else:
            p.setFont(theme.icon_font(44))
            p.setPen(theme.qcolor(t.text3))
            p.drawText(r.adjusted(0, -12, 0, -12), Qt.AlignmentFlag.AlignCenter, theme.GLYPH[self.glyph])
            if self.ext:
                f = theme.font(8, theme.QFont.Weight.DemiBold)
                f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.6)
                p.setFont(f)
                p.drawText(r.adjusted(0, 52, 0, 0), Qt.AlignmentFlag.AlignCenter, self.ext[1:].upper()[:5])
        p.end()


class SearchPage(QWidget):
    def __init__(self, ctx: AppContext, service: IndexService, toast, open_settings):
        super().__init__()
        self.ctx, self.service, self.toast, self.open_settings = ctx, service, toast, open_settings
        self.seq = 0
        self.related_to: str | None = None
        self._inspected: Row | None = None
        self._banner_kind = ""
        self.thumbs = ThumbLoader(self)
        self.thumbs.ready.connect(self._thumb_ready)
        self.gen = 0

        outer = QHBoxLayout(self)  # one centred column, so the field, chips and results line up
        outer.setContentsMargins(26, 10, 26, 16)
        column = QWidget()
        column.setMaximumWidth(1280)
        outer.addWidget(column, 1)
        lay = QVBoxLayout(column)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ---- the top block: greeting, field, chips ----
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(14)
        lay.addWidget(top)

        self.hero = QWidget()
        hl = QVBoxLayout(self.hero)
        hl.setContentsMargins(4, 44, 0, 10)
        hl.setSpacing(4)
        self.eyebrow = label("", "eyebrow")
        self.hello = label("", "greeting")
        hl.addWidget(self.eyebrow)
        hl.addWidget(self.hello)
        tl.addWidget(self.hero)
        self._hero_h = self.hero.sizeHint().height()
        self._hero_open = True
        self._hero_anim = QPropertyAnimation(self.hero, b"maximumHeight", self)
        self._hero_anim.setDuration(260)
        self._hero_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.pill = Glass(29, strong=True)
        self.pill.setFixedHeight(58)
        pl = QHBoxLayout(self.pill)
        pl.setContentsMargins(12, 0, 10, 0)
        pl.setSpacing(10)
        self.orb = Orb(36)
        self.field = QLineEdit()
        self.field.setObjectName("search")
        self.field.setPlaceholderText("Describe what you're looking for  -  \"rent agreement\", \"sunset at the beach\"")
        self.field.setClearButtonEnabled(True)
        self.field.textChanged.connect(self._typed)
        self.field.returnPressed.connect(self.open_current)
        self.field.installEventFilter(self)
        self.go_btn = IconButton("forward", "Search", self._go, 38, "go")
        pl.addWidget(self.orb)
        pl.addWidget(self.field, 1)
        pl.addWidget(self.go_btn)
        tl.addWidget(self.pill)

        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        self.scope = Chips(CHIPS)
        self.scope.changed.connect(lambda _: self.run())
        self.recent = chip("Last 3 months")
        self.recent.toggled.connect(lambda _: self.run())
        chips_row.addWidget(self.scope)
        chips_row.addWidget(self.recent)
        chips_row.addStretch(1)
        self.count = label("", "caption")
        chips_row.addWidget(self.count)
        self.sort = SortButton(SORTS, "best")
        self.sort.changed.connect(self._sorted)
        chips_row.addWidget(self.sort)
        chips_row.addSpacing(6)
        self.view_grid = IconButton("grid", "Cards", lambda: self.set_view(True), 32, checkable=True)
        self.view_list = IconButton("list", "List", lambda: self.set_view(False), 32, checkable=True)
        views = QButtonGroup(self)
        views.addButton(self.view_grid)
        views.addButton(self.view_list)
        self.view_grid.setChecked(True)
        chips_row.addWidget(self.view_grid)
        chips_row.addWidget(self.view_list)
        tl.addLayout(chips_row)

        self.banner = Glass(14)
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 8, 8, 8)
        self.banner_icon = GlyphLabel("sync", 16)
        self.banner_text = label("", "secondary", wrap=True)
        self.banner_btn = button("", "secondary")
        self.banner_btn.clicked.connect(self._banner_action)
        bl.addWidget(self.banner_icon)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(self.banner_btn)
        self.banner.hide()
        tl.addWidget(self.banner)
        lay.addSpacing(18)

        # ---- results and the preview panel ----
        body = QHBoxLayout()
        body.setSpacing(14)
        lay.addLayout(body, 1)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        col = QWidget()
        cl = QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        head = QHBoxLayout()
        self.back = button("Back to results", "plain", "back", self._leave_related)
        self.back.hide()
        self.section = label("", "eyebrow")
        head.addWidget(self.back)
        head.addWidget(self.section)
        head.addStretch(1)
        cl.addLayout(head)
        self.list = FileList(grid=True)
        self.list.selectionModel().currentChanged.connect(lambda *_: self._inspect(self.list.current_row()))
        self.list.activated.connect(lambda _: self.open_current())
        cl.addWidget(self.list, 1)
        self.stack.addWidget(col)
        self.empty = EmptyState("search", "", "")
        self.stack.addWidget(self.empty)

        self._build_inspector()
        body.addWidget(self.inspector, 0, Qt.AlignmentFlag.AlignTop)  # as tall as its content, not the window
        delete = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.list)
        delete.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete.activated.connect(self.remove_current)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self.run)
        self._clock = QTimer(self)
        self._clock.setInterval(30_000)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start()
        self._tick_clock()
        ctx.backend_changed.connect(self._update_banner)
        ctx.data_changed.connect(lambda what: what in ("index", "files", "quarantine") and self.isVisible()
                                 and not self.field.text().strip() and self.run())
        service.progress.connect(lambda d, t, txt: self._update_banner())
        service.state_changed.connect(lambda s: self._update_banner())

    # ---- layout pieces -----------------------------------------------------------------------
    def _build_inspector(self) -> None:
        self.inspector = Glass(22, strong=True)
        self.inspector.setFixedWidth(340)
        il = QVBoxLayout(self.inspector)
        il.setContentsMargins(16, 16, 16, 16)
        il.setSpacing(10)
        self.art = PreviewArt()
        il.addWidget(self.art)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        self.i_icon = GlyphLabel("document", 16, theme.current().text2)
        self.i_name = label("", "headline", wrap=True)
        name_row.addWidget(self.i_icon, 0, Qt.AlignmentFlag.AlignTop)
        name_row.addWidget(self.i_name, 1)
        il.addLayout(name_row)
        self.i_meta = label("", "caption", wrap=True)
        il.addWidget(self.i_meta)
        acts = QHBoxLayout()
        acts.setSpacing(8)
        self.open_btn = button("Open", "primary", slot=self.open_current)
        self.open_btn.setMinimumWidth(96)
        acts.addWidget(self.open_btn)
        acts.addWidget(IconButton("reveal", "Show in folder", lambda: self._current() and reveal(self._current().path)))
        acts.addWidget(IconButton("link", "Copy the file's location", self._copy_path))
        self.similar_btn = IconButton("sparkle", "Find similar files", self.find_similar)
        acts.addWidget(self.similar_btn)
        self.remove_btn = IconButton("trash", "Remove: moves it to Recently Removed, with Undo (Delete key)",
                                     self.remove_current)
        acts.addWidget(self.remove_btn)
        acts.addStretch(1)
        il.addLayout(acts)
        il.addSpacing(2)
        self.tabs = Segmented([("summary", "Summary"), ("related", "Related"), ("details", "Details")])
        self.tabs.changed.connect(self._tab)
        il.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignLeft)
        self.tab_stack = QStackedWidget()
        self.tab_stack.setMinimumHeight(240)
        il.addWidget(self.tab_stack, 1)

        sw = QWidget()
        sl = QVBoxLayout(sw)
        sl.setContentsMargins(2, 4, 2, 0)
        sl.setSpacing(8)
        self.i_why = label("", "caption", wrap=True)
        self.preview = label("", "previewText", wrap=True)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sl.addWidget(self.i_why)
        sl.addWidget(self.preview)
        sl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(sw)
        self.tab_stack.addWidget(scroll)

        rw = QWidget()
        rl = QVBoxLayout(rw)
        rl.setContentsMargins(0, 2, 0, 0)
        rl.setSpacing(4)
        self.related_list = FileList()
        self.related_list.activated.connect(lambda ix: self._open_row(self.related_list.model_.rows[ix.row()]))
        self.related_note = label("", "caption", wrap=True)
        self.related_more = button("Show all similar files", "plain", "chevron", self.find_similar)
        rl.addWidget(self.related_list, 1)
        rl.addWidget(self.related_note)
        rl.addWidget(self.related_more, 0, Qt.AlignmentFlag.AlignLeft)
        self.tab_stack.addWidget(rw)

        dw = QWidget()
        self.details = QGridLayout(dw)
        self.details.setContentsMargins(2, 6, 2, 0)
        self.details.setHorizontalSpacing(14)
        self.details.setVerticalSpacing(9)
        self.details.setColumnStretch(1, 1)
        self.details.setRowStretch(20, 1)
        self.i_where = label("", "metaValue", wrap=True)
        self._detail_labels: list = []
        dscroll = QScrollArea()
        dscroll.setWidgetResizable(True)
        dscroll.setWidget(dw)
        self.tab_stack.addWidget(dscroll)

        self.inspector.hide()
        self._panel_anim = QPropertyAnimation(self.inspector, b"maximumWidth", self)
        self._panel_anim.setDuration(220)
        self._panel_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ---- events ------------------------------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        self._update_banner()
        if not self.field.text().strip():
            self.run()
        self.field.setFocus()

    def eventFilter(self, obj, e):
        if obj is self.field:
            if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self.list.setFocus()
                if not self.list.currentIndex().isValid() and self.list.model_.rows:
                    self.list.setCurrentIndex(self.list.model_.index(0))
                return False
            if e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
                if self.field.text():
                    self.field.clear()
                else:
                    self._hide_inspector()
                return True
            if e.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                self.pill.set_lit(e.type() == QEvent.Type.FocusIn)
        return super().eventFilter(obj, e)

    def _typed(self, text: str) -> None:
        self._set_hero(not text.strip())
        self._debounce.start()

    def _go(self) -> None:
        self._debounce.stop()
        self.run()
        self.field.setFocus()

    def _tick_clock(self) -> None:
        now = dt.datetime.now()
        self.eyebrow.setText(f"{now:%A}  ·  {now.day} {now:%B}  ·  {now:%H:%M}".upper())
        self.hello.setText(greeting(now))

    def _set_hero(self, open_: bool) -> None:
        if open_ == self._hero_open:
            return
        self._hero_open = open_
        self._hero_h = max(self._hero_h, self.hero.sizeHint().height())
        self._hero_anim.stop()
        self._hero_anim.setStartValue(self.hero.maximumHeight() if self.hero.maximumHeight() < 10_000 else self._hero_h)
        self._hero_anim.setEndValue(self._hero_h if open_ else 0)
        self._hero_anim.start()

    def set_view(self, grid: bool) -> None:
        cur = self.list.currentIndex()
        self.list.set_grid(grid)
        (self.view_grid if grid else self.view_list).setChecked(True)
        if cur.isValid():
            self.list.scrollTo(cur)

    # ---- searching -------------------------------------------------------------------------
    def _filters(self) -> tuple[str, str, str]:
        scope, extra, where = CHIP_FILTER.get(self.scope.value(), CHIP_FILTER["all"])
        if self.recent.isChecked():
            since = dt.date.today() - dt.timedelta(days=RECENT_DAYS)
            extra = f"{extra} after:{since.isoformat()}".strip()
            where += f" AND mtime_ns >= {int(dt.datetime.combine(since, dt.time()).timestamp() * 1e9)}"
        return scope, extra, where

    def run(self) -> None:
        self.related_to = None
        self.back.hide()
        q = self.field.text().strip()
        scope, extra, where = self._filters()
        sortkey = self.sort.value()
        self.seq += 1
        seq, ctx = self.seq, self.ctx
        n_files = ctx.conn.execute("SELECT count(*) FROM files").fetchone()[0]
        if n_files == 0:
            self._show_empty("search", "Your files will appear here",
                             "Choose the folders to look after in Settings." if not self.service.state == "running"
                             else "Getting to know your files... this takes a few minutes the first time.")
            return
        backend, vision = ctx.backend, ctx.vision
        self.orb.set_active(bool(q))

        def work(job):
            conn = db.connect()
            try:
                if not q:
                    rows = conn.execute(f"SELECT id, path, name, kind, size, mtime_ns FROM files WHERE {where}"
                                        f" ORDER BY {RECENT_ORDER[sortkey]} LIMIT 100").fetchall()
                    out = [_row(r["id"], r["path"], r["name"], r["kind"], r["size"], r["mtime_ns"]) for r in rows]
                    problems = []
                else:
                    from ...search import SearchEngine

                    se = SearchEngine(conn, backend, vision if scope != "documents" else None)
                    hits = se.search(f"{q} {extra}".strip(), 120, scope=scope)
                    problems = se.last_problems
                    out = [_row(h.file_id, h.path, h.name, h.kind, h.size, h.mtime_ns, _short_reason_hit(h)) for h in hits]
                    for r, h in zip(out, hits):
                        r.extra["reasons"] = h.reasons
                        r.extra["snippet"] = h.snippet
                return out, problems
            finally:
                conn.close()

        ctx.run(work, lambda res: self._show(seq, q, scope, sortkey, *res), lambda msg: self.orb.set_active(False))

    def _show(self, seq: int, q: str, scope: str, sortkey: str, rows: list[Row], problems: list[str]) -> None:
        if seq != self.seq:
            return  # a newer search is on its way
        self.orb.set_active(False)
        self._hide_inspector()
        if not rows:
            if q:
                self._show_empty("search", "No results", "Try describing it differently, or check the spelling."
                                 + (f" ({'; '.join(problems)})" if problems else ""))
            else:
                self._show_empty("search", "Nothing here yet", "")
            return
        self.count.setText(f"{len(rows)} result{'s' * (len(rows) != 1)}" if q else "")
        self.section.setText("" if q else SECTION_NAME[sortkey])
        self.section.setVisible(not q)
        # typed searches arrive in relevance order and are re-ordered here; the empty-field list
        # was already ranked by the database
        self._fill(rows, sortkey if q else "best")

    def _fill(self, rows: list[Row], order: str = "best") -> None:
        self.list.set_rows(rows, order)
        self.stack.setCurrentIndex(0)
        self.list.scrollToTop()
        self.gen = self.thumbs.new_generation()
        for r in rows[:FIRST_THUMBS]:
            self.thumbs.request(self.gen, r.path, r.extra.get("kind", "other"), CARD_PX)

    def _thumb_ready(self, gen: int, path: str, px: int, img: QImage) -> None:
        if px == ART_PX:
            r = self._inspected
            if r is not None and r.path == path:
                self.art.set_file(r.extra.get("kind", "other"), path, img)
            return
        if gen == self.gen:
            self.list.set_thumb(path, img)
            r = self._inspected
            if r is not None and r.path == path and self.art.img is None:
                self.art.set_file(r.extra.get("kind", "other"), path, img)

    def _show_empty(self, icon: str, title: str, text: str) -> None:
        self.orb.set_active(False)
        self.empty.set_text(title, text)
        self.count.setText("")
        self._hide_inspector()
        self.stack.setCurrentWidget(self.empty)

    # ---- preview panel ---------------------------------------------------------------------
    def _current(self) -> Row | None:
        return self.list.current_row()

    def _show_inspector(self) -> None:
        if self.inspector.isVisible() and self._panel_anim.state() != QPropertyAnimation.State.Running:
            return
        self._panel_anim.stop()
        self.inspector.setMinimumWidth(0)
        self.inspector.setMaximumWidth(0 if not self.inspector.isVisible() else self.inspector.maximumWidth())
        self.inspector.show()
        self._panel_anim.setStartValue(self.inspector.maximumWidth())
        self._panel_anim.setEndValue(340)
        self._panel_anim.start()

    def _hide_inspector(self) -> None:
        self._panel_anim.stop()
        self.inspector.hide()
        self._inspected = None

    def _inspect(self, r: Row | None) -> None:
        if r is None:
            self._hide_inspector()
            return
        self._inspected = r
        kind = r.extra.get("kind", "other")
        hit = self.thumbs.cached(r.path, ART_PX)
        self.art.set_file(kind, r.path, hit if hit is not None else r.thumb)
        if hit is None and self.thumbs.wanted(kind, r.path):
            self.thumbs.request(self.gen, r.path, kind, ART_PX)
        self.i_icon.set_glyph(theme.kind_glyph(kind, os.path.splitext(r.path)[1].lower()))
        self.i_name.setText(r.title)
        self.i_meta.setText("  ·  ".join(s for s in (_type_name(kind, r.path), r.right2, r.right) if s))
        why = r.extra.get("why", "")
        self.i_why.setText(f"Found by {why}" if why else "")
        self.i_why.setVisible(bool(why))
        if kind == "image":
            text = ""
        else:
            from ...search import SearchEngine

            text = r.extra.get("snippet") or SearchEngine(self.ctx.conn).preview(r.key, 900)
            text = " ".join((text or "").replace("[", "").replace("]", "").split())
        self.preview.setText(text[:760] + (" ..." if len(text) > 760 else "") if text else
                             ("A picture. Describe what's in it to find others like it." if kind == "image" else
                              "No text to show for this file."))
        self.similar_btn.setVisible(self.ctx.backend is not None and not self.related_to)
        self._fill_details(r)
        if self.tabs.value() == "related":
            self._load_related(r)
        self._show_inspector()

    def _tab(self, key: str) -> None:
        self.tab_stack.setCurrentIndex({"summary": 0, "related": 1, "details": 2}[key])
        if key == "related" and self._inspected is not None:
            self._load_related(self._inspected)

    def _fill_details(self, r: Row) -> None:
        for w in self._detail_labels:
            self.details.removeWidget(w)
            if w is not self.i_where:
                w.deleteLater()
        self._detail_labels = []
        row = self.ctx.conn.execute("SELECT size, mtime_ns, ctime_ns, width, height, camera, title FROM files "
                                    "WHERE id=?", (r.key,)).fetchone()
        kind = r.extra.get("kind", "other")
        facts = [("Kind", _type_name(kind, r.path) if kind not in KIND_NAME or kind in ("document", "code") else
                  KIND_NAME[kind])]
        if row is not None:
            facts.append(("Size", fmt_size(row["size"])))
            facts.append(("Modified", _long_date(row["mtime_ns"])))
            if row["ctime_ns"]:
                facts.append(("Created", _long_date(row["ctime_ns"])))
            if row["width"] and row["height"]:
                facts.append(("Dimensions", f"{row['width']} × {row['height']}"))
            if row["camera"]:
                facts.append(("Camera", row["camera"]))
            if row["title"] and row["title"] != r.title:
                facts.append(("Title", row["title"]))
        # break long paths after each folder name rather than mid-word (the copy button gives the exact text)
        self.i_where.setText(os.path.dirname(r.path).replace(os.sep, os.sep + "​"))
        for i, (k, v) in enumerate(facts):
            kl, vl = label(k, "metaKey"), label(v, "metaValue", wrap=True)
            self.details.addWidget(kl, i, 0, Qt.AlignmentFlag.AlignTop)
            self.details.addWidget(vl, i, 1)
            self._detail_labels += [kl, vl]
        n = len(facts)
        where_key = label("Location", "metaKey")
        self.details.addWidget(where_key, n, 0, Qt.AlignmentFlag.AlignTop)
        self.details.addWidget(self.i_where, n, 1)
        self._detail_labels += [where_key, self.i_where]

    def _load_related(self, r: Row) -> None:
        self.related_list.set_rows([])
        if self.ctx.backend is None:
            self.related_note.setText("Similar files appear here once smart search is set up.")
            self.related_more.hide()
            return
        self.related_note.setText("Looking for similar files...")
        self.related_more.hide()
        backend, key = self.ctx.backend, r.key

        def work(job):
            from ...search import SearchEngine

            conn = db.connect()
            try:
                return [_row(h.file_id, h.path, h.name, h.kind, h.size, h.mtime_ns, "similar content")
                        for h in SearchEngine(conn, backend).related(key, 8)]
            finally:
                conn.close()

        def done(rows):
            if self._inspected is None or self._inspected.key != key:
                return
            self.related_list.set_rows(rows)
            self.related_note.setText("" if rows else "Nothing similar found.")
            self.related_more.setVisible(bool(rows))

        self.ctx.run(work, done, lambda msg: self.related_note.setText("Couldn't look for similar files right now."))

    def _copy_path(self) -> None:
        r = self._current()
        if r is not None:
            QGuiApplication.clipboard().setText(r.path)
            self.toast.show_message("Location copied", ms=2500)

    def _open_row(self, r: Row) -> None:
        if not os.path.exists(r.path):
            self.toast.show_message("That file has moved or was deleted. The index updates shortly.")
            return
        open_file(r.path)

    def open_current(self) -> None:
        r = self._current()
        if r is None and self.list.model_.rows and self.stack.currentIndex() == 0:
            r = self.list.model_.rows[0]
        if r is None:
            return
        self._open_row(r)

    def find_similar(self) -> None:
        r = self._current()
        if r is None or self.ctx.backend is None:
            return
        from ...search import SearchEngine

        hits = SearchEngine(self.ctx.conn, self.ctx.backend).related(r.key, 30)
        self.related_to = r.title
        rows = [_row(h.file_id, h.path, h.name, h.kind, h.size, h.mtime_ns, "similar content") for h in hits]
        self.section.setText(f"SIMILAR TO {r.title.upper()}")
        self.section.show()
        self.back.show()
        self._hide_inspector()
        self._fill(rows)

    def _leave_related(self) -> None:
        self.run()

    def _sorted(self, key: str) -> None:
        if self.related_to or self.field.text().strip():
            self.list.model_.sort(key)  # re-order what is on screen
        else:
            self.run()  # the whole library, ranked by the database

    # ---- removing --------------------------------------------------------------------------
    def remove_current(self) -> None:
        r = self._current()
        if r is None or self.ctx.fileop_running or not self.list.model_.rows:
            return
        fid, title, size = r.key, r.title, r.size

        def done(res) -> None:
            ok = [x.op_id for x in res.ok]
            if ok:
                self.list.set_rows([x for x in self.list.model_.rows if x.key != fid])
                self._hide_inspector()
                self.toast.show_message(f"Moved {title} ({fmt_size(size)}) to Recently Removed", "Undo",
                                        lambda: self._undo(ok))
            else:
                why = res.failed[0].message if res.failed else "it couldn't be moved"
                self.toast.show_message(f"Couldn't remove {title}: {why}")
            self.ctx.data_changed.emit("quarantine")

        self.ctx.run_fileop(self, "Remove", "Removing...", lambda fo: fo.quarantine([fid], "search: remove"), done)

    def _undo(self, op_ids: list[int]) -> None:
        def done(res) -> None:
            self.toast.show_message(f"Put back {len(res.ok)} file{'s' * (len(res.ok) != 1)}")
            self.ctx.data_changed.emit("quarantine")

        self.ctx.run_fileop(self, "Undo", "Putting back...", lambda fo: fo.restore(op_ids), done)

    # ---- banner ----------------------------------------------------------------------------
    def _update_banner(self) -> None:
        ctx = self.ctx
        if self.service.state == "running":
            self.banner_text.setText("Getting to know your files - results get better as this continues.")
            self.banner_icon.set_glyph("sync")
            self._banner_kind = ""
        elif ctx.backend is None and not ctx.backend_loading and ctx.backend_error and "not downloaded" in ctx.backend_error:
            self.banner_text.setText("Smart search isn't set up yet. Searching by name and words still works.")
            self.banner_icon.set_glyph("download")
            self._banner_kind = "download"
        else:
            self.banner.hide()
            return
        self.banner_btn.setText("Download (about 300 MB)..." if self._banner_kind == "download" else "")
        self.banner_btn.setVisible(bool(self._banner_kind))
        self.banner.show()

    def _banner_action(self) -> None:
        if self._banner_kind == "download":
            self.open_settings("download")

    def shutdown(self) -> None:
        self.thumbs.shutdown()


def _long_date(ns: int | None) -> str:
    if not ns:
        return ""
    d = dt.datetime.fromtimestamp(ns / 1e9 if ns > 1e12 else ns)
    return f"{d.day} {d:%B %Y}, {d:%H:%M}"


def _short_reason_hit(h) -> str:
    class _H:  # quick search's wording, shared so both windows explain matches the same way
        reasons = h.reasons

    why = _short_reason(_H)
    return {"text match": "words in the file", "name match": "its name", "similar meaning": "its meaning",
            "picture matches": "what's in the picture", "picture matches (strong)": "what's in the picture"}.get(why, why)
