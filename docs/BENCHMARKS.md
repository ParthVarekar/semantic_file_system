# Benchmarks

Machine: Windows 11 Home (26300), Intel Core 7 240H (10C/16T), 24 GB RAM, NVIDIA RTX 5050
Laptop 8 GB (driver 617.42), NVMe SSD. Python 3.12.13, ONNX Runtime 1.31.0 (CPU) /
onnxruntime-directml 1.24.4 (GPU), fastembed 0.9.1, sqlite-vec 0.1.9. Measured 2026-10-27.
Reproduce with the scripts in `bench/`. Numbers vary with hardware, background load and corpus.

## 1. Embedding models (`bench/bench_embeddings.py`, 256 chunks × ~1,200 chars, batch 32)

| Model | Device | Load s | Docs/s | Query ms (median) | Recall@3 sanity | Peak RSS MB |
|---|---|---|---|---|---|---|
| bge-small-en-v1.5 (int8) | CPU | 1.6 | 27.3 | 6.7 | 8/8 | 435 |
| bge-small-en-v1.5 (int8) | DirectML | 3.5 | **292.6** | 51.6 | 8/8 | 436 |
| paraphrase-multilingual-MiniLM-L12-v2 | CPU | 2.4 | 39.8 | 6.6 | 8/8 | 810 |
| paraphrase-multilingual-MiniLM-L12-v2 | DirectML | 2.6 | **394.4** | 46.2 | 8/8 | 819 |
| potion-base-8M (static) | CPU | 1.4 | **4,515.9** | <0.1 | 8/8 | 125 |
| Qwen3-Embedding-0.6B (int8) | CPU | 4.1 | 1.7 | 977 | 8/8 | 2,168 |
| Qwen3-Embedding-0.6B (int8) | DirectML | — | fails: Reshape node error in DML EP | — | — | — |

Takeaways: GPU gives about 10x for transformer models. For a *single query*, the CPU is faster
(about 7 ms vs about 50 ms of DirectML launch overhead). Qwen3 int8 isn't practical on CPU for
bulk indexing and doesn't run on DirectML. The app detects this and uses CPU. The 8-query
sanity set only shows that each model retrieves obvious matches; it isn't a quality ranking
(see MTEB for that).

## 2. End-to-end indexing (`bench/bench_index.py --docs 2000`)

Corpus: 2,000 generated files (35% TXT, 15% MD, 20% DOCX, 15% multi-page PDF, 7% XLSX, 8% JPEG),
23.2 MB, 6,030 text chunks. "Full index" = discover + hash + extract + chunk + embed (chunks and
one descriptor per file) + FTS + vectors.

| Configuration | Full index | Files/s | Incremental re-scan (no changes) | Hybrid search p50 / p95 | Index size | Peak RSS |
|---|---|---|---|---|---|---|
| bge-small, CPU, batch 32 (initial) | 476 s | 4.2 | 0.13 s | 28 / 32 ms | 27.6 MB (14 KB/file) | 1,273 MB |
| bge-small, CPU, batch 32 + length-sorted batches | 315 s | 6.4 | — | 30 ms | — | 1,220 MB |
| bge-small, CPU, **auto batch 8** (default)† | 268 s | 7.5 | 0.13 s | 33 / 35 ms | 14.3 KB/file | **437 MB** |
| bge-small, **DirectML GPU**, batch 32 | **40.4 s** | **49.5** | 0.12 s | 52 / 55 ms | 28.5 MB (14.6 KB/file) | 505 MB |
| potion-base-8M (static), CPU† | **7.0 s** | **284.7** | 0.15 s | 18 / 21 ms | 12.5 KB/file | 150 MB |

† Measured while a PyInstaller build was running on the same machine, so CPU numbers are
somewhat pessimistic.

Indexing pipeline memory without the model (hashing backend, 2,000 files): flat at
**85 MB working set**. All growth above that is the ONNX Runtime CPU arena, which scales with
batch × sequence length. That is why the auto batch size is 8 on CPU (435 MB peak, and faster)
and 32 on GPU.

Optimizations made because of these measurements:
1. **Length-sorted batching** (descriptors and chunks have very different lengths, and padding
   to the longest wasted about a third of CPU time): 476 s → 315 s.
2. **Device-aware batch size**: CPU batch 8 cut peak RAM from 1.2 GB to 0.43 GB and ran faster.

## 3. Image search (`bench/bench_vision.py`, synthetic images, vague descriptions)

