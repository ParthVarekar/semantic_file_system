# Architecture decision record and technology comparison

Status: accepted (2026-10-27). Target: Windows 11, Python 3.12, consumer laptop
(validated on Intel Core 7 240H, 24 GB RAM, RTX 5050 Laptop 8 GB).

## 1. Research summary: existing applications

I checked each candidate's repository with the GitHub API (license, last push, archived flag)
and read its README and source layout. "Coverage" is my estimate of how much of *this* product's
scope it already delivers.

| Project | License | Activity (last push) | What it does | Coverage | Verdict |
|---|---|---|---|---|---|
| **AI File Sorter** (hyperfield/ai-file-sorter) | AGPL-3.0 | active (2026-10) | C++/Qt app; per-file LLM (llama.cpp, Gemma) categorizes and renames, review dialog, "undo last run", learns from review | ~30% (categorize + review + undo) | Closest app. Not forked: its core is per-file LLM inference, with no content index, semantic search, duplicate detection, cleanup baskets, quarantine or crash-safe journal. Adding those means replacing most of the engine in C++, under AGPL. |
| **LlamaFS** (iyaja/llama-fs) | MIT | 2025-08 | Electron + FastAPI; LLM proposes a tree; Groq cloud by default, Ollama optional; AgentOps telemetry | ~15% | Cloud-first defaults and remote telemetry conflict with the privacy requirements. No undo, duplicates or search. |
| **Local-File-Organizer** (QiuYannnn) | MIT | 2024-10 (stale) | CLI; Nexa SDK LLaVA/Llama 3.2 summarize and copy files into a new tree; dry run | ~10% | Unmaintained; per-file generative LLM; copies rather than moves; no index. |
| **FileWizardAI** | none declared | 2025-01 | Angular + Python; OpenAI/Ollama | ~10% | No license, so it can't be reused. |
| **organize** (tfeldmann/organize) | MIT | active | Mature rule-based (YAML) organizer with simulation, conflict handling, duplicate filter | ~15% (rules engine) | Good ideas (simulate-first, `rename_new` conflict mode) were adopted. Not integrated: it's config-driven batch automation, not an interactive suggest/review engine. |
| **Czkawka / Krokiet** (qarmin/czkawka) | MIT (core, CLI); GPL-3.0 (Krokiet GUI) | active | Rust duplicate/similar-image/temp/empty/big-file finder with a CLI and JSON output | ~25% (cleanup scanners) | Evaluated as an external CLI. Not used as the engine: content hashes are needed in-process anyway (change detection, identity verification before every move), and invoking a second scanner would read every file twice and add a downloaded binary to the trust base. Its algorithms (size → prehash → hash; BK-tree/perceptual hashes) informed ours. |
| **fclones** (pkolaczk/fclones) | MIT | 2025-03 | Fast duplicate finder (size → prefix/suffix hash → full hash) | ~10% | Its staged hashing algorithm is implemented directly (≈40 lines). |
| **dupeGuru** | GPL-3.0 | sporadic | Python/Qt duplicate finder | ~10% | GPL, and duplicate finding alone is small. |
| **sist2**, **Recoll** | GPL-3.0 | active | Full-text desktop search (Elasticsearch / Xapian) | ~20% (search) | sist2 targets Linux/Docker; neither does semantic search or cleanup. |
| **Khoj** | AGPL-3.0 | active | Self-hosted "AI second brain" chat/RAG server | ~10% | Chat-centric server, not a file manager. |

**Conclusion:** no project covers 50–90% of the scope. The two closest, AI File Sorter
(categorization) and Czkawka (cleanup scanners), each cover a different quarter. Neither has the
shared foundation this product needs: a local content index, used by search, duplicates,
cleanup and organization alike, with verified, journaled, reversible file operations.

## 2. Decision

**Approach C (custom application), built mostly from existing libraries**, with
algorithms adopted from Approaches A/B projects where they are good (fclones staged hashing,
Czkawka-style perceptual-hash grouping, organize's simulate-first and no-overwrite conflict
naming, BERTopic-style c-TF-IDF cluster labels).

