# Learning guide

This guide is for learning how Local File Intelligence works: what each part does, the ideas
behind it, the real bugs that were found and fixed (and what they teach), and exercises to try.
No prior knowledge of search engines or Windows internals is assumed; basic Python is.

Start the app or its tests any time with **`startup.bat`** in the project folder (double-click it).

---

## 1. The big picture

The app answers four questions about your files, **without sending anything off the computer**:

1. *Where is that file?* - search by keywords or a vague description (also for pictures).
2. *What is duplicated or junk?* - exact duplicates, similar images, cleanup baskets.
3. *How should this be organized?* - suggested folders you review and approve.
4. *Can I undo it?* - every move is journaled, verified and reversible.

Everything rests on one **index**: a single SQLite file describing your files.

```
 your folders ──(read-only scan)──► indexer ──► index.sqlite3 ◄── search / duplicates / cleanup / organize
                                                    ▲                          │
                                                    └──── fileops (journal) ◄──┘  only after you approve
```

Two rules shape the whole design:
* **Reading and changing are separate.** Indexing never writes to your files. Only
  `fileops.py` changes the filesystem, only after approval, and only after re-checking the file.
* **Nothing is trusted blindly.** Files may be malformed (parsed in killable worker processes),
  may change after indexing (re-verified before moves), and paths may lie (checked as written
  *and* as resolved on disk).

---

## 2. Try it first

| Do this | What you learn |
|---|---|
| `startup.bat` → **6 Make a demo folder**, then in the app: Settings → Add Folder | The whole pipeline on safe synthetic files |
| Search `ext:pdf after:2024` and `notes from the chemistry class` | Filters vs. meaning-based search |
| Images scope: `a receipt` | Searching pictures by what they show |
| Clean Up → Duplicate files → Remove → **Undo** (or Recently Removed → Restore) | Reversible operations |
| `startup.bat` → **2 Quick search**, press **Alt+Shift+Space** | A separate, light launcher process |
| `.venv\Scripts\python -m lfi.cli search "tax" ` | The same engine without a GUI |

---

## 3. A guided tour of the code (read in this order)

| # | File | What to look for |
|---|---|---|
| 1 | `src/lfi/config.py` | Settings dataclass; where data lives (`%LOCALAPPDATA%\LocalFileIntelligence`); `enforce_offline_env` |
| 2 | `src/lfi/safety.py` | `ScopePolicy.check` (lexical) vs `check_physical` (filesystem as it is now); `real_path`; junction detection |
| 3 | `src/lfi/db.py` | The schema: `files`, `chunks`, FTS5 tables and their triggers, `operations` (the journal) |
| 4 | `src/lfi/pool.py` + `extract.py` | Parsing in separate processes that can be killed on crash or timeout |
| 5 | `src/lfi/indexer.py` | discover → process → embed (concurrently) → images → hash; `scan_gen` to detect deletions |
| 6 | `src/lfi/embeddings.py`, `vision.py` | Turning text and pictures into vectors with local ONNX models |
| 7 | `src/lfi/search.py` | Filters, BM25, vector search, rank fusion, the z-score rule |
| 8 | `src/lfi/duplicates.py`, `cleanup.py` | BLAKE3 groups, perceptual hashes, star grouping, review-priority scores |
| 9 | `src/lfi/organize.py` | Categories, clustering, labels, collision-free destination paths |
| 10 | `src/lfi/fileops.py` | Writer lock → validate → journal → move → verify; `recover()` |
| 11 | `src/lfi/ui/home/*` | The everyday window: `theme.py` (design tokens, icon font, the painted backdrop), `widgets.py` (frosted panels, the icon rail, the search orb, chips, switch, toasts with Undo, one painted view for cards or a list), `thumbs.py` + `previews.py` (previews loaded off the GUI thread; PDFs rendered in an isolated process), one file per screen, `service.py` (automatic indexing) |
| 11b | `src/lfi/ui/core.py`, `pages/*` | The Advanced tools: Qt workers, signals, keeping the window responsive |
| 12 | `src/lfi/ui/quick.py` | Global hotkey, tray icon, single instance, background search thread |
| 13 | `src/lfi/cli.py` | Same engine, exit codes, safe console output |

