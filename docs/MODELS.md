# Models: download, caching, offline use, GPU

## Which model?

| Model | Size | Dim | Languages | CPU docs/s* | GPU docs/s* (DirectML) | When to use |
|---|---|---|---|---|---|---|
| **BAAI/bge-small-en-v1.5** (default) | 67 MB | 384 | English | 27 | 293 | Good quality/speed balance |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | 220 MB | 384 | 50+ | 40 | 394 | Non-English files |
| minishlab/potion-base-8M | 30 MB | 256 | English | 4,500 | n/a (static) | Slow PCs, very large collections |
| nomic-ai/nomic-embed-text-v1.5 | 520 MB | 768 | English | not measured | not measured | Higher quality, slower |
| Qwen/Qwen3-Embedding-0.6B-Q | 1.1 GB | 1024 | 100+ | 1.7 | fails (int8 under DirectML) | Small collections where quality matters most |

\* `bench/bench_embeddings.py`, 256 chunks of ~1,200 characters, Core 7 240H / RTX 5050 Laptop.
All five models scored 8/8 on the built-in retrieval sanity set. That set is small, so it shows
the models work, not which one is best. See [BENCHMARKS.md](BENCHMARKS.md).

## Image search model (find pictures by describing them)

| Pair (image tower + text tower) | Size | License | CPU images/s* | Vague-query recall@1 / @3** | Notes |
|---|---|---|---|---|---|
| **nomic-q**: nomic-embed-vision-v1.5 + nomic-embed-text-v1.5 (int8) (default) | ~230 MB | Apache-2.0 | 14.3 (4 threads) | 10/12 / 12/12 | ImageNet zero-shot 71.0% (model card); smallest; CPU only (int8) |
| clip-b32: OpenAI CLIP ViT-B/32 (fp32) | ~590 MB | MIT | 25.7 (all threads); 38.2 on DirectML | 11/12 / 12/12*** | Faster, uses the GPU, less accurate on ImageNet (~63%) |

\* 2000×1500 JPEGs including decode. \*\* `tests/test_images.py` / `bench/bench_vision.py` on synthetic
images (code screenshot, receipt, charts, page of text, spreadsheet, sunset, stars, meadow, QR
code, map, red dot) among random distractors. \*\*\* Raw cosine ranking in the benchmark.

* Download: `setup.ps1` does it by default (skip with `-NoImageModel`), or Settings → Image
  search → Download, or `lfi models download-vision nomic-q`.
* How it stays light: images are decoded and embedded in **one** separate below-normal-priority
  process using at most a quarter of the CPU threads (Settings → *CPU threads for images*), two
  images at a time (measured peak 293 MB; throughput is the same as larger batches, which used up
  to 555 MB). JPEGs are decoded at reduced scale (`draft` mode), so large photos are cheap. The
  app process only loads the small text tower (~130 MB) for queries. Turn it off entirely in
  Settings → Image search.
* Correctness detail: nomic's reference code layer-normalizes text vectors before
  L2-normalization for text-to-image retrieval. fastembed doesn't, so the app mean-centers and
  re-normalizes (mathematically identical; skipping it silently hurts accuracy).

## Download (one-time, needs internet)

Pick one:

* during setup: `scripts\setup.ps1` (default model) or `scripts\setup.ps1 -Model "<name>"`
* in the app: Settings → Embedding model → choose → **Download selected model...**
* command line: `.venv\Scripts\python -m lfi.cli models download "BAAI/bge-small-en-v1.5"`

Files go to `%LOCALAPPDATA%\LocalFileIntelligence\models` (Hugging Face cache layout). This is
*not* fastembed's default (`%TEMP%\fastembed_cache`), which disk-cleanup tools can wipe.
Override the location with the `LFI_MODELS` environment variable. `lfi models list` shows what
is cached.

## Offline guarantee

After the download, the app loads models with `local_files_only=True` and sets
`HF_HUB_OFFLINE=1`. If a model is missing, the app does **not** try to download it. It shows
"not downloaded" and continues with keyword + filename search. Files indexed without vectors
are embedded automatically at the next indexing run once a model is available (no re-reading).

For a machine with no internet at all: on a connected PC, run `scripts\package.ps1
-BundleModel` and copy `dist\LocalFileIntelligence\` (it includes `models\`). Or copy the
`models` folder into `%LOCALAPPDATA%\LocalFileIntelligence\` on the offline machine.

## GPU acceleration

* Install: `scripts\setup.ps1 -Gpu`. This swaps `onnxruntime` for `onnxruntime-directml`
  (works on NVIDIA, AMD and Intel GPUs with DirectX 12, with no CUDA toolkit needed).
* Settings → Device: *Automatic* (default) uses the GPU when available, *CPU only* forces CPU.
* The app falls back to CPU automatically if the GPU session can't be created **or** fails at
  inference time. The status bar shows the device in use and why it fell back.
* Batch size *0 = auto* uses 32 on GPU and 8 on CPU. On CPU, small batches were both faster
  and used about 3x less memory in our measurements.
* Single search queries are faster on CPU (7 ms) than on DirectML (50 ms launch overhead). Both
  are imperceptible in the UI.
* Re-running `uv sync` restores the CPU build; re-run `setup.ps1 -Gpu` afterwards.
* CUDA (`onnxruntime-gpu` + CUDA 12/13 + cuDNN) also works through ONNX Runtime but was not
  validated here (about 3 GB of extra wheels).

## Using Ollama instead

If you already run Ollama: Settings → Runtime → *Ollama running on this computer*, enter a
model you've pulled (e.g. `nomic-embed-text`, `qwen3-embedding`), and save. Only loopback URLs
are accepted.

## Changing models

Vectors from different models are never mixed: each model gets its own vector tables. When you
switch, the next indexing run recomputes vectors from the stored text without re-reading your
files, and the previous model's tables are dropped.
