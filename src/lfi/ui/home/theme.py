"""Design tokens for the everyday window: colours, type, icons, the backdrop and one stylesheet.

A quiet monochrome look: deep blacks and charcoal (or soft white and pearl in the light variant),
off-white text, frosted panels floating over an atmospheric backdrop (a starfield and the rim of a
planet, drawn - no image files). Colour is reserved for the one thing that needs it: warnings.
Built from the Windows counterparts of Apple's building blocks: Segoe UI Variable for type and
Segoe Fluent Icons for symbols. The app sets its own palette so the Windows accent colour (often
red or green) never recolours it.
"""

from __future__ import annotations

import ctypes
import math
import random
import sys
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QIcon, QLinearGradient, QPainter, QPalette, QPen,
                           QPixmap, QRadialGradient)

TEXT_FONT = ["Segoe UI Variable Text", "Segoe UI"]
DISPLAY_FONT = ["Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI"]
ICON_FONT = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]

# Segoe Fluent Icons code points (shared with Segoe MDL2 Assets).
GLYPH = {
    "search": "\ue721", "broom": "\uea99", "folder": "\ue8b7", "trash": "\ue74d", "settings": "\ue713",
    "document": "\ue8a5", "photo": "\ue91b", "copy": "\ue8c8", "package": "\ue7b8", "clock": "\ue823",
    "large": "\ue740", "empty": "\ue8e5", "check": "\ue73e", "done": "\ue930", "sync": "\ue895",
    "add": "\ue710", "remove": "\ue738", "back": "\ue72b", "chevron": "\ue76c", "close": "\ue711",
    "info": "\ue946", "warning": "\ue7ba", "open": "\ue8a7", "undo": "\ue7a7", "download": "\ue896",
    "shield": "\uea18", "sparkle": "\uf4a5", "keyboard": "\ue765", "sort": "\ue8cb", "restore": "\ue777",
    "forward": "\ue72a", "video": "\ue714", "audio": "\ue8d6", "code": "\ue943", "slides": "\ue786",
    "grid": "\ue8a9", "list": "\ue8fd", "link": "\ue71b", "reveal": "\ue838", "calendar": "\ue787",
    "table": "\ue80a", "archive": "\ue7b8",
}


@dataclass(frozen=True)
class Palette:
    dark: bool
    window: str
    sidebar: str
    card: str
    hover: str
    field: str
    text: str
    text2: str
    text3: str
    separator: str
    accent: str
    accent_hover: str
    accent_soft: str
    danger: str
    success: str
    selection: str
    on_accent: str = "#FFFFFF"  # text and icons drawn on top of the accent
    glass: str = "rgba(18,18,20,0.66)"  # frosted panels
    glass_strong: str = "rgba(26,26,29,0.86)"  # the search field, the preview panel
    glass_edge_top: str = "rgba(255,255,255,0.14)"  # panel outline: brighter at the top, like a lit edge
    glass_edge_bottom: str = "rgba(255,255,255,0.04)"
    tile: str = "rgba(255,255,255,0.035)"  # a thumbnail's empty well


LIGHT = Palette(False, window="#ECECEF", sidebar="#E4E4E8", card="rgba(255,255,255,0.72)",
                hover="rgba(0,0,0,0.045)", field="rgba(255,255,255,0.80)", text="#111113", text2="#66666D",
                text3="#A2A2A9", separator="rgba(0,0,0,0.08)", accent="#111113", accent_hover="#000000",
                accent_soft="rgba(0,0,0,0.06)", danger="#D2382D", success="#111113",
                selection="rgba(0,0,0,0.075)", on_accent="#FFFFFF", glass="rgba(255,255,255,0.58)",
                glass_strong="rgba(255,255,255,0.86)", glass_edge_top="rgba(255,255,255,0.95)",
                glass_edge_bottom="rgba(0,0,0,0.07)", tile="rgba(0,0,0,0.035)")
DARK = Palette(True, window="#0B0B0C", sidebar="#111113", card="rgba(22,22,25,0.72)",
               hover="rgba(255,255,255,0.06)", field="rgba(255,255,255,0.05)", text="#F2F2F3", text2="#9C9CA3",
               text3="#5F5F66", separator="rgba(255,255,255,0.08)", accent="#F2F2F3", accent_hover="#FFFFFF",
               accent_soft="rgba(255,255,255,0.10)", danger="#FF6B61", success="#F2F2F3",
               selection="rgba(255,255,255,0.11)", on_accent="#0B0B0C", glass="rgba(17,17,19,0.76)")

_current: Palette = DARK