Tip: open a test next to each module (`tests/test_<area>.py`); tests are the clearest
description of what the code promises.

---

## 4. Key ideas, explained

### Incremental indexing
Each scan gets a generation number (`scan_gen`). Every file seen is stamped with it; after a
*complete* walk, rows with an older stamp are deleted files. Unchanged files (same size and
modification time) are skipped, so a re-scan of 20,000 files takes seconds. If a folder can't
be listed, its files are *not* treated as deleted - absence is only proven by a successful look.

### Keyword search: FTS5 and BM25
SQLite's FTS5 builds an *inverted index* (word → documents). BM25 ranks matches: rare words
count more than common ones, and repeated words have diminishing returns. The tokenizer decides
what a "word" is; here combining marks count as letters so Hindi words stay whole.

### Meaning search: embeddings and cosine similarity
A model maps text to a vector (384 numbers for bge-small) so that texts with similar meaning
point in similar directions. Similarity = cosine of the angle (dot product of normalized
vectors). "receipt for the laptop I bought" lands near an invoice that never uses those words.

### Combining rankings: Reciprocal Rank Fusion
Keyword scores and cosine scores aren't comparable, so instead each list contributes
`1 / (60 + rank)`. Documents ranked well by several lists rise to the top.

### "Does this result stand out?" - z-scores
A cosine of 0.55 means nothing on its own. The app compares the query against a sample of all
files: `z = (score - mean) / standard deviation`. A result supported only by meaning must have
z >= 2.0. This cut junk results for impossible queries from ~46 to ~19 (BENCHMARKS.md §6).

### Duplicates
* **Exact:** size → hash of the first bytes → full BLAKE3 hash (cheap tests first).
* **Hard links** are one file with two names; deleting one name frees nothing. Space counts only
  when *every* link goes (`nlink`).
* **Similar images:** perceptual hashes (pHash, dHash) change little when an image is resized or
  recompressed; compare by Hamming distance (number of differing bits).
* **Grouping without chaining:** if A~B and B~C, A and C may be unrelated. Groups are built
  around a centre that every member is directly similar to ("star grouping").

### Safe file operations: the write-ahead journal
1. Write "about to move X to Y" to the database and force it to disk.
2. Move (a rename that refuses to overwrite).
3. Verify, then mark "done".

After a crash, `recover()` compares the journal with the disk and finishes or rolls back each
step. A file lock makes sure only one process does this at a time.

### Responsive UI
Qt draws on one thread. Anything slow runs on a worker thread with its own database connection
and reports back through *signals*. Quick search keeps only the newest query, so fast typing
never builds a backlog.

### Designing for people, not for the engine
The everyday window shows four places and hides everything an ordinary user doesn't need
(scores, thresholds, indexing phases). Principles used, from Apple's Human Interface Guidelines:
one primary action per screen, large titles, plain language ("Remove", not "Quarantine"), safe
defaults (only certain categories are pre-selected), and **Undo instead of "Are you sure?"** for
anything reversible. Real confirmation dialogs are kept for the few things that are hard to undo.
Compare `ui/home/cleanup.py` with `ui/pages/cleanup.py` to see the same engine with two faces.

Two small ideas worth reading in the code. **One ordering for every list:** `Row` carries a `size` and a
`when`, and `RowModel` keeps the arrival order next to the chosen order, so "Largest first" is one
stable sort and "Suggested order" always comes back (`widgets.py`). **Rank where the data lives:** with an
empty search box, "Largest first" is `ORDER BY size DESC` in SQLite, not a sort of the 60 most
recent files, which would have hidden the biggest ones. And the Apps page shows how to be careful with an
action that affects other programs: it never deletes anything, it starts the app's own uninstaller after a
question whose default is Cancel, and it hands administrator rights to Windows' own prompt
(`ShellExecute`) instead of running this program as an administrator (`lfi/apps.py`).