Custom code is limited to: orchestration (incremental/resumable indexing), the safety layer
(scope policy, journal, verification, recovery), the explainable scoring, the organization
planner, and the UI.

## 3. Component choices (alternatives considered)

| Concern | Chosen | License | Alternatives considered | Why |
|---|---|---|---|---|
| Embedding runtime | **fastembed 0.9** on **ONNX Runtime** | Apache-2.0 / MIT | sentence-transformers + PyTorch (~2.5 GB, CUDA wheels); Ollama (separate server); llama.cpp | ~550 MB environment; one API for many models; `local_files_only` gives a hard offline mode; GPU via ONNX Runtime execution providers. Ollama is offered as an optional backend. |
| Default model | **BAAI/bge-small-en-v1.5** (int8 ONNX, 384-d, 67 MB) | MIT | Qwen3-Embedding-0.6B, multilingual-MiniLM, nomic-embed v1.5, potion-base-8M, EmbeddingGemma (Gemma terms) | 8/8 on the retrieval sanity set; fast on CPU; 11x faster on GPU (see BENCHMARKS.md). Qwen3-0.6B is the quality option but ~1 s per query on CPU, and its int8 graph fails under DirectML (verified). |
| Image search | **nomic-embed-vision-v1.5 + nomic-embed-text-v1.5, int8** (fastembed) | Apache-2.0 | OpenAI CLIP ViT-B/32 (MIT, kept as option), SigLIP2-base (1.5 GB), jina-clip-v1 (890 MB), MobileCLIP (restrictive weights license), generating captions with a VLM (slow, heavy) | Smallest (230 MB) with the best published zero-shot accuracy of those tested (71.0% vs 68.3% for CLIP B/16). Same runtime as documents (no PyTorch). Measured 10–11/12 at rank 1 on vague synthetic queries; 293 MB worker at 4 threads. |
| GPU path | **onnxruntime-directml 1.24.4** (opt-in) | MIT | onnxruntime-gpu (CUDA 12/13 + cuDNN, ~3 GB of wheels); WinML | Works on any DX12 GPU, including this RTX 5050 (Blackwell), with no CUDA toolkit. Verified: `DmlExecutionProvider`, 293 vs 27 docs/s. Automatic CPU fallback at load *and* at inference time. |
| Vector index | **sqlite-vec 0.1.9** (`vec0`, cosine, exact) | MIT / Apache-2.0 | LanceDB, Chroma, Qdrant local, FAISS, usearch, hnswlib | Lives in the *same SQLite file* as metadata: one file to back up, clear or delete, transactional with metadata. Exact search is fast enough for personal corpora (p50 hybrid search 29 ms for 8k vectors). NumPy fallback if the extension can't load. |
| Lexical search | **SQLite FTS5** (BM25) | public domain | Tantivy, Whoosh, Xapian | Built in; external-content table avoids duplicating text. |
| Fusion | **Reciprocal Rank Fusion** (k=60) | algorithm | weighted score blending | Rank-based, so no score calibration across BM25 and cosine is needed. |
| Hashing | **BLAKE3** (`blake3` package) | CC0 / Apache-2.0 | SHA-256, xxHash | Cryptographic strength for "identical" claims, and very fast. |
| Perceptual hashing | **ImageHash** (pHash + dHash) | BSD-2 | Czkawka CLI, CLIP embeddings | In-process, deterministic, cheap; two independent hashes must agree. |
| PDF | **pypdfium2** (PDFium) | BSD-3 / Apache-2.0 | PyMuPDF (AGPL), pdfminer.six, Docling, Kreuzberg | Fast page-by-page bounded extraction and a permissive license. Docling and Kreuzberg are heavier and aimed at layout/RAG. |
| Office | **python-docx / python-pptx / openpyxl (read-only)** | MIT | MarkItDown (pulls pandas etc.), Kreuzberg, Apache Tika (JVM) | Small and well understood; openpyxl read-only mode streams. |
| Clustering / labels | **scikit-learn** Agglomerative (cosine) + TF-IDF | BSD-3 | BERTopic (+UMAP/HDBSCAN), HDBSCAN | Deterministic, no extra heavy dependencies; c-TF-IDF labelling reimplemented in ~25 lines. |
| Text similarity evidence | **RapidFuzz** | MIT | difflib, datasketch MinHash | Fast token-set ratio to show "x% text overlap" as evidence. |
| Recycle Bin | **Send2Trash** | BSD-3 | `SHFileOperation` via ctypes | Mature, cross-platform. Only used on already-quarantined files, after confirmation. |
| Watching | **watchdog** | Apache-2.0 | `ReadDirectoryChangesW` via ctypes | Mature. Events only mark the index dirty; a debounced incremental scan does the work. |
| UI | **PySide6-Essentials 6.12** (Qt) | LGPL-3.0 | Tauri, Electron, Flet, NiceGUI | Native Windows widgets, model/view tables, QThreadPool; one language; LGPL allows dynamic linking in a packaged app. |
| Process isolation | custom `IsolatedPool` (multiprocessing spawn) | — | ProcessPoolExecutor | The stdlib pool can't kill a single hung task, and one native crash breaks the whole pool. Parsers of untrusted files need both protections. |
| Quick search launcher | own small Qt window + Win32 `RegisterHotKey` (via ctypes), `QLocalServer` for single instance, `QSystemTrayIcon` | — | a Flow Launcher / PowerToys Run plugin; keyboard-hook libraries (`keyboard`, `pynput`); AutoHotkey | A plugin would require the user to install and keep another launcher. Keyboard hooks see every keystroke typed anywhere (a privacy problem, and often flagged by antivirus); `RegisterHotKey` only tells us about the one shortcut. |
| Everyday UI | two windows over one engine: a minimal one (`ui/home`) and the detailed Advanced tools (`ui/pages`) | — | one window with a "simple mode" toggle | A toggle still shows the complexity's shape (menus, half-hidden controls). Separate windows let the everyday one stay minimal; Advanced runs as its own process so the two visual styles never mix, and a cross-process index lock stops both from indexing at once. Design follows Apple's Human Interface Guidelines with Windows' own counterparts (Segoe UI Variable, Segoe Fluent Icons). |
| Using the hardware | a resource governor (`resources.py`) sizing every worker pool at run time; parallel GPU embedding with one model per process | — | fixed worker counts; bigger GPU batches; several models in one process | Measured: bigger batches give +19% for 4x the GPU memory, two DirectML sessions in one process crash, one model per process gives 1.55x for ~1 GB each. Reserves for Windows and other apps; shrinks when the machine gets busy (BENCHMARKS.md §8). |
| Multi-process safety | file lock (`msvcrt.locking`) around every batch of file operations | — | a lock row in SQLite; PID files | An OS file lock is released automatically if the holder crashes, so it can never be left stuck. |
| Packaging | **PyInstaller** one-folder | GPL-2.0 with bootloader exception | Nuitka, Briefcase | Mature hooks for Qt, ONNX Runtime and NumPy; the license exception permits distributing the output. |

