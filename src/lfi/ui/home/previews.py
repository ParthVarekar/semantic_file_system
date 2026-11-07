"""Picture previews of documents, made in an isolated worker process.

* PDF: the first page, rendered small with PDFium (the library already used to read PDFs).
* Word / PowerPoint / Excel / OpenDocument: the thumbnail the saving program stored inside the
  file (``docProps/thumbnail.jpeg`` or ``Thumbnails/thumbnail.png``), if there is one.

This module runs inside ``lfi.pool`` workers (``TASK``), so a damaged or hostile file can crash
or hang only that worker, never the window: the pool replaces it and the card keeps its icon.
It imports no Qt and only ever reads files.
"""

from __future__ import annotations

import io
import os
import zipfile

TASK = "lfi.ui.home.previews:render"
PDF = {".pdf"}
OFFICE = {".docx", ".docm", ".pptx", ".pptm", ".xlsx", ".xlsm", ".odt", ".odp", ".ods"}
MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_EMBEDDED = 4 * 1024 * 1024
_EMBEDDED_NAMES = ("docprops/thumbnail.jpeg", "docprops/thumbnail.jpg", "docprops/thumbnail.png",
                   "thumbnails/thumbnail.png")


def can_preview(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in PDF or ext in OFFICE


def render(payload: dict) -> dict:
    """``{"path", "px"}`` -> ``{"ok": True, "data": <JPEG/PNG bytes>}`` or ``{"ok": False}``."""
    path, px = payload["path"], int(payload.get("px", 360))
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in PDF:
            return _pdf(path, px)
        if ext in OFFICE:
            return _embedded(path)
    except Exception as e:  # unreadable, encrypted, damaged: the card keeps its icon
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": False}


def _pdf(path: str, px: int) -> dict:
    if os.path.getsize(path) > MAX_PDF_BYTES:
        return {"ok": False}
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        if len(pdf) == 0:
            return {"ok": False}
        page = pdf[0]
        try:
            w, h = page.get_size()
            if w <= 0 or h <= 0 or w * h > 1e8:
                return {"ok": False}
            bitmap = page.render(scale=min(4.0, px / max(w, h)))
            try:
                img = bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        pdf.close()
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=84)
    return {"ok": True, "data": buf.getvalue()}


def _embedded(path: str) -> dict:
    with zipfile.ZipFile(path) as z:
        by_name = {i.filename.lower(): i for i in z.infolist()}
        for name in _EMBEDDED_NAMES:
            info = by_name.get(name)
            if info is not None and 0 < info.file_size <= MAX_EMBEDDED:
                data = z.read(info)
                return {"ok": not _blank(data), "data": data}
    return {"ok": False}


def _blank(data: bytes) -> bool:
    """True for a stored thumbnail that is one flat colour (templates often carry an empty page);
    the file's symbol says more than a white sheet."""
    from PIL import Image, ImageStat

    with Image.open(io.BytesIO(data)) as im:
        im.thumbnail((96, 96))
        return max(ImageStat.Stat(im.convert("L")).stddev) < 3.0