12 images with recognizable content (named `IMG_0000.png`... so only pixels can match) + 30 random
distractors; one vague description per image. Speed: 64 JPEGs of 1600×1200 incl. decode.

| Pair | Device | Recall@1 | Recall@3 | Images/s | Query ms | Peak RSS (bench process) |
|---|---|---|---|---|---|---|
| nomic-q (int8, 230 MB) | CPU | 11/12 | 12/12 | 16.6 | 16 | 744 MB (both towers, batch 8) |
| clip-b32 (fp32, 590 MB) | CPU | 11/12 | 12/12 | 25.7 | 22 | 781 MB |
| clip-b32 | DirectML | 11/12 | 12/12 | 38.2 | 6 | 818 MB |
| nomic-q | DirectML env | (int8 → stays on CPU by design) | | 16.0 | 16 | 780 MB |

In-app background indexing (300 JPEGs of 2000×1500, nomic-q, 4 of 16 CPU threads, below-normal
priority): **14.3 images/s, vision worker peak 293 MB** (auto batch 2). Batch-size sweep at 4
threads: 13.3 / 13.4 / 13.0 / 13.0 img/s for batch 1 / 2 / 4 / 8, with peak 250 / 293 / 382 / 555 MB,
so batch 2 was chosen. End-to-end search check with the real model, fused ranking and 20
distractors: 10/12 at rank 1, 12/12 in the top 3 (the misses: "a big red dot" among random colored
ellipses, and a crude synthetic street map).

Calibration notes (from these runs, used in code): raw image-text cosine is tiny and
model-specific (nomic 0.03–0.10, CLIP 0.15–0.32), so search uses per-query z-scores against a
sample of your images. Auto-tags require z ≥ 3.3: every correct top tag scored ≥ 3.49 and the one
wrong tag (map → "chart") 3.04.

## 4. Scale (`tests/test_large.py`, `-m slow`)

20,000 small text files, hashing backend (no model): indexed with 0 errors, peak memory growth
under the 600 MB budget, and an unchanged re-scan processes 0 files. The whole test
(generation + two runs) takes 90 s.

## 5. Responsiveness (`tests/test_gui.py`)

While indexing 2,500 files, a 20 ms UI timer never saw a gap above 0.5 s (the GUI thread is
never blocked), and *Cancel* stopped the job within the 15 s limit with progress kept for the
next run.

## 6. Search quality on vague queries (`bench/search_eval/run_eval.py`)