## 4. System structure

```
           ┌──────────────────────────── UI (PySide6) ──────────────────────────────┐
           │ Dashboard  Folders  Search  Organize  Duplicates  Cleanup  Quarantine │
           │ Activity  Settings   ── QThreadPool workers (own SQLite connections) ─│
           └────────────┬───────────────┬───────────────┬──────────────┬───────────┘
                        │               │               │              │
                  indexer.py       search.py     cleanup.py /     organize.py
                (discover →        (FTS5 BM25 +  duplicates.py    (categorize →
                 process →          vec0 KNN,    (BLAKE3, pHash,  cluster → plan)
                 hash → embed)      RRF, filters) embeddings)          │
                   │    │                │               │             │
     pool.py ◄─────┘    └─► embeddings.py (fastembed/ONNX | Ollama | hashing)
   (isolated workers:            │
    extract.py, read-only)       │
                   └──────────► db.py / vectorstore.py / library.py ◄────┘
                             (one SQLite file: metadata, FTS5, vec0, prefs, journal)
                                          ▲
                    fileops.py ───────────┘  (writer lock → journal → verify → rename/copy → verify → done)
                    safety.py  (scope, protected dirs, reparse points, projects) used by all

  ui/home/     - the everyday window (Search, Clean Up, Organize, Recently Removed; monochrome
                 design tokens and the painted backdrop in theme.py; IndexService keeps the index up
                 to date by itself; thumbs.py loads card previews in the background, and document
                 previews (previews.py) are rendered in an isolated pool worker, read-only)
               apps.py (installed apps, ranked by size; the only thing that starts another program is the
               app's own uninstaller) lives in lfi/apps.py; shortcuts.py writes the Desktop shortcuts
  ui/pages/    - the Advanced tools window (every detail), started as its own process
  ui/quick.py  - separate tray process: global hotkey → small search window → search.py (read-only)
  cli.py       - the same engine from the command line
```