def current() -> Palette:
    return _current


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def resolve(appearance: str) -> Palette:
    if appearance == "dark":
        return DARK
    if appearance == "light":
        return LIGHT
    return DARK if system_is_dark() else LIGHT


def qcolor(css: str) -> QColor:
    if css.startswith("rgba("):
        r, g, b, a = (float(x) for x in css[5:-1].split(","))
        return QColor(int(r), int(g), int(b), int(a * 255))
    return QColor(css)


def font(size: float = 10.0, weight: QFont.Weight = QFont.Weight.Normal, display: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(DISPLAY_FONT if display else TEXT_FONT)
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def glyph(name: str, size: int = 16, color: str | None = None) -> QIcon:
    """An icon drawn from the Fluent icon font (crisp at any scale, no image files)."""
    ratio = 2.0
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setFont(icon_font(size))
    p.setPen(qcolor(color or _current.text2))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, GLYPH.get(name, name))
    p.end()
    return QIcon(pm)


def icon_font(size: float) -> QFont:
    f = QFont()
    f.setFamilies(ICON_FONT)
    f.setPixelSize(max(1, int(size * 0.92)))
    return f


SLIDES = {".ppt", ".pptx", ".odp", ".key"}
SHEETS = {".xls", ".xlsx", ".csv", ".ods", ".tsv"}


def kind_glyph(kind: str, ext: str = "") -> str:
    """The symbol for a file: by type, with presentations and spreadsheets told apart."""
    if ext in SLIDES:
        return "slides"
    if ext in SHEETS:
        return "table"
    return {"image": "photo", "video": "video", "audio": "audio", "code": "code", "archive": "archive",
            "installer": "package", "data": "table"}.get(kind, "document")


# ------------------------------------------------------------------------------------ backdrop
def paint_backdrop(p: QPainter, w: int, h: int, t: Palette) -> None:
    """The atmosphere behind everything: a deep sky with a soft light from the upper right, a
    sparse field of stars, and the rim of a planet rising along the bottom. Deterministic, so it
    looks the same every time, and drawn once per window size (the window caches it)."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    sky = QLinearGradient(0, 0, 0, h)
    if t.dark:
        sky.setColorAt(0, QColor("#111114"))
        sky.setColorAt(0.55, QColor("#09090B"))
        sky.setColorAt(1, QColor("#050506"))
    else:
        sky.setColorAt(0, QColor("#F6F6F8"))
        sky.setColorAt(0.6, QColor("#ECECEF"))
        sky.setColorAt(1, QColor("#E2E2E6"))
    p.fillRect(0, 0, w, h, sky)

    glow = QRadialGradient(QPointF(w * 0.82, -h * 0.12), max(w, h) * 0.85)
    glow.setColorAt(0, QColor(255, 255, 255, 30 if t.dark else 200))
    glow.setColorAt(0.45, QColor(255, 255, 255, 9 if t.dark else 70))
    glow.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(0, 0, w, h, glow)

    if t.dark:  # stars: denser and brighter towards the top, none on the planet
        rnd = random.Random(1729)
        p.setPen(Qt.PenStyle.NoPen)
        for _ in range(int(w * h / 5200)):
            x, y = rnd.random() * w, (rnd.random() ** 1.6) * h * 0.78
            a = int(rnd.uniform(18, 120) * (1.0 - y / (h * 0.95)))
            r = rnd.choice((0.45, 0.55, 0.7, 0.7, 0.9, 1.15))
            p.setBrush(QColor(255, 255, 255, max(8, a)))
            p.drawEllipse(QPointF(x, y), r, r)

    # the planet: a huge circle whose top edge crosses the lower part of the window
    radius = max(w * 1.25, 900.0)
    cx, cy = w * 0.62, h * 0.9 + radius
    halo = QRadialGradient(QPointF(cx, cy), radius * 1.10)
    edge = radius / (radius * 1.10)
    if t.dark:
        halo.setColorAt(edge - 0.004, QColor(0, 0, 0, 0))
        halo.setColorAt(edge, QColor(255, 255, 255, 46))
        halo.setColorAt(edge + 0.012, QColor(200, 205, 220, 18))
        halo.setColorAt(1, QColor(255, 255, 255, 0))
    else:
        halo.setColorAt(edge - 0.004, QColor(255, 255, 255, 0))
        halo.setColorAt(edge, QColor(255, 255, 255, 220))
        halo.setColorAt(edge + 0.015, QColor(255, 255, 255, 90))
        halo.setColorAt(1, QColor(255, 255, 255, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(halo)
    p.drawEllipse(QPointF(cx, cy), radius * 1.10, radius * 1.10)
    body = QRadialGradient(QPointF(cx + radius * 0.25, cy - radius * 0.98), radius * 1.15)
    if t.dark:
        body.setColorAt(0, QColor("#2A2A2F"))
        body.setColorAt(0.18, QColor("#121215"))
        body.setColorAt(1, QColor("#050506"))
    else:
        body.setColorAt(0, QColor("#E9E9ED"))
        body.setColorAt(0.2, QColor("#DCDCE1"))
        body.setColorAt(1, QColor("#CFCFD5"))
    p.setBrush(body)
    p.drawEllipse(QPointF(cx, cy), radius, radius)
    rim = QColor(255, 255, 255, 96 if t.dark else 255)
    pen_w = 1.2
    p.setBrush(Qt.BrushStyle.NoBrush)
    for i in range(3):  # a thin lit rim that fades towards the sides
        c = QColor(rim)
        c.setAlpha(int(rim.alpha() * (1.0, 0.35, 0.12)[i]))
        p.setPen(QPen(c, pen_w + i * 2.2))
        span = (32, 50, 70)[i]
        top_angle = 90 + math.degrees(math.asin(max(-1.0, min(1.0, (cx - w * 0.62) / radius))))
        p.drawArc(QRectF(cx - radius, cy - radius, radius * 2, radius * 2), int((top_angle - span / 2) * 16),
                  int(span * 16))


def apply(app, appearance: str = "system") -> Palette:
    """Set palette and stylesheet for the whole application."""
    global _current
    t = _current = resolve(appearance)
    pal = QPalette()
    for role, css in ((QPalette.ColorRole.Window, t.window), (QPalette.ColorRole.Base, t.window),
                      (QPalette.ColorRole.AlternateBase, t.window), (QPalette.ColorRole.Text, t.text),
                      (QPalette.ColorRole.WindowText, t.text), (QPalette.ColorRole.ButtonText, t.text),
                      (QPalette.ColorRole.Button, t.sidebar), (QPalette.ColorRole.Highlight, t.accent),
                      (QPalette.ColorRole.HighlightedText, t.on_accent), (QPalette.ColorRole.PlaceholderText, t.text3),
                      (QPalette.ColorRole.ToolTipBase, t.sidebar), (QPalette.ColorRole.ToolTipText, t.text),
                      (QPalette.ColorRole.Link, t.text)):
        pal.setColor(role, qcolor(css))
    app.setPalette(pal)
    app.setFont(font(10))
    app.setStyleSheet(stylesheet(t))
    return t


def stylesheet(t: Palette) -> str:
    chip_bg = "rgba(255,255,255,0.045)" if t.dark else "rgba(255,255,255,0.65)"
    return f"""
