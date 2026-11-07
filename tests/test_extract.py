import os
import zipfile

from lfi import synthetic
from lfi.extract import Budget, decode_bytes, kind_for, process_file


def run(path, **kw):
    return process_file({"path": str(path), "max_text_chars": 200_000, "max_pdf_pages": 50, **kw})


def test_pdf_docx_pptx_xlsx_html_ipynb(tmp_path):
    synthetic.make_pdf(tmp_path / "a.pdf", [["Eigenvalues of matrices"], ["Page two text"]], title="LA notes")
    synthetic.make_docx(tmp_path / "b.docx", ["Titration with phenolphthalein"], title="Lab")
    synthetic.make_pptx(tmp_path / "c.pptx", [("Periodic table", "Groups and periods")])
    synthetic.make_xlsx(tmp_path / "d.xlsx", [["Date", "Amount"], ["2024-01-01", 500]])
    (tmp_path / "e.html").write_text("<html><head><title>Recipe</title><script>evil()</script></head>"
                                     "<body><p>Paneer tikka</p></body></html>")
    (tmp_path / "f.ipynb").write_text('{"cells":[{"source":["# Forecasting sales"]}]}')
    r = run(tmp_path / "a.pdf")
    assert r["ok"] and "Eigenvalues" in r["text"] and "Page two" in r["text"] and r["title"] == "LA notes"
    assert r["pages"] == 2 and len(r["hash"]) == 64
    assert "phenolphthalein" in run(tmp_path / "b.docx")["text"]
    assert "Periodic table" in run(tmp_path / "c.pptx")["text"]
    assert "Amount" in run(tmp_path / "d.xlsx")["text"]
    h = run(tmp_path / "e.html")
    assert "Paneer" in h["text"] and "evil" not in h["text"] and h["title"] == "Recipe"
    assert "Forecasting" in run(tmp_path / "f.ipynb")["text"]


def test_zip_listing_is_inspection_only(tmp_path):
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("setup.exe", "MZ")
        zf.writestr("docs/readme.txt", "hello")
    r = run(z)
    assert "setup.exe" in r["text"] and r["members"] == 2
    assert not (tmp_path / "setup.exe").exists()  # nothing extracted to disk


def test_image_info_and_screenshot(tmp_path):
    synthetic.make_image(tmp_path / "p.jpg", 1, exif_camera="Canon EOS 80D")
    synthetic.make_image(tmp_path / "Screenshot 1.png", 2, (1920, 1080))
    p = run(tmp_path / "p.jpg")
    assert p["width"] == 640 and p["camera"].startswith("Canon") and len(p["phash"]) == 16
    assert not p["is_screenshot"]
    assert run(tmp_path / "Screenshot 1.png")["is_screenshot"]


def test_corrupt_and_unsupported_never_raise(tmp_path):
    (tmp_path / "bad.docx").write_bytes(b"PK\x03\x04garbage")
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-1.4 garbage")
    (tmp_path / "bad.jpg").write_bytes(b"\xff\xd8\xff garbage")
    (tmp_path / "blob.weird").write_bytes(os.urandom(500))
    for name in ("bad.docx", "bad.pdf", "bad.jpg"):
        r = run(tmp_path / name)
        assert r["status"] == "error" and r["error"]
        assert r.get("hash")  # metadata/hash still recorded for duplicate detection
    r = run(tmp_path / "blob.weird")
    assert r["ok"] and r["text"] == ""


def test_encrypted_pdf_detected(tmp_path):
    m = synthetic.build_corpus(tmp_path / "c", with_special=False)
    if m["encrypted"] is None:
        import pytest

        pytest.skip("pypdf not installed")
    assert run(m["encrypted"])["status"] == "encrypted"


def test_missing_file(tmp_path):
    r = run(tmp_path / "nope.txt")
    assert r["status"] == "missing" and not r["ok"]


def test_bounded_extraction(tmp_path):
    p = tmp_path / "big.txt"
    p.write_text("word " * 200_000)
    r = run(p, max_text_chars=5000)
    assert len(r["text"]) <= 5000 and r["truncated"]
    synthetic.make_pdf(tmp_path / "long.pdf", [[f"page {i}"] for i in range(30)])
    r = run(tmp_path / "long.pdf", max_pdf_pages=5)
    assert "page 4" in r["text"] and "page 5" not in r["text"] and r["truncated"]


def test_too_large_skips_text_but_hashes(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("hello " * 1000)
    r = run(p, max_extract_bytes=100)
    assert r["text"] == "" and r["hash"] and "too large" in r["note"]


def test_decode_and_kinds():
    assert decode_bytes("héllo".encode("utf-16")) == "héllo"
    assert decode_bytes("caf\xe9".encode("cp1252")) == "café"
    assert decode_bytes(b"\x00\x01binary") is None
    assert kind_for(".pdf") == "document" and kind_for(".exe") == "installer" and kind_for(".jpg") == "image"
    assert kind_for(".xlsx", "~$budget.xlsx") == "temporary"
    b = Budget(10)
    b.add("12345")
    b.add("67890abc")
    assert b.truncated and len(b.text()) <= 11


def test_changed_during_read_detected(tmp_path, monkeypatch):
    import lfi.extract as ex

    p = tmp_path / "grow.txt"
    p.write_text("start")
    real = ex.hash_file

    def mutate(path, *a, **k):
        h = real(path, *a, **k)
        with open(path, "a") as fh:
            fh.write(" appended while reading")
        return h

    monkeypatch.setattr(ex, "hash_file", mutate)
    r = run(p)
    assert r["status"] == "changed" and not r["ok"]


def test_cloud_placeholder_metadata_only(tmp_path):
    p = tmp_path / "doc.txt"
    p.write_text("secret content")
    r = run(p, metadata_only=True)
    assert r["status"] == "metadata_only" and r["text"] == ""