Boundaries: only `fileops.py` changes the filesystem, and only when the user approves.
`extract.py` and `indexer.py` never open files for writing. Embedding backends are
interchangeable through the `EmbeddingBackend` protocol. Vector tables are named per model,
so switching models never mixes vector spaces.

## 5. Key data flows

* **Indexing**: `scandir` (no link following) → upsert metadata keyed by case-folded path →
  changed if (size, mtime) differ → isolated worker hashes and extracts (bounded) → chunks +
  FTS → an embedding thread consumes processed files concurrently → `stage=2`. Cancellation
  is checked between directories and tasks. A cancelled walk never deletes rows.
* **Search**: query parsed for filters → the set of files passing the filters is computed first
  → FTS on content and names, KNN on chunk and file vectors, all restricted to that set (exact
  scoring for small sets) → RRF → explanations ("keyword match", "semantic similarity 0.76").
  A hit supported only by meaning (no keyword, name or picture evidence) must reach 85% of the
  best similarity *and* stand out from the library (z >= 2.0 against the query's similarity to a
  sample of all files); see BENCHMARKS.md section 6 for how this was tuned.
* **Quick search**: its own tray process. The hotkey (Win32 `RegisterHotKey`, delivered as
  `WM_HOTKEY`) shows the window; typing runs searches on one background thread that always serves
  the newest query only. Keyword results come first; the models load on the first search, the same
  query is answered again with meaning, and the models are released after 10 idle minutes.
* **Image search**: after reading files, image paths go to one isolated low-priority worker that
  decodes them at reduced scale and returns image vectors (`vec_images_<model>`). The main
  process derives conservative zero-shot tags (stored as a searchable chunk). At query time the
  text tower embeds the description; visual matches are scored as z-scores against a sample of
  the user's own images. In the Images view the score is visual z + 0.5 for a filename/folder
  match; in the mixed view visual matches join rank fusion as one more list.
* **Quarantine**: take the writer lock → validate (scope as written *and* as it is on disk right
  now - no junction or link between the selected folder and the file, resolved location not
  protected - cloud placeholder, project, pinned, size/mtime/file ID, optional hash, keeper re-hash)
  → journal `pending` (`synchronous=FULL`) → same-volume `MoveFileExW` without replace → verify →
  `done` + manifest line + audit line. Crash recovery runs only if it can take the same lock, so
  it never mistakes another window's batch for an interrupted one.

## 6. Security risks considered

See [PRIVACY_AND_THREAT_MODEL.md](PRIVACY_AND_THREAT_MODEL.md). In short: malicious documents
are parsed only in killable low-priority processes; nothing is executed; the app never needs
admin rights and warns if elevated; path checks use case-folded absolute paths with separator-
aware prefix tests, and anything that moves files re-checks the real, resolved location (aliases
such as `C:\PROGRA~1` or `\\?\C:\Windows`, and folders swapped for junctions after indexing, are
refused); links and junctions are never followed; the index is treated as sensitive.

## 7. License constraints

All runtime dependencies are permissive (MIT/BSD/Apache/CC0/MPL-2.0 for certifi and tqdm)
except Qt for Python (LGPL-3.0), which is dynamically linked and replaceable in the one-folder
build. AGPL candidates (PyMuPDF, AI File Sorter, Khoj) were avoided on purpose. Full
inventory: [THIRD_PARTY.md](THIRD_PARTY.md).