QWidget {{ font-family: "Segoe UI Variable Text", "Segoe UI"; color: {t.text}; }}
QMainWindow, QDialog {{ background: {t.window}; }}
#content, #pages {{ background: transparent; }}
QToolTip {{ background: {t.sidebar}; color: {t.text}; border: 1px solid {t.separator}; padding: 5px 9px;
           border-radius: 8px; }}

#status {{ color: {t.text2}; font-size: 9pt; }}
#largeTitle {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 24pt; font-weight: 300;
              letter-spacing: -0.3px; }}
#title {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 14pt; font-weight: 500; }}
#greeting {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 30pt; font-weight: 250;
            letter-spacing: -0.5px; }}
#eyebrow {{ color: {t.text2}; font-size: 8pt; font-weight: 600; letter-spacing: 2.2px; }}
#headline {{ font-size: 10.5pt; font-weight: 600; }}
#secondary {{ color: {t.text2}; }}
#caption {{ color: {t.text2}; font-size: 9pt; }}
#hero {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 34pt; font-weight: 250; color: {t.text}; }}
#previewText {{ color: {t.text2}; font-size: 9.5pt; line-height: 140%; }}
#metaKey {{ color: {t.text3}; font-size: 8.5pt; }}
#metaValue {{ color: {t.text}; font-size: 9pt; }}

#cardButton {{ background: {t.glass}; border: 1px solid {t.separator}; border-radius: 18px; text-align: left; }}
#cardButton:hover {{ background: {t.glass_strong}; border-color: {t.glass_edge_top}; }}
#cardButton:disabled {{ background: {t.glass}; }}
#banner {{ background: {t.glass}; border: 1px solid {t.separator}; border-radius: 14px; }}
#previewBox {{ background: {t.tile}; border-radius: 14px; padding: 12px; color: {t.text2}; }}

QPushButton {{ border: none; border-radius: 10px; padding: 7px 16px; font-size: 10pt; }}
QPushButton#primary {{ background: {t.accent}; color: {t.on_accent}; font-weight: 600; border-radius: 15px;
                      padding: 7px 20px; min-height: 18px; }}
