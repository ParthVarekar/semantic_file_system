import os

from conftest import file_id, index

from lfi import db, library
from lfi.cleanup import analyze, mark_kept
from lfi.duplicates import COPY_NAME, exact_groups, similar_document_groups, similar_image_groups
from lfi.embeddings import HashingBackend
from lfi.search import SearchEngine, parse_query


# ---------------------------------------------------------------- search
def test_hybrid_search_and_filters(indexed):
    conn, *_ = indexed
    se = SearchEngine(conn, HashingBackend())
    hits = se.search("eigenvalues matrix homework")
    assert hits[0].name.startswith(("lecture07", "hw3"))
    assert any("keyword" in r for r in hits[0].reasons)
    assert all(h.name.endswith(".pdf") for h in se.search("eigenvalues ext:pdf"))
    imgs = se.search("kind:image")
    assert imgs and all(h.kind == "image" for h in imgs)
    big = se.search("larger:1MB")
    assert [h.name for h in big] == ["screen_recording_raw.mp4"]
    assert all("Finance" in h.path for h in se.search("bill in:finance"))
    assert se.search("zzzqqqxxx nonexistentterm") == [] or all(h.semantic is not None for h in
                                                              se.search("zzzqqqxxx nonexistentterm"))


def test_parse_query():
    text, f = parse_query('budget ext:pdf,docx after:2024-01 before:2025 larger:10MB in:"My Docs"')
    assert text == "budget" and f.exts == [".pdf", ".docx"] and f.min_size == 10 * 1024**2
    assert f.after_ns < f.before_ns and f.folder == "my docs"


def test_related_files(indexed):
    conn, *_ = indexed
    se = SearchEngine(conn, HashingBackend())
    rel = se.related(file_id(conn, "efficient_attention_transformers.pdf"), 5)
    assert rel[0].name == "efficient_attention_transformers (1).pdf"
    assert any("identical content" in r for r in rel[0].reasons)


def test_search_index_consistent_after_move(indexed, settings):
    from lfi.fileops import FileOps

    conn, root, *_ = indexed
    fo = FileOps(conn, settings, library.scope_policy(conn, settings))
    fid = file_id(conn, "notes.md")
    fo.move_files([(fid, str(root / "Organized" / "chem_notes.md"))], "t")
    se = SearchEngine(conn, HashingBackend())
    hit = next(h for h in se.search("alkanes reaction mechanisms") if h.file_id == fid)
    assert hit.path.endswith("chem_notes.md")
    assert any(h.file_id == fid for h in se.search("chem notes"))  # filename index updated


# ---------------------------------------------------------------- duplicates
def test_exact_groups_and_keeper(indexed):
    conn, root, m, *_ = indexed
    groups = {os.path.basename(g.keeper.path): g for g in exact_groups(conn)}
    assert set(groups) == {"lecture07_eigenvalues.docx", "efficient_attention_transformers.pdf",
                           "electricity_bill_march.pdf"}
    g = groups["lecture07_eigenvalues.docx"]
    assert any("organized folder" in r for r in g.keeper.keeper_reasons)
    assert g.reclaimable() == m["la"].stat().st_size


def test_pin_overrides_keeper(indexed):
    conn, root, m, *_ = indexed
    library.set_pref(conn, str(m["la_dup"]), pinned=1)
    g = next(g for g in exact_groups(conn) if "lecture07" in g.keeper.name)
    assert g.keeper.path == str(m["la_dup"]) and "pinned" in g.keeper.keeper_reasons[0]


def test_copy_name_pattern():
    for n in ("report (1)", "report - Copy", "Copy of report", "report_copy", "x - Copy (2)"):
        assert COPY_NAME.search(n), n
    for n in ("Lecture 7", "hw_3", "copyright", "photocopy notes"):
        assert not COPY_NAME.search(n), n


def test_near_duplicate_images(indexed):
    conn, root, m, *_ = indexed
    groups = similar_image_groups(conn, 6)
    assert len(groups) == 1
    names = {f.name for f in groups[0].files}
    assert names == {"beach_holiday.jpg", "beach_holiday_small.jpg"}
    assert groups[0].keeper.name == "beach_holiday.jpg"  # higher resolution, organized folder


