"""Synthetic, reproducible test corpus. Never point tests at real personal files."""

from __future__ import annotations

import io
import json
import os
import random
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

DAY = 86400


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(path: Path, pages: list[list[str]], title: str | None = None) -> None:
    """Minimal valid PDF (Helvetica text) without any PDF-writing dependency."""
    objs: list[bytes] = []
    n_pages = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lines in enumerate(pages):
        content = "BT /F1 11 Tf 72 740 Td 14 TL " + " ".join(f"({_pdf_escape(ln)}) Tj T*" for ln in lines) + " ET"
        data = content.encode("latin-1", errors="replace")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * i} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
    info_ref = ""
    if title:
        objs.append(f"<< /Title ({_pdf_escape(title)}) >>".encode())
        info_ref = f" /Info {len(objs)} 0 R"
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R{info_ref} >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(out.getvalue())


def make_docx(path: Path, paragraphs: list[str], title: str | None = None) -> None:
    import docx

    d = docx.Document()
    if title:
        d.core_properties.title = title
        d.add_heading(title, 1)
    for p in paragraphs:
        d.add_paragraph(p)
    d.save(path)


def make_pptx(path: Path, slides: list[tuple[str, str]]) -> None:
    from pptx import Presentation

    prs = Presentation()
    for title, body in slides:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = title
        s.placeholders[1].text = body
    prs.save(path)


def make_xlsx(path: Path, rows: list[list]) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)