QPushButton#primary:hover {{ background: {t.accent_hover}; }}
QPushButton#primary:pressed {{ background: {t.text2}; }}
QPushButton#primary:disabled {{ background: {t.separator}; color: {t.text3}; }}
QPushButton#secondary {{ background: {chip_bg}; color: {t.text}; border: 1px solid {t.separator}; border-radius: 15px;
                        padding: 7px 18px; min-height: 18px; }}
QPushButton#secondary:hover {{ background: {t.selection}; }}
QPushButton#secondary:disabled {{ color: {t.text3}; }}
QPushButton#plain {{ background: transparent; color: {t.text}; padding: 6px 10px; }}
QPushButton#plain:hover {{ background: {t.hover}; }}
QPushButton#plain:disabled {{ color: {t.text3}; }}
QPushButton#danger {{ background: transparent; color: {t.danger}; border: 1px solid {t.separator}; border-radius: 15px;
                     min-height: 18px; }}
QPushButton#danger:hover {{ background: {t.hover}; }}
QPushButton#segment {{ background: transparent; color: {t.text2}; border-radius: 8px; padding: 5px 14px; }}
QPushButton#segment:hover {{ color: {t.text}; }}
QPushButton#segment:checked {{ background: {t.selection}; color: {t.text}; }}
#segmented {{ background: {t.tile}; border: 1px solid {t.separator}; border-radius: 11px; }}
QPushButton#chip {{ background: {chip_bg}; color: {t.text2}; border: 1px solid {t.separator}; border-radius: 14px;
                   padding: 4px 15px; font-size: 9.5pt; min-height: 20px; }}
QPushButton#chip:hover {{ color: {t.text}; border-color: {t.glass_edge_top}; }}
QPushButton#chip:checked {{ background: {t.accent}; color: {t.on_accent}; border-color: {t.accent}; font-weight: 600; }}
QPushButton#iconButton {{ background: {chip_bg}; border: 1px solid {t.separator}; border-radius: 15px; padding: 0; }}
QPushButton#iconButton:hover {{ background: {t.selection}; border-color: {t.glass_edge_top}; }}
QPushButton#iconButton:checked {{ background: {t.selection}; border-color: {t.glass_edge_top}; }}
QPushButton#iconButton:disabled {{ background: transparent; }}
QPushButton#go {{ background: {t.accent}; border-radius: 19px; padding: 0; }}
QPushButton#go:hover {{ background: {t.accent_hover}; }}
QPushButton#go:pressed {{ background: {t.text2}; }}

QLineEdit {{ background: {t.field}; border: 1px solid {t.separator}; border-radius: 10px; padding: 6px 10px;
            selection-background-color: {t.accent}; selection-color: {t.on_accent}; }}
QLineEdit:focus {{ border: 1px solid {t.text2}; }}
QLineEdit#search, QLineEdit#search:focus {{ background: transparent; border: none; font-size: 12.5pt; padding: 0 4px; }}

QListView, QListWidget, QTreeWidget {{ background: transparent; border: none; outline: none; }}
QListView::item:selected, QListWidget::item:selected {{ background: {t.selection}; color: {t.text}; border-radius: 10px; }}
QListView::item:hover, QListWidget::item:hover {{ background: {t.hover}; border-radius: 10px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ width: 8px; background: transparent; margin: 4px 1px; }}
QScrollBar::handle:vertical {{ background: {t.separator}; border-radius: 3px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {t.text3}; }}
QScrollBar:horizontal {{ height: 0px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}

QCheckBox {{ spacing: 8px; }}
QProgressBar {{ background: {t.separator}; border: none; border-radius: 2px; max-height: 4px; }}
QProgressBar::chunk {{ background: {t.text}; border-radius: 2px; }}
QMenu {{ background: {t.sidebar}; border: 1px solid {t.separator}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t.selection}; color: {t.text}; }}
#toast {{ background: transparent; }}
#toastText {{ color: {t.text}; }}
#toastAction {{ color: {t.text}; background: {t.selection}; font-weight: 600; padding: 4px 12px; border-radius: 12px;
                min-height: 18px; }}
#toastAction:hover {{ background: {t.accent_soft}; }}
"""


def dark_title_bar(widget, dark: bool) -> None:
    """Match the native title bar to the theme (Windows 10 2004+ / 11): dark mode plus, on
    Windows 11, a caption colour that blends into the backdrop."""
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.c_void_p(int(widget.winId()))
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        caption = ctypes.c_uint(0x00110F0F if dark else 0x00F8F6F6)  # COLORREF is 0x00BBGGRR
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
    except Exception:
        pass