def test_similar_documents_are_not_exact(corpus, settings):
    root, m = corpus
    base = "Quarterly report on regional sales performance and marketing spend. " * 30
    (root / "Misc" / "report_v1.txt").write_text(base + "Conclusion: increase budget.")
    (root / "Misc" / "report_v2.txt").write_text(base + "Conclusion: decrease budget slightly.")
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings)
    groups = similar_document_groups(conn, 0.9)
    g = next(g for g in groups if {f.name for f in g.files} >= {"report_v1.txt", "report_v2.txt"})
    assert any("compare manually" in e for e in g.evidence)
    assert not any("report_v1.txt" in x.keeper.name for x in exact_groups(conn))


# ---------------------------------------------------------------- cleanup
def test_baskets_and_explanations(indexed, settings):
    conn, root, m, *_ = indexed
    rep = analyze(conn, settings)
    b = {k: {c.name for c in v} for k, v in rep.baskets.items()}
    assert b["exact_duplicates"] == {"lecture07_eigenvalues - Copy.docx", "efficient_attention_transformers (1).pdf",
                                     "electricity_bill_march_copy.pdf"}
    assert b["similar_images"] == {"beach_holiday_small.jpg"}
    assert {"movie_download.crdownload", "~$budget.xlsx"} <= b["temporary"]
    assert b["empty"] == {"empty_note.txt"}
    assert b["old_installers"] == {"ZoomInstaller_setup.exe", "vlc-3.0.20-win64.msi"}
    assert b["large_files"] == {"screen_recording_raw.mp4"}
    for cands in rep.baskets.values():
        for c in cands:
            assert 0 <= c.score <= 100 and c.signals
            assert "not a probability" in c.explanation()
    dup = rep.baskets["exact_duplicates"][0]
    assert dup.confidence == "high" and dup.keeper_path


def test_reclaimable_counts_each_file_once(indexed, settings):
    conn, root, m, *_ = indexed
    rep = analyze(conn, settings)
    # The big .mp4 is also old: it must not be counted twice even if two baskets list it.
    rep.baskets["old_installers"].append(rep.baskets["large_files"][0])
    total = rep.reclaimable()
    uniq = {}
    for k, cands in rep.baskets.items():
        if k != "similar_documents":
            for c in cands:
                uniq[c.phys] = c.size
    assert total == sum(uniq.values())


def test_age_is_not_decisive_and_weights_apply(indexed, settings):
    conn, root, m, *_ = indexed
    rep = analyze(conn, settings)
    big = rep.baskets["large_files"][0]
    age = [s for s in big.signals if s.name.startswith("age")]
    assert age and age[0].points <= settings.weights.age_max
    settings.weights.exact_duplicate = 10
    low = analyze(conn, settings).baskets["exact_duplicates"][0]
    assert low.score < rep.baskets["exact_duplicates"][0].score


def test_feedback_lowers_scores_and_pins_hide(indexed, settings):
    conn, root, m, *_ = indexed
    rep = analyze(conn, settings)
    tmp = next(c for c in rep.baskets["temporary"] if c.name == "movie_download.crdownload")
    for _ in range(3):
        mark_kept(conn, [tmp])
    tmp2 = next(c for c in analyze(conn, settings).baskets["temporary"] if c.name == tmp.name)
    assert tmp2.score < tmp.score and any(s.name == "your past decisions" for s in tmp2.signals)
    library.set_pref(conn, str(m["empty"]), pinned=1)
    rep3 = analyze(conn, settings)
    assert not rep3.baskets["empty"]


def test_project_files_penalized(corpus, settings):
    root, m = corpus
    (root / "Projects" / "webapp" / "build.tmp").write_text("x")
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings)
    c = next(c for c in analyze(conn, settings).baskets["temporary"] if c.name == "build.tmp")
    assert any(s.name == "inside a project" and s.points < 0 for s in c.signals)