def make_image(path: Path, seed: int, size=(640, 480), fmt=None, quality=92, exif_camera: str | None = None) -> None:
    from PIL import Image, ImageDraw

    rnd = random.Random(seed)
    im = Image.new("RGB", size, (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    dr = ImageDraw.Draw(im)
    for _ in range(14):
        x0, y0 = rnd.randint(0, size[0]), rnd.randint(0, size[1])
        x1, y1 = x0 + rnd.randint(30, size[0] // 2), y0 + rnd.randint(30, size[1] // 2)
        dr.ellipse([x0, y0, x1, y1], fill=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    kw = {}
    if exif_camera:
        ex = Image.Exif()
        ex[0x010F], ex[0x0110] = exif_camera.split(" ", 1)
        ex[0x0132] = "2023:12:24 18:30:00"
        kw["exif"] = ex.tobytes()
    if (fmt or path.suffix.lstrip(".").upper()) in ("JPG", "JPEG"):
        im.save(path, "JPEG", quality=quality, **kw)
    else:
        im.save(path, **kw)


def _font(size: int):
    from PIL import ImageFont

    for name in ("consola.ttf", "arial.ttf", "DejaVuSans.ttf", "DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_semantic_images(folder: Path) -> dict[str, tuple[Path, str]]:
    """Images with recognizable content, each paired with a deliberately vague description.
    Used to sanity-check text-to-image retrieval without downloading any photos."""
    import math

    from PIL import Image, ImageDraw

    folder.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(5)
    out: dict[str, tuple[Path, str]] = {}

    def save(key, im, query, ext="png"):
        p = folder / f"IMG_{len(out):04d}.{ext}"  # uninformative names: only pixels can match
        im.save(p, quality=90) if ext == "jpg" else im.save(p)
        out[key] = (p, query)

    im = Image.new("RGB", (1280, 760), (30, 30, 36))
    d = ImageDraw.Draw(im)
    f = _font(22)
    code = ["def train(model, data):", "    for epoch in range(10):", "        loss = model.step(data)",
            "        print(f'epoch {epoch}: {loss:.3f}')", "", "class Dataset:", "    def __init__(self, path):",
            "        self.items = load(path)", "", "if __name__ == '__main__':", "    train(Model(), Dataset('x'))"]
    for i, line in enumerate(code):
        d.text((40, 30 + i * 36), line, font=f, fill=[(198, 120, 221), (152, 195, 121), (97, 175, 239)][i % 3])
    save("code", im, "screenshot of some programming code I wrote")

    im = Image.new("RGB", (420, 900), "white")
    d = ImageDraw.Draw(im)
    f = _font(20)
    lines = ["FRESH MART", "Store #142", "--------------------", "Milk 1L         2.49", "Bread           1.99",
             "Eggs x12        3.79", "Apples          4.20", "Coffee          7.95", "--------------------",
             "SUBTOTAL       20.42", "TAX             1.63", "TOTAL          22.05", "", "VISA ****1234",
             "THANK YOU!"]
    for i, line in enumerate(lines):
        d.text((30, 30 + i * 50), line, font=f, fill="black")
    save("receipt", im, "the paper receipt from when I bought groceries", "jpg")

    im = Image.new("RGB", (900, 600), "white")
    d = ImageDraw.Draw(im)
    d.line([(80, 40), (80, 540), (860, 540)], fill="black", width=3)
    for i, h in enumerate([220, 340, 160, 420, 300, 260]):
        d.rectangle([110 + i * 120, 540 - h, 190 + i * 120, 540], fill=(66, 133, 244))
    save("bar_chart", im, "a graph with vertical bars comparing values")

    im = Image.new("RGB", (700, 700), "white")
    d = ImageDraw.Draw(im)
    start = 0
    for frac, col in [(0.4, (219, 68, 55)), (0.25, (244, 180, 0)), (0.2, (15, 157, 88)), (0.15, (66, 133, 244))]:
        d.pieslice([100, 100, 600, 600], start, start + frac * 360, fill=col)
        start += frac * 360
    save("pie_chart", im, "round chart split into colored slices")

    im = Image.new("RGB", (850, 1100), "white")
    d = ImageDraw.Draw(im)
    f, fb = _font(18), _font(30)
    d.text((80, 70), "Chapter 3: Results and Discussion", font=fb, fill="black")
    words = "the results of the study indicate that participants who completed the program showed".split()
    for i in range(34):
        d.text((80, 140 + i * 26), " ".join(rnd.choice(words) for _ in range(11)), font=f, fill=(40, 40, 40))
    save("document", im, "a scanned page full of printed text")

    im = Image.new("RGB", (1000, 560), "white")
    d = ImageDraw.Draw(im)
    f = _font(18)
    for r in range(15):
        for c in range(7):
            x, y = 20 + c * 138, 20 + r * 34
            d.rectangle([x, y, x + 138, y + 34], outline=(180, 180, 180), fill=(232, 240, 254) if r == 0 else "white")
            d.text((x + 8, y + 8), ["Item", "Qty", "Price", "Total", "Date", "Region", "Owner"][c] if r == 0
                   else str(rnd.randint(1, 9999)), font=f, fill="black")
    save("spreadsheet", im, "spreadsheet with rows and columns of numbers")

    im = Image.new("RGB", (1024, 640))
    d = ImageDraw.Draw(im)
    for y in range(640):
        t = y / 400
        d.line([(0, y), (1024, y)], fill=(int(250 - 60 * t), int(140 - 90 * t), int(60 + 80 * t)) if y < 420
               else (25, 20, 30))
    d.ellipse([440, 330, 590, 480], fill=(255, 200, 80))
    d.rectangle([0, 420, 1024, 640], fill=(20, 18, 28))
    save("sunset", im, "evening sky when the sun goes down", "jpg")

    im = Image.new("RGB", (1024, 640), (5, 8, 25))
    d = ImageDraw.Draw(im)
    for _ in range(400):
        x, y, r = rnd.randint(0, 1023), rnd.randint(0, 639), rnd.choice([1, 1, 1, 2, 3])
        d.ellipse([x, y, x + r, y + r], fill=(255, 255, 240))
    save("stars", im, "dark night sky full of stars", "jpg")

    im = Image.new("RGB", (1024, 640))
    d = ImageDraw.Draw(im)
    for y in range(640):
        d.line([(0, y), (1024, y)], fill=(110 + y // 8, 170 + y // 10, 240) if y < 330 else
               (60 + rnd.randint(0, 25), 150 + rnd.randint(0, 40), 50))
    for _ in range(5):
        cx, cy = rnd.randint(50, 950), rnd.randint(40, 200)
        for k in range(4):
            d.ellipse([cx + k * 30, cy - (k % 2) * 15, cx + k * 30 + 70, cy + 45], fill="white")
    save("meadow", im, "green grass field under a blue sky with clouds", "jpg")

    im = Image.new("RGB", (580, 580), "white")
    d = ImageDraw.Draw(im)
    cell = 20
    for r in range(25):
        for c in range(25):
            if rnd.random() < 0.5:
                d.rectangle([40 + c * cell, 40 + r * cell, 40 + (c + 1) * cell, 40 + (r + 1) * cell], fill="black")
    for ox, oy in ((40, 40), (40 + 18 * cell, 40), (40, 40 + 18 * cell)):
        d.rectangle([ox, oy, ox + 7 * cell, oy + 7 * cell], fill="black")
        d.rectangle([ox + cell, oy + cell, ox + 6 * cell, oy + 6 * cell], fill="white")
        d.rectangle([ox + 2 * cell, oy + 2 * cell, ox + 5 * cell, oy + 5 * cell], fill="black")
    save("qr", im, "a qr code to scan")

    im = Image.new("RGB", (640, 640), "white")
    ImageDraw.Draw(im).ellipse([140, 140, 500, 500], fill=(220, 20, 30))
    save("red_circle", im, "a big red dot")

    im = Image.new("RGB", (640, 640), (245, 245, 245))
    d = ImageDraw.Draw(im)
    for i in range(12):  # simple bar-code-free "map": roads and a river
        d.line([(0, 50 * i + rnd.randint(-20, 20)), (640, 50 * i + rnd.randint(-20, 20))], fill=(255, 210, 120), width=6)
        d.line([(50 * i + rnd.randint(-20, 20), 0), (50 * i + rnd.randint(-20, 20), 640)], fill=(200, 200, 200), width=4)
    d.line([(0, 420)] + [(x, 420 + int(40 * math.sin(x / 60))) for x in range(0, 641, 20)], fill=(120, 170, 230), width=18)
    save("map", im, "street map with roads and a river")
    return out


def near_duplicate_image(src: Path, dst: Path, scale: float = 0.6, quality: int = 70) -> None:
    from PIL import Image

    with Image.open(src) as im:
        w, h = im.size
        im.convert("RGB").resize((int(w * scale), int(h * scale))).save(dst, "JPEG", quality=quality)


def set_age(path: Path, days: float) -> None:
    t = time.time() - days * DAY
    os.utime(path, (t, t))


LA = ["Lecture 7: Eigenvalues and eigenvectors of a square matrix.",
      "A matrix is diagonalizable if it has n linearly independent eigenvectors.",
      "The characteristic polynomial det(A - lambda I) = 0 gives the eigenvalues.",
      "Homework: compute the eigen decomposition for the given 3x3 symmetric matrix."]
CHEM = ["Experiment 4: acid-base titration with phenolphthalein indicator.",
        "25 mL of hydrochloric acid was titrated against standardized sodium hydroxide.",
        "The endpoint was reached at 23.4 mL; molarity calculated as 0.094 M."]
PAPER = ["Abstract. We propose an efficient attention mechanism for transformer neural networks.",
         "1 Introduction. Self-attention has quadratic complexity in sequence length.",
         "Experiments on machine translation benchmarks show improved BLEU scores.",
         "References: [1] Vaswani et al. Attention is all you need. arXiv:1706.03762"]
GNN = ["A survey of graph neural networks: message passing, graph convolution and pooling.",
       "We review spectral and spatial methods, node classification and link prediction.",
       "References: Kipf and Welling, semi-supervised classification with GCNs, ICLR 2017."]
BILL = ["Electricity bill - March. Consumer number 4471.", "Amount due: INR 1,240.50. Due date 15 March.",
        "Payment received via UPI. Thank you for paying your bill on time."]


def build_corpus(root: Path, with_special: bool = True) -> dict:
    """Create the corpus under ``root``. Returns a manifest of notable paths."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    m: dict = {}
    d = {k: root / k for k in ("Downloads", "College/Linear Algebra", "College/Chemistry", "Research",
                               "Finance", "Pictures", "Projects/webapp", "Projects/webapp/src", "Projects/pyscripts", "Misc")}
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)

    la = d["College/Linear Algebra"] / "lecture07_eigenvalues.docx"
    make_docx(la, LA, title="Linear Algebra Lecture 7")
    make_pdf(d["College/Linear Algebra"] / "hw3_solutions.pdf", [LA[2:] + ["Solution: lambda = 2, 3, 5."]])
    make_docx(d["College/Chemistry"] / "lab_report_titration.docx", CHEM, title="Titration lab report")
    (d["College/Chemistry"] / "notes.md").write_text("# Organic chemistry notes\n\nAlkanes, alkenes and "
                                                      "alkynes. Reaction mechanisms: SN1 and SN2.\n", encoding="utf-8")
    make_pptx(d["College/Chemistry"] / "periodic_table_slides.pptx",
              [("Periodic table", "Groups, periods and electron configuration"),
               ("Transition metals", "d-block elements and their oxidation states")])

    paper = d["Research"] / "efficient_attention_transformers.pdf"
    make_pdf(paper, [PAPER[:2], PAPER[2:]], title="Efficient Attention for Transformers")
    make_pdf(d["Research"] / "gnn_survey.pdf", [GNN])

    bill = d["Finance"] / "electricity_bill_march.pdf"
    make_pdf(bill, [BILL])
    make_xlsx(d["Finance"] / "bank_statement_2024.xlsx",
              [["Date", "Description", "Debit", "Credit"], ["2024-01-03", "Salary credit", None, 85000],
               ["2024-01-05", "Rent payment", 22000, None], ["2024-01-09", "Grocery store", 3400, None]])
    (d["Finance"] / "salary_slip.csv").write_text("month,gross,tax,net\nJan,85000,8500,76500\n", encoding="utf-8")

    beach = d["Pictures"] / "beach_holiday.jpg"
    make_image(beach, 1, (800, 600), exif_camera="Canon EOS 80D")
    make_image(d["Pictures"] / "mountain_sunset.png", 2, (640, 480))
    make_image(d["Pictures"] / "Screenshot 2024-05-01 101010.png", 3, (1920, 1080))

    (d["Projects/webapp"] / "package.json").write_text(json.dumps({"name": "webapp", "version": "1.0.0"}))
    (d["Projects/webapp"] / "README.md").write_text("# Web app\nA small React dashboard.\n")
    (d["Projects/webapp/src"] / "index.js").write_text("export function add(a, b) { return a + b; }\n")
    (d["Projects/webapp"] / ".git").mkdir(exist_ok=True)
    (d["Projects/webapp"] / ".git" / "config").write_text("[core]\n")
    (d["Projects/webapp"] / "node_modules" / "left-pad").mkdir(parents=True, exist_ok=True)
    (d["Projects/webapp"] / "node_modules" / "left-pad" / "index.js").write_text("module.exports = 1;\n")
    (d["Projects/pyscripts"] / "pyproject.toml").write_text("[project]\nname='pyscripts'\n")
    (d["Projects/pyscripts"] / "resize_images.py").write_text(
        "from PIL import Image\n\ndef resize(path, width):\n    im = Image.open(path)\n"
        "    im.thumbnail((width, width))\n    im.save(path)\n")

    (d["Misc"] / "data.json").write_text(json.dumps({"city": "Pune", "temperature": [21, 24, 30]}))
    (d["Misc"] / "page.html").write_text("<html><head><title>Recipe: paneer tikka</title><script>var x=1;</script>"
                                         "</head><body><h1>Paneer tikka</h1><p>Marinate paneer in yogurt and spices."
                                         "</p></body></html>", encoding="utf-8")
    (d["Misc"] / "config.yaml").write_text("server:\n  port: 8080\n  debug: true\n")
    (d["Misc"] / "notebook.ipynb").write_text(json.dumps({"cells": [{"cell_type": "markdown", "source": [
        "# Sales forecasting with linear regression"]}], "nbformat": 4}))
    (d["Misc"] / "résumé – नोट्स.txt").write_text("Curriculum vitae: software engineer, Python and SQL.",
                                                  encoding="utf-8")
    (d["Misc"] / "corrupt.docx").write_bytes(b"PK\x03\x04 this is not really a docx" + os.urandom(200))
    (d["Misc"] / "corrupt.pdf").write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Catalog >> garbage" + os.urandom(100))
    (d["Misc"] / "blob.bin").write_bytes(os.urandom(4096))
    m["corrupt"] = [d["Misc"] / "corrupt.docx", d["Misc"] / "corrupt.pdf"]
    try:
        from pypdf import PdfReader, PdfWriter

        w = PdfWriter(clone_from=PdfReader(str(bill)))
        w.encrypt("secret", algorithm="RC4-128")
        with open(d["Misc"] / "encrypted_statement.pdf", "wb") as fh:
            w.write(fh)
        m["encrypted"] = d["Misc"] / "encrypted_statement.pdf"
    except Exception:
        m["encrypted"] = None

    # --- clutter in Downloads -------------------------------------------------------------
    dl = d["Downloads"]
    rnd = random.Random(42)
    inst = dl / "ZoomInstaller_setup.exe"
    inst.write_bytes(b"MZ" + bytes(rnd.getrandbits(8) for _ in range(300_000)))
    set_age(inst, 200)
    msi = dl / "vlc-3.0.20-win64.msi"
    msi.write_bytes(b"\xd0\xcf\x11\xe0" + bytes(rnd.getrandbits(8) for _ in range(150_000)))
    set_age(msi, 400)
    shutil.copy2(paper, dl / "efficient_attention_transformers (1).pdf")
    shutil.copy2(la, dl / "lecture07_eigenvalues - Copy.docx")
    shutil.copy2(bill, d["Finance"] / "electricity_bill_march_copy.pdf")
    near_duplicate_image(beach, dl / "beach_holiday_small.jpg")
    with zipfile.ZipFile(dl / "course_materials.zip", "w") as zf:
        zf.writestr("syllabus.txt", "Linear algebra syllabus: vectors, matrices, eigenvalues")
        zf.writestr("week1/intro.pdf", "fake")
    (dl / "movie_download.crdownload").write_bytes(os.urandom(50_000))
    (dl / "~$budget.xlsx").write_bytes(b"\x0bowner" + bytes(150))
    (dl / "empty_note.txt").write_bytes(b"")
    big = dl / "screen_recording_raw.mp4"
    with open(big, "wb") as fh:
        fh.write(os.urandom(64 * 1024))
        fh.truncate(3 * 1024 * 1024)  # 3 MiB; tests lower the large-file threshold
    set_age(big, 500)

    m.update(
        installer=inst, msi=msi, paper=paper, paper_dup=dl / "efficient_attention_transformers (1).pdf",
        la=la, la_dup=dl / "lecture07_eigenvalues - Copy.docx", bill=bill,
        bill_dup=d["Finance"] / "electricity_bill_march_copy.pdf", beach=beach,
        beach_near=dl / "beach_holiday_small.jpg", temp=dl / "movie_download.crdownload",
        lockfile=dl / "~$budget.xlsx", empty=dl / "empty_note.txt", big=big,
        project=d["Projects/webapp"], project_file=d["Projects/webapp/src"] / "index.js",
    )

    if with_special:
        deep = root / "Deep"
        p = deep
        for i in range(12):
            p = p / f"nested_folder_level_{i:02d}_with_long_name"
        os.makedirs("\\\\?\\" + str(p) if sys.platform == "win32" else p, exist_ok=True)
        lp = p / "deep_note_about_quantum_computing.txt"
        with open("\\\\?\\" + str(lp) if sys.platform == "win32" else lp, "w", encoding="utf-8") as fh:
            fh.write("Qubits, superposition and entanglement in quantum computing.")
        m["long_path"] = lp
        if sys.platform == "win32":
            target = root / "College"
            junction = root / "JunctionToCollege"
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)], capture_output=True)
            m["junction"] = junction if r.returncode == 0 else None
            ini = d["Pictures"] / "desktop.ini"
            ini.write_text("[.ShellClassInfo]\n")
            subprocess.run(["attrib", "+s", "+h", str(ini)], capture_output=True)
            m["system_file"] = ini
    return m
