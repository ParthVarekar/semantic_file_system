"""Reusable controls for the everyday window: frosted panels over an atmospheric backdrop, an icon
rail, a glowing search orb, chips, a switch, toasts with Undo, fading page transitions, empty
states, and one fast file view that shows either cards with previews or a compact list.

Everything that shows a colour or a symbol paints it at paint time from the current theme, so
switching between light and dark needs no rebuilding."""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import (QAbstractListModel, QEasingCurve, QEvent, QModelIndex, QPoint, QPointF, QPropertyAnimation,
                            QRect, QRectF, QSize, Qt, QTimer, Property, Signal)
from PySide6.QtGui import (QActionGroup, QBrush, QColor, QCursor, QFontMetrics, QIcon, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFileIconProvider, QFrame, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QListView, QMenu, QPushButton, QSizePolicy, QStackedWidget, QStyle,
                               QStyledItemDelegate, QToolTip, QVBoxLayout, QWidget)

from . import theme


# ------------------------------------------------------------------------------------ basics
def label(text: str = "", role: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setObjectName(role)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return lb


def _button_icon(kind: str, icon: str) -> QIcon:
    t = theme.current()
    color = {"primary": t.on_accent, "danger": t.danger}.get(kind, t.text)
    return theme.glyph(icon, 14, color)


def button(text: str, kind: str = "secondary", icon: str | None = None, slot: Callable | None = None) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(kind)
    b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    if icon:
        b.setProperty("glyph", icon)
        b.setIcon(_button_icon(kind, icon))
    if slot:
        b.clicked.connect(slot)
    return b


def refresh_icons(root: QWidget) -> None:
    """Redraw the icons of ``button``s after the theme changed."""
    for b in root.findChildren(QPushButton):
        name = b.property("glyph")
        if name:
            b.setIcon(_button_icon(b.objectName(), name))


def paint_glass(p: QPainter, rect: QRectF, radius: float, strong: bool = False, lit: bool = False) -> None:
    """A frosted panel: translucent fill and a hairline edge that is brighter at the top."""
    t = theme.current()
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.fillPath(path, theme.qcolor(t.glass_strong if strong else t.glass))
    top = theme.qcolor(t.glass_edge_top)
    if lit:
        top.setAlpha(min(255, int(top.alpha() * 2.2)))
    g = QLinearGradient(0, rect.top(), 0, rect.bottom())
    g.setColorAt(0, top)
    g.setColorAt(1, theme.qcolor(t.glass_edge_bottom))
    p.setPen(QPen(QBrush(g), 1.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


class Glass(QFrame):
    """A frosted panel. ``lit`` brightens its edge (used for the focused search field)."""

    def __init__(self, radius: float = 18, strong: bool = False, parent=None):
        super().__init__(parent)
        self.radius, self.strong, self._lit = radius, strong, False

    def set_lit(self, on: bool) -> None:
        self._lit = on
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_glass(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.strong, self._lit)
        p.end()


def card(layout_cls=QVBoxLayout, margins=(18, 16, 18, 16), spacing: int = 8) -> tuple[QFrame, QVBoxLayout]:
    f = Glass(18)
    f.setObjectName("card")
    lay = layout_cls(f)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return f, lay


def hbox(*widgets, spacing: int = 8, stretch_at: int | None = None) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setSpacing(spacing)
    h.setContentsMargins(0, 0, 0, 0)
    for i, w in enumerate(widgets):
        if w is None:
            h.addStretch(1)
        elif isinstance(w, int):
            h.addSpacing(w)
        else:
            h.addWidget(w)
    return h


def _role_of(color: str | None) -> str:
    """Which palette entry ``color`` came from, so the symbol follows later theme changes."""
    if color is None:
        return "text2"
    t = theme.current()
    for name in ("text", "text2", "text3", "accent", "danger", "on_accent"):
        if getattr(t, name) == color:
            return name
    return ""


class GlyphLabel(QLabel):
    """A symbol from the icon font, painted in the current theme's colour."""

    def __init__(self, name: str, size: int = 20, color: str | None = None):
        super().__init__()
        self.name, self.px = name, size
        self.role, self.fixed = _role_of(color), color
        self.setFixedSize(size + 4, size + 4)

    def set_glyph(self, name: str) -> None:
        self.name = name
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(theme.icon_font(self.px))
        color = getattr(theme.current(), self.role) if self.role else self.fixed
        p.setPen(theme.qcolor(color))
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, theme.GLYPH.get(self.name, self.name))
        p.end()


def icon_label(name: str, size: int = 20, color: str | None = None) -> QLabel:
    return GlyphLabel(name, size, color)


class IconButton(QPushButton):
    """A round button that shows one symbol (``kind="go"`` is the filled one in the search field)."""

    def __init__(self, glyph: str, tip: str = "", slot: Callable | None = None, size: int = 34, kind: str = "iconButton",
                 checkable: bool = False):
        super().__init__()
        self.glyph_name = glyph
        self.setObjectName(kind)
        self.setFixedSize(size, size)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip(tip)
        self.setCheckable(checkable)
        if slot:
            self.clicked.connect(slot)

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(theme.icon_font(16 if self.objectName() == "go" else 14))
        color = t.on_accent if self.objectName() == "go" else (t.text if self.isEnabled() else t.text3)
        p.setPen(theme.qcolor(color))
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, theme.GLYPH.get(self.glyph_name, self.glyph_name))
        p.end()


# ------------------------------------------------------------------------------------ atmosphere
class Backdrop(QWidget):
    """The window's background (see ``theme.paint_backdrop``), cached per size and theme."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: tuple | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def refresh(self) -> None:
        self._cache = None
        self.update()

    def paintEvent(self, _e) -> None:
        t = theme.current()
        key = (self.width(), self.height(), t.dark, self.devicePixelRatioF())
        if self._cache is None or self._cache[0] != key:
            ratio = self.devicePixelRatioF()
            pm = QPixmap(int(self.width() * ratio), int(self.height() * ratio))
            pm.setDevicePixelRatio(ratio)
            bp = QPainter(pm)
            theme.paint_backdrop(bp, self.width(), self.height(), t)
            bp.end()
            self._cache = (key, pm)
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache[1])
        p.end()


def paint_orb(p: QPainter, c: QPointF, r: float, phase: float = 0.0, glow: float = 0.0) -> None:
    """A small glass sphere lit from the upper left; ``phase`` turns the light around it."""
    if glow > 0:
        halo = QRadialGradient(c, r * 1.9)
        halo.setColorAt(0.5, QColor(255, 255, 255, int(70 * glow)))
        halo.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(c, r * 1.9, r * 1.9)
    ang = math.radians(225 + phase)
    focal = QPointF(c.x() + math.cos(ang) * r * 0.42, c.y() + math.sin(ang) * r * 0.42)
    g = QRadialGradient(c, r, focal)
    g.setColorAt(0.0, QColor("#FFFFFF"))
    g.setColorAt(0.22, QColor("#D9D9DE"))
    g.setColorAt(0.62, QColor("#55555C"))
    g.setColorAt(1.0, QColor("#0D0D10"))
    p.setPen(QPen(QColor(255, 255, 255, 60), 0.8))
    p.setBrush(g)
    p.drawEllipse(c, r, r)


class Orb(QWidget):
    """The search field's sphere. It slowly turns and glows while something is being worked on."""

    def __init__(self, size: int = 32, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.phase, self.glow, self._active = 0.0, 0.0, False
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def set_active(self, on: bool) -> None:
        self._active = on
        if on or self.glow > 0:
            self._timer.start()

    def _tick(self) -> None:
        self.phase = (self.phase + 4.0) % 360
        self.glow = min(1.0, self.glow + 0.08) if self._active else max(0.0, self.glow - 0.06)
        if not self._active and self.glow <= 0:
            self._timer.stop()
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = min(self.width(), self.height())
        paint_orb(p, QPointF(self.width() / 2, self.height() / 2), s / 2 - 4, self.phase, self.glow)
        p.end()


def paint_ring(p: QPainter, c: QPointF, r: float, value: float, spin: float = 0.0) -> None:
    """A thin progress ring; ``value < 0`` draws a turning arc (progress unknown)."""
    t = theme.current()
    rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(theme.qcolor(t.separator), 2.0))
    p.drawEllipse(rect)
    pen = QPen(theme.qcolor(t.text), 2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    if value < 0:
        p.drawArc(rect, int(-spin * 16), int(90 * 16))
    else:
        p.drawArc(rect, 90 * 16, int(-360 * 16 * max(0.02, min(1.0, value))))


class Ring(QWidget):
    def __init__(self, size: int = 16, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.value, self.spin = -1.0, 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    def set_value(self, v: float) -> None:
        self.value = v
        if v < 0 and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self.spin = (self.spin + 9) % 360
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self.set_value(self.value)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_ring(p, QPointF(self.width() / 2, self.height() / 2), self.width() / 2 - 2, self.value, self.spin)
        p.end()


# ------------------------------------------------------------------------------------ navigation
class Rail(QWidget):
    """A floating column of symbols - one per place, Settings at the bottom - with a highlight that
    glides to the chosen place, and a progress ring while the index is being updated."""

    changed = Signal(str)
    settings_clicked = Signal()
    CELL = 52
    TOP = 66

    def __init__(self, items: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        self.items = items  # (key, name, glyph)
        self.current = items[0][0]
        self.hover = -1
        self._y = float(self.TOP)
        self.progress: float | None = None  # None: idle; <0: working, amount unknown
        self.status_text = ""
        self._spin = 0.0
        self.setFixedWidth(64)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._anim = QPropertyAnimation(self, b"indicator", self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._spinner = QTimer(self)
        self._spinner.setInterval(30)
        self._spinner.timeout.connect(self._tick)

    def _get_y(self) -> float:
        return self._y

    def _set_y(self, v: float) -> None:
        self._y = v
        self.update()

    indicator = Property(float, _get_y, _set_y)

    def _tick(self) -> None:
        self._spin = (self._spin + 9) % 360
        self.update(self._ring_rect().toRect().adjusted(-3, -3, 3, 3))

    def _cell_y(self, i: int) -> float:
        return self.TOP + i * self.CELL

    def _settings_rect(self) -> QRectF:
        return QRectF((self.width() - 44) / 2, self.height() - 14 - 44, 44, 44)

    def _ring_rect(self) -> QRectF:
        s = self._settings_rect()
        return QRectF(self.width() / 2 - 10, s.top() - 34, 20, 20)

    def set_current(self, key: str, animate: bool = True) -> None:
        i = [k for k, *_ in self.items].index(key)
        self.current = key
        if animate and self.isVisible():
            self._anim.stop()
            self._anim.setStartValue(self._y)
            self._anim.setEndValue(self._cell_y(i))
            self._anim.start()
        else:
            self._set_y(self._cell_y(i))

    def set_progress(self, value: float | None, text: str = "") -> None:
        self.progress, self.status_text = value, text
        if value is not None and value < 0:
            self._spinner.start()
        else:
            self._spinner.stop()
        self.update()

    def _index_at(self, pos) -> int:
        for i in range(len(self.items)):
            if QRectF(0, self._cell_y(i), self.width(), self.CELL).contains(QPointF(pos)):
                return i
        if self._settings_rect().contains(QPointF(pos)):
            return len(self.items)
        return -1

    def mouseMoveEvent(self, e) -> None:
        i = self._index_at(e.position().toPoint())
        if i != self.hover:
            self.hover = i
            self.update()

    def leaveEvent(self, e) -> None:
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        i = self._index_at(e.position().toPoint())
        if i == len(self.items):
            self.settings_clicked.emit()
        elif i >= 0:
            key = self.items[i][0]
            self.set_current(key)
            self.changed.emit(key)

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.ToolTip:
            pos = e.pos()
            i = self._index_at(pos)
            tip = (self.items[i][1] if 0 <= i < len(self.items) else "Settings" if i == len(self.items) else
                   self.status_text if self._ring_rect().adjusted(-8, -8, 8, 8).contains(QPointF(pos)) else "")
            if tip:
                QToolTip.showText(e.globalPos(), tip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def paintEvent(self, _e) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_glass(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 24)
        paint_orb(p, QPointF(self.width() / 2, 31), 11)
        ind = QRectF((self.width() - 42) / 2, self._y + (self.CELL - 42) / 2, 42, 42)
        if 0 <= self.hover <= len(self.items):
            hr = (QRectF((self.width() - 42) / 2, self._cell_y(self.hover) + (self.CELL - 42) / 2, 42, 42)
                  if self.hover < len(self.items) else self._settings_rect().adjusted(1, 1, -1, -1))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(t.hover))
            p.drawRoundedRect(hr, 13, 13)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(t.accent))
        p.drawRoundedRect(ind, 13, 13)
        p.setFont(theme.icon_font(18))
        for i, (key, _name, g) in enumerate(self.items):
            cell = QRectF(0, self._cell_y(i), self.width(), self.CELL)
            under = abs(self._y - self._cell_y(i)) < self.CELL / 2
            p.setPen(theme.qcolor(t.on_accent if under else (t.text if i == self.hover else t.text2)))
            p.drawText(cell, Qt.AlignmentFlag.AlignCenter, theme.GLYPH[g])
        p.setPen(theme.qcolor(t.text if self.hover == len(self.items) else t.text2))
        p.drawText(self._settings_rect(), Qt.AlignmentFlag.AlignCenter, theme.GLYPH["settings"])
        if self.progress is not None:
            r = self._ring_rect()
            paint_ring(p, r.center(), r.width() / 2 - 1, self.progress, self._spin)
        p.end()


# ------------------------------------------------------------------------------------ controls
class Segmented(QFrame):
    """A pill-shaped segmented control (like UISegmentedControl)."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str | None = None):
        super().__init__()
        self.setObjectName("segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.keys: dict[QPushButton, str] = {}
        for key, text in options:
            b = QPushButton(text)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.group.addButton(b)
            self.keys[b] = key
            lay.addWidget(b)
            if key == (current or options[0][0]):
                b.setChecked(True)
        self.group.buttonClicked.connect(lambda b: self.changed.emit(self.keys[b]))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def value(self) -> str:
        b = self.group.checkedButton()
        return self.keys[b] if b else ""

    def set_value(self, key: str) -> None:
        for b, k in self.keys.items():
            if k == key:
                b.setChecked(True)


class Chips(QWidget):
    """A row of pill-shaped choices, one selected at a time (same interface as ``Segmented``)."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str | None = None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.keys: dict[QPushButton, str] = {}
        for key, text in options:
            b = chip(text)
            self.group.addButton(b)
            self.keys[b] = key
            lay.addWidget(b)
            if key == (current or options[0][0]):
                b.setChecked(True)
        self.group.buttonClicked.connect(lambda b: self.changed.emit(self.keys[b]))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def value(self) -> str:
        b = self.group.checkedButton()
        return self.keys[b] if b else ""

    def set_value(self, key: str) -> None:
        for b, k in self.keys.items():
            if k == key:
                b.setChecked(True)


def chip(text: str, checkable: bool = True) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("chip")
    b.setCheckable(checkable)
    b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    return b


class SortButton(QPushButton):
    """"Sort: Largest first" - a pill that opens a small menu of orders."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str | None = None):
        super().__init__()
        self.setObjectName("chip")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.options = dict(options)
        self.key = current or options[0][0]
        self.menu_ = QMenu(self)
        self.group = QActionGroup(self)
        self.group.setExclusive(True)
        for k, text in options:
            a = self.menu_.addAction(text)
            a.setCheckable(True)
            a.setData(k)
            a.setChecked(k == self.key)
            self.group.addAction(a)
        self.menu_.triggered.connect(self._picked)
        self.clicked.connect(lambda: self.menu_.exec(self.mapToGlobal(QPoint(0, self.height() + 4))))
        self._retext()

    def _retext(self) -> None:
        self.setText(f"Sort: {self.options[self.key]}  \u25be")

    def _picked(self, action) -> None:
        k = action.data()
        if k != self.key:
            self.set_value(k)
            self.changed.emit(k)

    def set_value(self, key: str) -> None:
        self.key = key
        for a in self.group.actions():
            a.setChecked(a.data() == key)
        self._retext()

    def value(self) -> str:
        return self.key


class Switch(QAbstractButton):
    """An on/off switch with a sliding knob (monochrome: the track fills with the text colour)."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedSize(40, 24)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, v: float) -> None:
        self._pos = v
        self.update()

    pos = Property(float, _get_pos, _set_pos)

    def sizeHint(self) -> QSize:
        return QSize(40, 24)

    @staticmethod
    def _mix(a: QColor, b: QColor, f: float) -> QColor:
        return QColor(int(a.red() + (b.red() - a.red()) * f), int(a.green() + (b.green() - a.green()) * f),
                      int(a.blue() + (b.blue() - a.blue()) * f), int(a.alpha() + (b.alpha() - a.alpha()) * f))

    def paintEvent(self, _e) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off = QColor(255, 255, 255, 34) if t.dark else QColor(0, 0, 0, 28)
        track = self._mix(off, theme.qcolor(t.accent), self._pos)
        knob = self._mix(QColor("#F2F2F3") if t.dark else QColor("#FFFFFF"), theme.qcolor(t.on_accent), self._pos)
        if not self.isEnabled():
            track.setAlpha(track.alpha() // 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 40, 24), 12, 12)
        x = 2 + self._pos * 16
        p.setBrush(QColor(0, 0, 0, 50))
        p.drawEllipse(QRectF(x, 3, 20, 20))
        p.setBrush(knob)
        p.drawEllipse(QRectF(x, 2, 20, 20))
        p.end()


class FadeStack(QStackedWidget):
    """A stacked widget whose pages fade in (short and subtle, as in macOS)."""

    def show_page(self, w: QWidget) -> None:
        if self.currentWidget() is w:
            return
        self.setCurrentWidget(w)
        eff = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", w)
        anim.setDuration(200)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: w.setGraphicsEffect(None))  # effects slow down painting; drop it after
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


class Toast(QFrame):
    """A short message near the bottom of the window, optionally with one action (Undo)."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("toast")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 10, 10, 10)
        lay.setSpacing(14)
        self.text = label("", "toastText")
        self.action = QPushButton()
        self.action.setObjectName("toastAction")
        self.action.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        lay.addWidget(self.text)
        lay.addWidget(self.action)
        self._cb: Callable | None = None
        self.action.clicked.connect(self._act)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_glass(p, r, r.height() / 2, strong=True)
        p.end()

    def show_message(self, text: str, action: str = "", callback: Callable | None = None, ms: int = 7000) -> None:
        self.text.setText(text)
        self.action.setText(action)
        self.action.setVisible(bool(action))
        self._cb = callback
        self.adjustSize()
        par = self.parentWidget()
        end = QPoint((par.width() - self.width()) // 2, par.height() - self.height() - 40)
        self.move(end + QPoint(0, 14))
        self.show()
        self.raise_()
        eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        fade = QPropertyAnimation(eff, b"opacity", self)
        fade.setDuration(220)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.finished.connect(lambda: self.setGraphicsEffect(None))
        fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        rise = QPropertyAnimation(self, b"pos", self)  # rises gently into place
        rise.setDuration(260)
        rise.setStartValue(self.pos())
        rise.setEndValue(end)
        rise.setEasingCurve(QEasingCurve.Type.OutCubic)
        rise.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._timer.start(ms)

    def _act(self) -> None:
        cb, self._cb = self._cb, None
        self.hide()
        if cb:
            cb()


class EmptyState(QWidget):
    """Big quiet icon, a title, one sentence and at most one button."""

    def __init__(self, icon: str, title: str, text: str, action: str = "", slot: Callable | None = None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addStretch(2)
        self.icon = GlyphLabel(icon, 40, theme.current().text3)
        self.icon.setFixedSize(60, 60)
        self.title = label(title, "title")
        self.text = label(text, "secondary", wrap=True)
        for w in (self.icon, self.title, self.text):
            lay.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setMaximumWidth(420)
        self.text.setMinimumWidth(320)  # a centred, word-wrapped label is otherwise measured too narrow
        self.button = button(action, "primary", slot=slot) if action else None
        if self.button:
            lay.addSpacing(8)
            lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(3)

    def set_text(self, title: str, text: str) -> None:
        self.title.setText(title)
        self.text.setText(text)


# ------------------------------------------------------------------------------------ file views
@dataclass
class Row:
    key: int
    title: str
    subtitle: str = ""
    right: str = ""
    right2: str = ""
    path: str = ""
    checked: bool | None = None  # None: not checkable
    thumb: QImage | None = None
    badge: str = ""  # small tag after the title, e.g. "Keep"
    data: object = None
    extra: dict = field(default_factory=dict)
    size: int = 0  # bytes, for ranking by storage
    when: float = 0.0  # seconds since 1970, for ranking by date


# How lists can be ordered. "best" keeps the order the list arrived in (the search ranking, the
# suggested order, ...); the others sort by what the row carries.
ORDERS = {
    "largest": lambda r: -r.size,
    "smallest": lambda r: r.size,
    "newest": lambda r: -r.when,
    "oldest": lambda r: r.when,
    "name": lambda r: r.title.lower(),
}


def seconds(ns_or_s: float | int | None) -> float:
    """File times are stored in nanoseconds; accept seconds too."""
    v = float(ns_or_s or 0)
    return v / 1e9 if v > 1e12 else v


class RowModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[Row] = []
        self._icons = QFileIconProvider()
        self._cache: dict[str, QIcon] = {}
        self._by_path: dict[str, list[int]] = {}
        self.born: dict[int, float] = {}  # row -> when its thumbnail arrived (for the fade-in)
        self._orig: list[Row] = []
        self.order = "best"

    def set_rows(self, rows: list[Row], order: str | None = None) -> None:
        self._orig = list(rows)
        if order is not None:
            self.order = order
        self._apply()

    def sort(self, order: str) -> None:
        self.order = order
        self._apply()

    def _apply(self) -> None:
        rows = list(self._orig)
        fn = ORDERS.get(self.order)
        if fn is not None:
            rows.sort(key=fn)  # stable: equal sizes keep their arrival order
        self.beginResetModel()
        self.rows = rows
        self.born = {}
        self._by_path = {}
        for i, r in enumerate(rows):
            self._by_path.setdefault(r.path, []).append(i)
        self.endResetModel()

    def set_thumb(self, path: str, img: QImage) -> bool:
        hit = False
        for i in self._by_path.get(path, []):
            if self.rows[i].thumb is None:
                self.rows[i].thumb = img
                self.born[i] = time.monotonic()
                ix = self.index(i)
                self.dataChanged.emit(ix, ix)
                hit = True
        return hit

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return r
        if role == Qt.ItemDataRole.DisplayRole:
            return r.title
        if role == Qt.ItemDataRole.ToolTipRole:
            return r.path or None
        return None

    def toggle(self, row: int) -> None:
        r = self.rows[row]
        if r.checked is not None:
            r.checked = not r.checked
            ix = self.index(row)
            self.dataChanged.emit(ix, ix)

    def checked(self) -> list[Row]:
        return [r for r in self.rows if r.checked]

    def set_all(self, on: bool) -> None:
        for r in self.rows:
            if r.checked is not None:
                r.checked = on
        if self.rows:
            self.dataChanged.emit(self.index(0), self.index(len(self.rows) - 1))

    def icon_for(self, path: str) -> QIcon:
        ext = os.path.splitext(path)[1].lower()
        key = path if ext in (".exe", ".lnk", ".ico", ".url") else ext
        if key not in self._cache:
            from PySide6.QtCore import QFileInfo

            ic = self._icons.icon(QFileInfo(path))
            if ic.isNull() or not ic.availableSizes():  # e.g. a removed file: no longer on disk to ask Windows about
                ic = theme.glyph("photo" if ext in (".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif") else "document",
                                 32, theme.current().text3)
            self._cache[key] = ic
        return self._cache[key]


FADE_S = 0.22


def _fade(model: RowModel, row: int) -> float:
    born = model.born.get(row)
    return 1.0 if born is None else min(1.0, (time.monotonic() - born) / FADE_S)


def _draw_cover(p: QPainter, img: QImage, rect: QRect, radius: float, opacity: float = 1.0) -> None:
    """Draw ``img`` filling ``rect`` (cropped, never stretched) with rounded corners."""
    pm = QPixmap.fromImage(img)
    size = pm.size().scaled(rect.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding)
    tr = QRect(0, 0, size.width(), size.height())
    tr.moveCenter(rect.center())
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(rect), radius, radius)
    p.save()
    p.setClipPath(clip)
    p.setOpacity(opacity)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawPixmap(tr, pm)
    p.restore()


def _draw_paper(p: QPainter, img: QImage, well: QRect, opacity: float = 1.0) -> None:
    """A document's first page (or a slide) as a sheet resting in ``well``: top-aligned, with a
    soft shadow, cut off by the well's bottom edge rather than squeezed to fit."""
    a = img.height() / max(1, img.width())
    pw = (well.width() - 20) * (0.88 if a < 1 else 0.6)
    ph = pw * a
    top = well.top() + (well.height() - ph) / 2 if ph < well.height() - 20 else well.top() + 12
    page = QRectF(well.center().x() - pw / 2, top, pw, ph)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(well), 11, 11)
    p.save()
    p.setClipPath(clip)
    p.setOpacity(opacity)
    p.setPen(Qt.PenStyle.NoPen)
    for i, alpha in enumerate((40, 22, 10)):
        p.setBrush(QColor(0, 0, 0, alpha))
        p.drawRoundedRect(page.adjusted(-1 - i, 1 - i, 1 + i, 3 + i), 4 + i, 4 + i)
    sheet = QPainterPath()
    sheet.addRoundedRect(page, 3, 3)
    p.setClipPath(sheet, Qt.ClipOperation.IntersectClip)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(page, img)
    p.restore()


def draw_preview(p: QPainter, img: QImage, well: QRect, kind: str, radius: float, opacity: float = 1.0) -> None:
    """Photos fill the well; documents and slides sit in it as a sheet of paper."""
    if kind in ("image", "video"):
        _draw_cover(p, img, well, radius, opacity)
    else:
        _draw_paper(p, img, well, opacity)


class RowDelegate(QStyledItemDelegate):
    """Paints a row: [round check] [thumbnail or file icon] title + subtitle ... right texts."""

    ROW_H = 56
    THUMB = 36

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), self.ROW_H)

    def paint(self, p: QPainter, opt, index) -> None:
        r: Row = index.data(Qt.ItemDataRole.UserRole)
        t = theme.current()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = opt.rect.adjusted(4, 2, -4, -2)
        if opt.state & QStyle.StateFlag.State_Selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(t.selection))
            p.drawRoundedRect(rect, 12, 12)
        elif opt.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(t.hover))
            p.drawRoundedRect(rect, 12, 12)
        x = rect.left() + 12
        cy = rect.center().y()
        if r.checked is not None:  # round check, like Photos / Mail in edit mode
            c = QRectF(x, cy - 10, 20, 20)
            if r.checked:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.qcolor(t.accent))
                p.drawEllipse(c)
                pen = QPen(theme.qcolor(t.on_accent), 2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(int(x + 5.5), int(cy + 0.5), int(x + 8.5), int(cy + 3.5))
                p.drawLine(int(x + 8.5), int(cy + 3.5), int(x + 14.5), int(cy - 3))
            else:
                p.setPen(QPen(theme.qcolor(t.text3), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c.adjusted(1, 1, -1, -1))
            x += 32
        icon_rect = QRect(int(x), cy - self.THUMB // 2, self.THUMB, self.THUMB)
        if r.thumb is not None:
            _draw_cover(p, r.thumb, icon_rect, 8, _fade(index.model(), index.row()))
        elif r.path:
            index.model().icon_for(r.path).paint(p, icon_rect.adjusted(3, 3, -3, -3))
        else:
            p.setFont(theme.icon_font(20))
            p.setPen(theme.qcolor(t.text3))
            p.drawText(QRectF(icon_rect), Qt.AlignmentFlag.AlignCenter, theme.GLYPH.get(r.extra.get("glyph", "package"), "?"))
        x = icon_rect.right() + 14
        fm_r = QFontMetrics(theme.font(9))
        right_w = max(fm_r.horizontalAdvance(r.right), fm_r.horizontalAdvance(r.right2)) + 16 if (r.right or r.right2) else 0
        text_w = rect.right() - right_w - x - 8
        tf = theme.font(10.5, theme.QFont.Weight.DemiBold)
        p.setFont(tf)
        fm = QFontMetrics(tf)
        title = fm.elidedText(r.title, Qt.TextElideMode.ElideMiddle, max(10, text_w - (60 if r.badge else 0)))
        p.setPen(theme.qcolor(t.text))
        top = rect.top() + 9
        p.drawText(QRect(int(x), top, text_w, fm.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        if r.badge:
            bx = x + fm.horizontalAdvance(title) + 8
            bf = theme.font(8, theme.QFont.Weight.DemiBold)
            bw = QFontMetrics(bf).horizontalAdvance(r.badge) + 12
            br = QRectF(bx, top + 2, bw, fm.height() - 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(t.accent_soft))
            p.drawRoundedRect(br, 6, 6)
            p.setFont(bf)
            p.setPen(theme.qcolor(t.text))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, r.badge)
        sf = theme.font(9)
        p.setFont(sf)
        p.setPen(theme.qcolor(t.text2))
        fs = QFontMetrics(sf)
        p.drawText(QRect(int(x), rect.bottom() - 9 - fs.height(), text_w, fs.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fs.elidedText(r.subtitle, Qt.TextElideMode.ElideMiddle, text_w))
        if right_w:
            rr = QRect(rect.right() - right_w - 6, top, right_w, fs.height() + 2)
            p.drawText(rr, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, r.right)
            p.drawText(rr.translated(0, fs.height() + 5), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       r.right2)
        p.restore()


class CardDelegate(QStyledItemDelegate):
    """Paints a result as a card: a preview (or the file type's symbol) with a small type badge,
    then the name and one quiet line of size and date."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.size = QSize(190, 190)

    def sizeHint(self, option, index) -> QSize:
        return self.size

    def paint(self, p: QPainter, opt, index) -> None:
        r: Row = index.data(Qt.ItemDataRole.UserRole)
        t = theme.current()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(opt.rect.adjusted(6, 6, -6, -6))
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)
        p.fillPath(path, theme.qcolor(t.glass_strong if (selected or hover) else t.glass))
        edge = theme.qcolor(t.accent if selected else (t.glass_edge_top if hover else t.separator))
        if selected:
            edge.setAlpha(200)
        p.setPen(QPen(edge, 1.4 if selected else 1.0))
        p.drawPath(path)

        well = QRect(int(rect.left() + 7), int(rect.top() + 7), int(rect.width() - 14), int((rect.width() - 14) * 0.64))
        kind = r.extra.get("kind", "other")
        ext = os.path.splitext(r.path)[1].lower()
        wp = QPainterPath()
        wp.addRoundedRect(QRectF(well), 11, 11)
        p.fillPath(wp, theme.qcolor(t.tile))
        g = theme.kind_glyph(kind, ext)
        if r.thumb is not None:
            draw_preview(p, r.thumb, well, kind, 11, _fade(index.model(), index.row()))
            badge = QRectF(well.left() + 7, well.bottom() - 29, 22, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(10, 10, 12, 150))
            p.drawRoundedRect(badge, 7, 7)
            p.setFont(theme.icon_font(12))
            p.setPen(QColor(255, 255, 255, 235))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, theme.GLYPH[g])
        else:
            p.setFont(theme.icon_font(30))
            p.setPen(theme.qcolor(t.text3))
            p.drawText(QRectF(well).adjusted(0, -10, 0, -10), Qt.AlignmentFlag.AlignCenter, theme.GLYPH[g])
            if ext:
                f = theme.font(7.5, theme.QFont.Weight.DemiBold)
                f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.4)
                p.setFont(f)
                p.drawText(QRectF(well).adjusted(0, 38, 0, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                           ext[1:].upper()[:5])

        tf = theme.font(9.5, theme.QFont.Weight.DemiBold)
        fm = QFontMetrics(tf)
        x, w = int(rect.left() + 11), int(rect.width() - 22)
        y = well.bottom() + 9
        p.setFont(tf)
        p.setPen(theme.qcolor(t.text))
        p.drawText(QRect(x, y, w, fm.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(r.title, Qt.TextElideMode.ElideMiddle, w))
        sf = theme.font(8.5)
        fs = QFontMetrics(sf)
        p.setFont(sf)
        p.setPen(theme.qcolor(t.text2))
        meta = "  ·  ".join(s for s in (r.right2, r.right) if s)
        p.drawText(QRect(x, y + fm.height() + 2, w, fs.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fs.elidedText(meta, Qt.TextElideMode.ElideRight, w))
        p.restore()


class FileList(QListView):
    """A borderless view of ``Row``s: a compact list, or (``grid=True``) cards that reflow to the
    width. Checkable lists toggle on click; others select."""

    toggled = Signal()
    MIN_CARD = 184

    def __init__(self, checkable: bool = False, grid: bool = False):
        super().__init__()
        self.model_ = RowModel(self)
        self.setModel(self.model_)
        self._list_delegate = RowDelegate(self)
        self._card_delegate = CardDelegate(self)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.checkable = checkable
        self.grid = None
        self.set_grid(grid)
        self._fader = QTimer(self)
        self._fader.setInterval(16)
        self._fader.timeout.connect(self._fade_tick)
        if checkable:
            self.setSelectionMode(QListView.SelectionMode.NoSelection)
            self.clicked.connect(self._toggle)

    def set_grid(self, on: bool) -> None:
        if on == self.grid:
            return
        self.grid = on
        if on:
            self.setViewMode(QListView.ViewMode.IconMode)
            self.setMovement(QListView.Movement.Static)
            self.setResizeMode(QListView.ResizeMode.Adjust)
            self.setWrapping(True)
            self.setSpacing(0)
            self.setItemDelegate(self._card_delegate)
            self._relayout()
        else:
            self.setViewMode(QListView.ViewMode.ListMode)
            self.setWrapping(False)
            self.setGridSize(QSize())
            self.setItemDelegate(self._list_delegate)
        self.scheduleDelayedItemsLayout()

    def _relayout(self) -> None:
        if not self.grid:
            return
        vw = max(self.MIN_CARD, self.viewport().width() - 2)
        cols = max(1, vw // self.MIN_CARD)
        cw = vw // cols
        ch = int((cw - 26) * 0.64) + 26 + 46
        size = QSize(cw, ch)
        if size != self.gridSize():
            self._card_delegate.size = size
            self.setGridSize(size)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()

    def set_thumb(self, path: str, img: QImage) -> None:
        if self.model_.set_thumb(path, img):
            self._fader.start()

    def _fade_tick(self) -> None:
        now = time.monotonic()
        if all(now - b > FADE_S for b in self.model_.born.values()):
            self._fader.stop()
        self.viewport().update()

    def _toggle(self, ix: QModelIndex) -> None:
        self.model_.toggle(ix.row())
        self.toggled.emit()

    def set_rows(self, rows: list[Row], order: str | None = None) -> None:
        self.model_.set_rows(rows, order)

    def current_row(self) -> Row | None:
        ix = self.currentIndex()
        return self.model_.rows[ix.row()] if ix.isValid() and ix.row() < len(self.model_.rows) else None


def fmt_size(n: float | None) -> str:
    n = float(n or 0)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}".replace(".0 ", " ")
        n /= 1024  # as File Explorer shows sizes, so the numbers match what people see there
    return ""


def friendly_date(ns: int | None) -> str:
    import datetime as dt

    if not ns:
        return ""
    d = dt.datetime.fromtimestamp(ns / 1e9 if ns > 1e12 else ns)
    today = dt.date.today()
    if d.date() == today:
        return "Today, " + d.strftime("%H:%M")
    if (today - d.date()).days == 1:
        return "Yesterday"
    if (today - d.date()).days < 7:
        return d.strftime("%A")
    return d.strftime("%d %b %Y").lstrip("0")


def short_folder(path: str) -> str:
    """'C:\\Users\\me\\Documents\\Tax' -> 'Documents > Tax' (folders below the user profile)."""
    folder = os.path.dirname(path)
    home = os.path.expanduser("~")
    if folder.lower().startswith(home.lower() + os.sep):
        folder = folder[len(home) + 1:]
    return folder.replace(os.sep, " \u203a ")
