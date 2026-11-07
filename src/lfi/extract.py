"""Read-only, bounded content extraction. Runs inside isolated worker processes.

Every function here opens files for reading only. Each extractor stops after a character /
page / cell budget so huge documents never load fully into memory. Library choice per format:

* PDF   - pypdfium2 (PDFium, BSD-3/Apache-2.0); page-by-page text, encrypted PDFs reported.
* DOCX  - python-docx (MIT);  PPTX - python-pptx (MIT);  XLSX - openpyxl read-only (MIT).
* ODF / EPUB / ZIP listings / HTML / notebooks - Python standard library.
* Images - Pillow (MIT-CMU) for size/EXIF, ImageHash (BSD-2) for perceptual hashes.
"""

from __future__ import annotations

import json
import os
import re
import time
import zipfile
from html.parser import HTMLParser

KIND_BY_EXT: dict[str, str] = {}


def _reg(kind: str, exts: str) -> None:
    for e in exts.split():
        KIND_BY_EXT["." + e] = kind


_reg("document", "pdf docx doc odt rtf txt md markdown rst tex pptx ppt odp xlsx xlsm xls ods csv tsv "
                 "epub html htm xhtml mhtml log")
_reg("code", "py pyw ipynb js mjs cjs ts tsx jsx java kt kts scala c h cpp cc cxx hpp hh cs go rs rb "
             "php swift m mm r jl lua dart vue svelte css scss sass less sql sh bash zsh ps1 psm1 bat cmd "
             "pl pm hs ex exs erl clj groovy gradle cmake mk asm f90 v vhd sv")
_reg("data", "json jsonl ndjson xml yaml yml toml ini cfg conf env properties parquet sqlite db")
_reg("image", "jpg jpeg png gif bmp tif tiff webp heic heif avif ico svg raw cr2 nef arw dng psd")
_reg("audio", "mp3 wav flac aac m4a ogg opus wma aiff mid midi")
_reg("video", "mp4 mkv avi mov wmv webm flv m4v mpg mpeg 3gp ts")
_reg("archive", "zip rar 7z tar gz tgz bz2 xz zst lz cab")
_reg("installer", "exe msi msix msixbundle appx appxbundle dmg pkg deb rpm apk iso img")
_reg("temporary", "tmp temp crdownload part partial download bak old swp swo dmp")
KIND_BY_EXT[".ts"] = "code"  # TypeScript is far more common than MPEG-TS in user folders

TEXT_LIKE = {
    ".txt", ".md", ".markdown", ".rst", ".tex", ".csv", ".tsv", ".log", ".json", ".jsonl", ".ndjson",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties", ".env", ".svg",
}
HTML_LIKE = {".html", ".htm", ".xhtml"}
IMAGE_DECODABLE = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".ico"}

SCREEN_SIZES = {
    (1920, 1080), (1366, 768), (1536, 864), (1440, 900), (1280, 720), (1600, 900), (2560, 1440),
    (1280, 800), (1920, 1200), (2560, 1600), (2880, 1800), (3840, 2160), (1680, 1050), (2560, 1080),
    (3440, 1440), (1280, 1024), (2736, 1824), (2256, 1504), (3000, 2000), (1920, 1280),
}


def kind_for(ext: str, name: str = "") -> str:
    if name.startswith("~$") or name.startswith(".~lock."):
        return "temporary"  # Office / LibreOffice owner-lock files
    ext = ext.lower()
    if ext in KIND_BY_EXT:
        return KIND_BY_EXT[ext]
    if ext in TEXT_LIKE:
        return "document"
    return "other"


class Budget:
    """Accumulates text up to a character limit."""

    def __init__(self, max_chars: int):
        self.max_chars = max_chars
        self.parts: list[str] = []
        self.n = 0
        self.truncated = False

    @property
    def full(self) -> bool:
        return self.n >= self.max_chars

    def add(self, s: str | None) -> bool:
        if not s:
            return not self.full
        s = s.strip()
        if not s:
            return not self.full
        room = self.max_chars - self.n
        if room <= 0:
            self.truncated = True
            return False
        if len(s) > room:
            s = s[:room]
            self.truncated = True
        self.parts.append(s)
        self.n += len(s) + 1
        return not self.full

    def text(self) -> str:
        return "\n".join(self.parts)


