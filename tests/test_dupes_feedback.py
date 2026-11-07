"""Regression tests: similar-item grouping must not chain or crash, featureless pictures are not
'near-duplicates', hard-linked data is reclaimable only when every link goes, and restoring a
file teaches the scorer the suggestion was wrong. Synthetic temp files only."""

import os

import numpy as np
from conftest import file_id, index
from PIL import Image

from lfi import cleanup, db, library
from lfi.duplicates import _star_groups, exact_groups, similar_document_groups, similar_image_groups
from lfi.fileops import FileOps


def test_star_groups_never_chain():
    chain = {(i, i + 1): 1.0 for i in range(1, 12)}  # 1~2~3~...~12, ends share nothing
    groups = _star_groups(chain, higher_is_closer=False)
    for centre, members in groups:
        assert all((min(centre, m), max(centre, m)) in chain for m in members if m != centre)
        assert max(members) - min(members) <= 2  # every member sits next to its centre
    assert groups


def test_hub_shaped_similar_documents_do_not_crash(tmp_path, settings):
    """One original (highest id) is 0.95-similar to 9 revisions that are only ~0.90-similar to
    each other: the old code took the 8 lowest ids, found no pair among them and crashed."""
    root = tmp_path / "root"
    root.mkdir()
    base = "Project proposal for the community library renovation. " * 6
    for i in range(9):
        (root / f"proposal_rev{i}.txt").write_text(base + f" Revision {i}: changed section {i}.")
    (root / "zz_original.txt").write_text(base + " Original.")
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings)
    _, fstore = library.stores(conn)
    ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM files")}
    vecs = {ids["zz_original.txt"]: np.eye(fstore.dim, dtype=np.float32)[0]}
    for k in range(9):
        v = np.zeros(fstore.dim, dtype=np.float32)
        v[0], v[k + 1] = 0.95, np.sqrt(1 - 0.95**2)
        vecs[ids[f"proposal_rev{k}.txt"]] = v
    fstore.add(list(vecs), np.vstack(list(vecs.values())))
    groups = similar_document_groups(conn, 0.93)
    assert len(groups) == 1 and len(groups[0].files) == 8 and groups[0].evidence
    cleanup.analyze(conn, settings)  # used to raise ValueError on an empty min()


def test_featureless_pictures_are_not_near_duplicates(tmp_path, settings):
    root = tmp_path / "root"
    root.mkdir()
    for name, colour in (("black", (0, 0, 0)), ("white", (255, 255, 255)), ("red", (200, 0, 0)), ("navy", (0, 0, 90))):
        Image.new("RGB", (320, 240), colour).save(root / f"{name}.png")
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings)
    assert similar_image_groups(conn) == []


def test_hard_linked_copy_counts_only_when_every_link_goes(tmp_path, settings):
    root = tmp_path / "root"
    (root / "Docs").mkdir(parents=True)
    (root / "Downloads").mkdir()
    data = os.urandom(300_000)
    (root / "Docs" / "movie.bin").write_bytes(data)
    (root / "Downloads" / "movie (1).bin").write_bytes(data)
    os.link(root / "Downloads" / "movie (1).bin", root / "Downloads" / "movie - Copy.bin")
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings)
    (g,) = exact_groups(conn)
    assert g.keeper.name == "movie.bin"
    b, c = file_id(conn, "movie (1).bin"), file_id(conn, "movie - Copy.bin")
    assert g.reclaimable() == 300_000 and g.reclaimable({b}) == 0 and g.reclaimable({b, c}) == 300_000
    rep = cleanup.analyze(conn, settings)
    offered = {x.file_id for x in rep.baskets["exact_duplicates"]}
    assert offered == {b, c}
    assert rep.reclaimable("exact_duplicates") == 300_000
    assert rep.reclaimable("exact_duplicates", selected={b}) == 0


def test_restoring_a_file_lowers_later_scores(indexed, settings):
    conn, root, m, *_ = indexed
    rep = cleanup.analyze(conn, settings)
    cand = rep.baskets["exact_duplicates"][0]
    before = {c.name: c.score for c in rep.baskets["exact_duplicates"]}
    fo = FileOps(conn, settings, library.scope_policy(conn, settings))
    q = fo.quarantine([cand.file_id], "test", "exact_duplicates", {cand.file_id: cand.keeper_id})
    assert q.ok
    assert fo.restore([q.ok[0].op_id]).ok
    index(settings)
    after = {c.name: c.score for c in cleanup.analyze(conn, settings).baskets["exact_duplicates"]}
    same_ext = [n for n in after if os.path.splitext(n)[1] == os.path.splitext(cand.name)[1]]
    assert same_ext and all(after[n] < before[n] for n in same_ext if n in before)
