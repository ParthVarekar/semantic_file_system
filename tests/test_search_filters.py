"""Regression tests: filters must not lose matches in larger libraries, the query parser must
never raise, privacy mode must keep the full-text index consistent, and Devanagari words must be
matched whole. Synthetic temp files only."""

import os
import time

import pytest
from conftest import index

from lfi import db, library
from lfi.search import SearchEngine, fts_query, parse_query


def _integrity_ok(conn) -> bool:
    try:
        conn.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES('integrity-check', 1)")
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def big_library(tmp_path_factory):
    """425 files: 400 recent .txt invoices crowd the top of every ranked list; 25 old .md
    invoices in an 'archive' folder are what the filters below must still find."""
    root = tmp_path_factory.mktemp("big") / "lib"
    (root / "current").mkdir(parents=True)
    (root / "archive" / "2019").mkdir(parents=True)
    for i in range(400):
        (root / "current" / f"invoice_{i:03d}.txt").write_text(f"invoice {i} total amount due {i * 7} rupees")
    old = time.mktime((2019, 6, 1, 12, 0, 0, 0, 0, -1))
    for i in range(25):
        p = root / "archive" / "2019" / f"old_invoice_{i:02d}.md"
        p.write_text(f"# Invoice {i}\nledger entry and amount due")
        os.utime(p, (old, old))
    return root


@pytest.fixture
def engine(big_library, settings, isolated_home):
    from lfi.embeddings import HashingBackend

    conn = db.connect()
    library.add_root(conn, str(big_library), validate=False)
    index(settings)
    return SearchEngine(conn, HashingBackend())


@pytest.mark.parametrize("q", ["ext:md", "invoice ext:md", "in:archive", "invoice in:archive", "before:2020",
                               "invoice before:2020", "amount kind:document ext:md"])
def test_filters_find_every_match_beyond_truncated_lists(engine, q):
    hits = engine.search(q, 100)
    assert len(hits) == 25 and all(h.name.startswith("old_invoice_") for h in hits)


def test_parse_query_is_robust():
    text, f = parse_query("ext:pdf ext:pdfx")
    assert text == "" and f.exts == [".pdf", ".pdfx"]
    assert parse_query("kind:photos")[1].kinds == ["image"] and parse_query("type:pdf")[1].exts == [".pdf"]
    for q in ("after:1970", "before:1969-12-31", "larger:9999999999999999999999999999GB", "after:2024-13-01",
              "before:today", "smaller:-5", 'in:"My Folder" notes', "meeting at 10:30 re: budget"):
        parse_query(q)  # never raises
    assert parse_query("after:1970")[1].after_ns is not None
    assert parse_query("before:today")[1].problems
    assert parse_query("meeting at 10:30")[0] == "meeting at 10:30"


def test_privacy_mode_keeps_full_text_index_consistent(corpus, settings):
    root, m = corpus
    settings.store_text = False
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings)
    assert _integrity_ok(conn)
    time.sleep(0.02)
    (root / "Misc" / "config.yaml").write_text("server:\n  port: 9090\n")
    os.remove(root / "Misc" / "data.json")
    index(settings)
    assert _integrity_ok(conn)
    assert conn.execute("SELECT count(*) FROM chunks WHERE text <> ''").fetchone()[0] == 0
    library.remove_root(conn, str(root))
    assert _integrity_ok(conn)


def test_database_damaged_by_old_triggers_is_repaired_on_open(indexed):
    conn, root, *_ = indexed
    # Reproduce the v1 sequence: hand-made FTS delete, blank text, then delete the rows.
    conn.execute("DROP TRIGGER chunks_au")
    rows = conn.execute("SELECT id, text FROM chunks WHERE text <> '' LIMIT 5").fetchall()
    for r in rows:
        conn.execute("INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)", (r[0], r[1]))
        conn.execute("UPDATE chunks SET text='' WHERE id=?", (r[0],))
    conn.execute(f"DELETE FROM chunks WHERE id IN ({','.join(str(r[0]) for r in rows)})")
    assert not _integrity_ok(conn)
    db.meta_set(conn, "fts_version", "1")
    conn.close()
    conn = db.connect()
    assert _integrity_ok(conn)
    assert conn.execute("SELECT count(*) FROM names_fts").fetchone()[0] == \
        conn.execute("SELECT count(*) FROM files").fetchone()[0]
    assert SearchEngine(conn).search("eigenvalues")


def test_devanagari_words_match_whole(tmp_path, settings):
    root = tmp_path / "root"
    root.mkdir()
    (root / "patra.txt").write_text("यह दस्तावेज़ हिन्दी में है। नमस्ते दुनिया", encoding="utf-8")
    (root / "other.txt").write_text("दस रुपये", encoding="utf-8")
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings, embed=False)
    assert fts_query("दस्तावेज़") == '"दस्तावेज़"*'
    se = SearchEngine(conn)
    assert [h.name for h in se.search("दस्तावेज़")] == ["patra.txt"]
    assert [h.name for h in se.search("नमस्ते")] == ["patra.txt"]