def decode_bytes(data: bytes) -> str | None:
    """Decode text with BOM sniffing; returns None for binary-looking data."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_text_file(path: str, budget: Budget) -> None:
    limit = budget.max_chars * 4 + 4
    with open(path, "rb") as fh:
        data = fh.read(limit)
        more = fh.read(1)
    text = decode_bytes(data)
    if text is None:
        raise ValueError("binary content")
    budget.add(text)
    if more:
        budget.truncated = True


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self, budget: Budget):
        super().__init__(convert_charrefs=True)
        self.budget = budget
        self.depth_skip = 0
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.depth_skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.depth_skip:
            self.depth_skip -= 1

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()[:200] or None
        if not self.depth_skip:
            self.budget.add(data)


def html_to_text(raw: str, budget: Budget) -> str | None:
    p = _HTMLText(budget)
    try:
        p.feed(raw)
        p.close()
    except Exception:
        budget.add(re.sub(r"<[^>]+>", " ", raw))
    return p.title


def extract_pdf(path: str, budget: Budget, max_pages: int) -> dict:
    import pypdfium2 as pdfium

    info: dict = {}
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as e:
        msg = str(e)
        if "password" in msg.lower():
            info["status"] = "encrypted"
            return info
        raise
    try:
        try:
            meta = pdf.get_metadata_dict(skip_empty=True)
            info["title"] = (meta.get("Title") or "").strip()[:200] or None
        except Exception:
            pass
        n = len(pdf)
        info["pages"] = n
        for i in range(min(n, max_pages)):
            page = pdf[i]
            try:
                tp = page.get_textpage()
                try:
                    ok = budget.add(tp.get_text_bounded())
                finally:
                    tp.close()
            finally:
                page.close()
            if not ok:
                break
        if n > max_pages:
            budget.truncated = True
        if budget.n == 0 and n > 0:
            info["note"] = "no text layer (scanned PDF?)"
    finally:
        pdf.close()
    return info


def extract_docx(path: str, budget: Budget) -> dict:
    import docx

    d = docx.Document(path)
    info = {"title": (d.core_properties.title or "").strip()[:200] or None}
    for para in d.paragraphs:
        if not budget.add(para.text):
            return info
    for table in d.tables:
        for row in table.rows:
            cells = []
            seen = set()
            for c in row.cells:
                if id(c._tc) in seen:  # merged cells repeat
                    continue
                seen.add(id(c._tc))
                cells.append(c.text.strip())
            if not budget.add(" | ".join(x for x in cells if x)):
                return info
    return info


def extract_pptx(path: str, budget: Budget) -> dict:
    from pptx import Presentation

    prs = Presentation(path)
    info = {"title": (prs.core_properties.title or "").strip()[:200] or None, "slides": len(prs.slides)}
    for slide in prs.slides:
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                if not budget.add(shape.text_frame.text):
                    return info
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    if not budget.add(" | ".join(c.text for c in row.cells)):
                        return info
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            if not budget.add(slide.notes_slide.notes_text_frame.text):
                return info
    return info


def extract_xlsx(path: str, budget: Budget, max_cells: int = 200_000) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    info = {"sheets": len(wb.sheetnames)}
    cells = 0
    try:
        for ws in wb.worksheets:
            if not budget.add(f"Sheet: {ws.title}"):
                break
            for row in ws.iter_rows(values_only=True):
                vals = [str(v) for v in row if v is not None and str(v).strip()]
                cells += len(row)
                if vals and not budget.add(" | ".join(vals)):
                    return info
                if cells > max_cells:
                    budget.truncated = True
                    return info
    finally:
        wb.close()
    return info


def extract_zip_listing(path: str, budget: Budget, max_names: int = 300) -> dict:
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        encrypted = any(i.flag_bits & 0x1 for i in infos)
        names = [i.filename for i in infos[:max_names]]
    budget.add("Archive contents: " + ", ".join(names))
    if len(infos) > max_names:
        budget.truncated = True
    return {"members": len(infos), "encrypted": encrypted}


def extract_odf(path: str, budget: Budget) -> dict:
    with zipfile.ZipFile(path) as zf:
        with zf.open("content.xml") as fh:
            raw = fh.read(budget.max_chars * 8).decode("utf-8", errors="replace")
    raw = re.sub(r"<text:(p|h)[^>]*>", "\n", raw)
    budget.add(re.sub(r"<[^>]+>", " ", raw))
    return {}


def extract_epub(path: str, budget: Budget) -> dict:
    title = None
    with zipfile.ZipFile(path) as zf:
        for name in sorted(n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))):
            if budget.full:
                budget.truncated = True
                break
            with zf.open(name) as fh:
                raw = fh.read(2_000_000).decode("utf-8", errors="replace")
            t = html_to_text(raw, budget)
            title = title or t
    return {"title": title}


def extract_ipynb(path: str, budget: Budget) -> dict:
    with open(path, "rb") as fh:
        nb = json.loads(fh.read(budget.max_chars * 8))
    for cell in nb.get("cells", []):
        src = cell.get("source", "")
        if not budget.add("".join(src) if isinstance(src, list) else str(src)):
            break
    return {}


def image_info(path: str) -> dict:
    from PIL import ExifTags, Image

    info: dict = {}
    with Image.open(path) as im:
        info["width"], info["height"] = im.size
        fmt = (im.format or "").upper()
        try:
            exif = im.getexif()
        except Exception:
            exif = None
        if exif:
            dt = exif.get(0x9003) or exif.get(0x0132)  # DateTimeOriginal / DateTime
            try:
                sub = exif.get_ifd(ExifTags.IFD.Exif)
                dt = sub.get(0x9003) or dt
            except Exception:
                pass
            if dt:
                info["exif_time"] = str(dt)[:19]
            make, model = exif.get(0x010F), exif.get(0x0110)
            if make or model:
                info["camera"] = f"{make or ''} {model or ''}".strip()[:80]
        try:
            import imagehash

            im.draft("RGB", (256, 256))  # JPEG: decode at reduced scale, much faster
            frame = im.convert("RGB") if im.mode not in ("RGB", "L") else im
            info["phash"] = str(imagehash.phash(frame))
            info["dhash"] = str(imagehash.dhash(frame))
        except Exception as e:  # perceptual hash is best-effort
            info["hash_error"] = str(e)[:120]
        info["format"] = fmt
    return info


def looks_like_screenshot(name: str, info: dict) -> bool:
    low = name.lower()
    if re.search(r"screenshot|screen shot|screen_shot|snip|capture|scrnli|bildschirmfoto", low):
        return True
    if info.get("camera") or info.get("exif_time"):
        return False
    w, h = info.get("width"), info.get("height")
    return info.get("format") == "PNG" and ((w, h) in SCREEN_SIZES or (h, w) in SCREEN_SIZES)


def hash_file(path: str, chunk: int = 1 << 20) -> str:
    import blake3

    h = blake3.blake3()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def prehash_file(path: str, size: int, block: int = 65536) -> str:
    """Hash of size + first and last 64 KiB: cheap filter before full hashing (fclones-style)."""
    import blake3

    h = blake3.blake3()
    h.update(str(size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(block))
        if size > 2 * block:
            fh.seek(-block, os.SEEK_END)
            h.update(fh.read(block))
    return h.hexdigest()


def _stat_sig(path: str) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def process_file(task: dict) -> dict:
    """Worker entry point: hash + extract one file. Never raises; never writes."""
    path = task["path"]
    ext = os.path.splitext(path)[1].lower()
    out: dict = {"ok": True, "status": "ok", "text": "", "truncated": False}
    t0 = time.perf_counter()
    try:
        before = _stat_sig(path)
    except FileNotFoundError:
        return {"ok": False, "status": "missing", "error": "file disappeared"}
    except OSError as e:
        return {"ok": False, "status": "error", "error": f"stat failed: {e.strerror or e}"}
    size = before[0]
    try:
        st = os.stat(path)
        out.update(ino=str(st.st_ino), dev=str(st.st_dev), nlink=st.st_nlink)
        if task.get("metadata_only"):
            # Online-only cloud file: opening it for reading (even to hash it) makes OneDrive
            # download it. stat() is metadata and safe; nothing else is.
            out["status"] = "metadata_only"
            return out
        if task.get("do_hash", True) and size <= task.get("hash_max_bytes", 1 << 62):
            out["hash"] = hash_file(path)

        budget = Budget(task.get("max_text_chars", 200_000))
        info: dict = {}
        if kind_for(ext, os.path.basename(path)) == "temporary":
            info["note"] = "temporary file; content not extracted"
        elif size > task.get("max_extract_bytes", 1 << 62):
            info["note"] = "too large for text extraction"
        elif ext == ".pdf":
            info = extract_pdf(path, budget, task.get("max_pdf_pages", 150))
        elif ext in (".docx", ".docm"):
            info = extract_docx(path, budget)
        elif ext in (".pptx", ".pptm"):
            info = extract_pptx(path, budget)
        elif ext in (".xlsx", ".xlsm"):
            info = extract_xlsx(path, budget)
        elif ext in (".odt", ".odp", ".ods"):
            info = extract_odf(path, budget)
        elif ext == ".epub":
            info = extract_epub(path, budget)
        elif ext == ".ipynb":
            info = extract_ipynb(path, budget)
        elif ext in HTML_LIKE:
            with open(path, "rb") as fh:
                raw = decode_bytes(fh.read(budget.max_chars * 6)) or ""
            info = {"title": html_to_text(raw, budget)}
        elif ext == ".zip" or ext in (".jar", ".apk"):
            info = extract_zip_listing(path, budget)
        elif ext in IMAGE_DECODABLE:
            info = image_info(path)
            info["is_screenshot"] = looks_like_screenshot(os.path.basename(path), info)
        elif ext in TEXT_LIKE or kind_for(ext) == "code" or ext == "":
            try:
                read_text_file(path, budget)
            except ValueError:
                info["note"] = "binary content"
        if info.get("status") == "encrypted":
            out["status"] = "encrypted"
        out.update({k: v for k, v in info.items() if k != "status"})
        out["text"] = budget.text()
        out["truncated"] = budget.truncated
    except MemoryError:
        out.update(ok=False, status="error", error="out of memory while extracting")
    except PermissionError as e:
        out.update(ok=False, status="locked", error=f"permission denied / locked: {e.strerror or e}")
    except FileNotFoundError:
        return {"ok": False, "status": "missing", "error": "file disappeared"}
    except Exception as e:  # malformed, unsupported or partially readable
        out.update(ok=False, status="error", error=f"{type(e).__name__}: {str(e)[:200]}")
    try:
        after = _stat_sig(path)
        if after != before:
            out.update(ok=False, status="changed", error="file changed while being read")
    except OSError:
        out.update(ok=False, status="missing", error="file disappeared")
    out["elapsed"] = time.perf_counter() - t0
    return out


def hash_task(task: dict) -> dict:
    """Worker entry point for duplicate verification of large files."""
    path = task["path"]
    try:
        before = _stat_sig(path)
        if task.get("pre"):
            res = {"ok": True, "prehash": prehash_file(path, before[0])}
        else:
            res = {"ok": True, "hash": hash_file(path)}
        if _stat_sig(path) != before:
            return {"ok": False, "status": "changed", "error": "file changed while hashing"}
        return res
    except OSError as e:
        return {"ok": False, "status": "error", "error": str(e)[:200]}


TASKS = {"process": process_file, "hash": hash_task}

__all__ = ["process_file", "hash_task", "kind_for", "hash_file", "decode_bytes", "Budget"]