The look is deliberately monochrome: colour is kept for warnings, so nothing competes with your
files' own previews. Every colour and symbol is painted from the current theme at paint time
(`theme.current()`), which is why switching light/dark needs no rebuilding. Motion is used only
where it explains something: the rail's highlight glides to the new place, the greeting folds away
when results need the room, previews fade in as they arrive, the preview panel slides in, and the
orb turns only while a search is running. Document previews show what a robust design looks like
in the GUI: rendering a PDF page runs PDFium on untrusted bytes, so it happens in an `IsolatedPool`
worker with a 10-second limit - a damaged file costs one card its preview, never the window.

### Global hotkeys
`RegisterHotKey` asks Windows to notify one window when a key combination is pressed anywhere
(`WM_HOTKEY`). Unlike a keyboard hook, the app never sees other keystrokes.

---

## 5. Lessons from real bugs (all fixed, all with regression tests)

| Bug | Lesson |
|---|---|
| A folder replaced by a **junction** after indexing let moves land outside the selected folders | Checking a path's *spelling* is not checking where it *leads*. Re-check the real filesystem right before acting (time-of-check vs time-of-use). |
| `C:\PROGRA~1`, `\\?\C:\Windows`, `\\localhost\C$` slipped past the protected-folder check | One location has many names. Canonicalize first, then compare. |
| Turning off "keep text" **corrupted the full-text index** | An FTS5 external-content index must be changed only through matching triggers; a mismatched "delete" corrupts it. |
| Filters like `ext:md` returned 0 of 25 matches in a big library | Filter *before* truncating to the top-N candidates, not after. |
| `lfi models download` silently never downloaded | A library read an environment variable once at import; changing the variable later did nothing. |
| Every process reserved ~500 MB just by importing NumPy | Measure memory. Thread pools have per-thread costs (OpenBLAS buffers). |
| Search results appeared in **reverse** order | A hidden default (Qt's sort indicator) silently re-sorted every table. |
| A tag added while embeddings were computed was never embedded | Classic race: re-check state at commit time, not only at read time. |
| Restoring a file *raised* similar files' cleanup scores | Event history must be replayed in order, so a later action can cancel an earlier one. |
| One file name with an invalid character stopped all indexing | One bad input must never stop the whole job. |

---

## 6. Exercises

1. **Read a test, break the code.** Comment out the `link_between` check in
   `ScopePolicy.check_physical` and run `pytest tests/test_paths_and_recovery.py`. Which tests
   fail and why? Restore it.
2. **Add a search filter** `name:` that matches only file names (hint: `parse_query` and
   `_allowed_ids` in `search.py`). Add a test in `tests/test_search_filters.py`.
3. **Tune search.** Run `.venv\Scripts\python bench\search_eval\run_eval.py`, then try
   `--set search.SEMANTIC_MIN_Z=1.5` and `--set search.RRF_K=20`. Does the held-out half agree
   with the tuning half? (If not, you are over-fitting.)
4. **New cleanup basket.** Add "screenshots older than a year" in `cleanup.py`, with a signal
   and an explanation. Check that the score stays within 0-100.
5. **Change the quick search look.** Edit `Theme` in `ui/quick.py`, then render it with the
   approach used in `tests/test_quick.py` (offscreen) and look at the picture.
6. **Crash on purpose.** In a test, raise an exception between journaling and moving
   (monkeypatch `_rename_noreplace`), then call `recover()`. What state does the journal end in?

---

## 7. Commands

```
startup.bat                       menu: app, quick search, tests, demo folder, shortcuts
.venv\Scripts\python -m pytest    fast test suite (about 2 minutes)
.venv\Scripts\python -m pytest -m "model or slow or not model"   everything (about 5 minutes)
.venv\Scripts\python bench\search_eval\run_eval.py               search-quality evaluation
```

Further reading: [ARCHITECTURE.md](ARCHITECTURE.md) (design decisions),
[PRIVACY_AND_THREAT_MODEL.md](PRIVACY_AND_THREAT_MODEL.md), [BENCHMARKS.md](BENCHMARKS.md),
[REFERENCES.md](REFERENCES.md).
