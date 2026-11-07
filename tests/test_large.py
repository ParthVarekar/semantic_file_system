"""Large-directory behaviour: bounded memory and fast incremental re-scan. Run with -m slow."""

import pytest
from conftest import index

from lfi import db, library
from lfi.sysinfo import rss_bytes


@pytest.mark.slow
def test_twenty_thousand_files_bounded(tmp_path, settings):
    root = tmp_path / "big"
    for i in range(20_000):
        d = root / f"d{i // 500:03d}"
        if i % 500 == 0:
            d.mkdir(parents=True)
        (d / f"f{i:05d}.txt").write_text(f"file {i} " + "content words here " * (i % 50))
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    _, before = rss_bytes()
    _, st = index(settings)
    _, after = rss_bytes()
    assert st.discovered == 20_000 and st.processed == 20_000 and st.errors == 0
    assert (after - before) < 600 * 2**20, f"peak memory grew by {(after - before) / 2**20:.0f} MB"
    _, st2 = index(settings)
    assert st2.processed == 0 and st2.unchanged == 20_000
    assert st2.elapsed_s < max(30.0, st.elapsed_s / 3)