75 vague, human-style queries ("that thing about my rent agreement", "notes from the machine
learning class", "pictures of the sky in the evening", plus 8 queries with no right answer) over a
synthetic library of 142 realistic documents and pictures (PDF/DOCX/PPTX/XLSX/TXT/MD, often with
uninformative names such as `scan_0012.pdf`; all people in it are fictional), graded by hand.
The queries are split into a tuning half and a **held-out** half; a change is kept only if the
held-out half improves. Real models (bge-small-en-v1.5, nomic-q), CPU.

```powershell
.venv\Scripts\python bench\search_eval\run_eval.py                                  # as shipped
.venv\Scripts\python bench\search_eval\run_eval.py --set search.SEMANTIC_MIN_Z=-1e9  # without the z rule
```

| Held-out half, scope *All* | MRR@10 | Recall@5 | Recall@10 | nDCG@10 | Results for a query with no answer |
|---|---|---|---|---|---|
| Relative semantic floor only (before) | 0.775 | 0.773 | 0.833 | 0.767 | 46.0 |
| **+ semantic-only hits must stand out from the library, z >= 2.0** (shipped) | **0.793** | **0.803** | **0.924** | **0.802** | **18.8** |
| + z >= 2.5 (stricter; rejected) | 0.805 | 0.833 | 0.918 | 0.809 | 16.5 |

z >= 2.5 looks slightly better in *All* but drops more right answers in *Documents* (recall@10
0.958 -> 0.937, vs 0.947 at 2.0), so 2.0 was kept. In *Documents* the shipped rule cuts results
for no-answer queries from 34.5 to 8.5. *Images* is unchanged (MRR 0.96): picture matches already
use a z-score. The rule is `SEMANTIC_MIN_Z` in `src/lfi/search.py` and is skipped for libraries
under 30 files. A relative fused-score cut-off was also tried and rejected (it lowered recall).

## 7. Memory of background processes

`import numpy` alone made each process commit about **500 MB** of private memory on this
16-thread laptop (OpenBLAS reserves a work buffer per CPU thread). Every process that imports
NumPy paid it: the app, the CLI, each extractor worker and the image worker. The package now caps
OpenBLAS at 4 threads (1 in worker processes and the quick search tray process) unless
`OPENBLAS_NUM_THREADS` is set.

| Process (idle) | Private memory before | After |
|---|---|---|
| `python -c "import numpy"` | 498 MB | 16 MB |
| Quick search in the tray (no model loaded) | 543 MB | 61 MB |

Quick search loads the search models on the first search and releases them after 10 idle minutes.

## 8. Using more of the machine: parallel model copies and the resource governor

Measured on this laptop (RTX 5050 8 GB, Core 7 240H, 24 GB RAM), real bge-small-en-v1.5 on
DirectML, 2,000 synthetic documents, fresh index each time.

**Where the time goes** (`bench/bench_stages.py`): reading files with 2 worker processes runs at
182 files/s, embedding at 52 files/s. Embedding on the GPU is the bottleneck, so adding readers
would not help; more embedding throughput does.

**More model copies, or bigger batches?** (`bench/bench_parallel.py`, `bench/bench_parallel_proc.py`)

| Approach | Speed-up | GPU memory |
|---|---|---|
| 1 model, batch 32 (before) | 1.00x | ~1.1 GB |
| 1 model, batch 128 | 1.19x | ~4.4 GB |
| 2 models in one process | crashes (native crash in ONNX Runtime's DirectML provider) | - |
| 2 models, one per process | 1.39x | ~2.0 GB |
| 3 models, one per process | 1.55x | ~2.9 GB |

**End to end** (`bench/bench_speed.py --docs 2000`):

| Indexing speed setting | Time | Throughput | Model copies |
|---|---|---|---|
| Gentle (one model) | 40.1 s | 49.9 files/s | 1 |
| **Balanced (default)** | **26.0 s** | **76.8 files/s (1.54x)** | 3 |
| Fast | 25.0 s | 80.1 files/s (1.61x) | 3 |

Images (`bench/bench_images_parallel.py`, 300 photos; the image model runs on the CPU): 13.4 ->
16.0 images/s. The governor stopped at 2 image workers because the CPU reached its reserve; that
is intended.

How it stays safe (`src/lfi/resources.py`, `src/lfi/pool.py`): every few seconds the governor reads
free RAM, total CPU load and free GPU memory (`nvidia-smi`; other GPUs are capped at 2 copies) and
each pool moves by at most one worker: it grows only with headroom on every resource and shrinks at
once when a reserve is breached (Balanced keeps 25% CPU, max(3 GB, 20%) RAM and 25% of GPU memory
free). Idle workers beyond the allowed size are stopped, so their memory really is returned.
Workers run at below-normal priority and are crash-isolated: a failed batch is embedded in the
main process instead, and after three failures parallel embedding switches off for that run.
Nothing is lost either way, because a file only counts as done once its vectors are stored.

## 9. Doing less work (2026-10-27)

Measured on this laptop's Desktop folder (1.18 million files in total; only directory listings and
image headers were read for these counts):

| Change | Effect |
|---|---|
| More tool-made folders excluded by default (`.venv*`, `site-packages`, `.next`, `.turbo`, `*.egg-info`, ...; see `config.EXCLUDE_ADDITIONS`) and build output (`dist`, `build`, `target`, `out`, `bin`, `obj`, ...) skipped **inside software projects only** | files to index **105,198 -> 76,289 (-27%)**; nothing a person made is excluded |
| Video frame dumps sampled for the image model: a folder of 200+ same-size images written less than a second apart each keeps 60 evenly spread frames for picture search; the rest stay findable by name and folder | one recording folder: **13,817 -> 2,663 images** through the model (-81%), about 12 CPU-minutes saved per copy |
| Tiny pictures (< 100 px, icons) skipped by the image model | small; avoids meaningless picture matches |
| SQLite: 32 MB page cache, in-memory temp tables, log trimmed to 64 MB, `wal_checkpoint(TRUNCATE)` + `optimize` after each run | keeps the write-ahead log from growing without bound (it had reached 100 MB on this machine) |

Camera folders are never sampled: photos are taken over hours or days, so they fail the
"written within seconds" test (covered by `tests/test_optimisations.py`). Existing settings get the
new default exclusions once; an exclusion the user removed is not added back.
