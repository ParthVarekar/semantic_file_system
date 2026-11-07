# Third-party components, licenses and provenance

This application is original orchestration code (MIT) built on the components below. No
upstream source code was copied into this repository. Where an upstream *algorithm* was
re-implemented, it is credited in the "Algorithms" section and in the module docstring.

Versions are those resolved in `uv.lock` and verified installed on 2026-10-27 (license fields
read from each package's installed metadata).

## Runtime dependencies (direct)

| Package | Version | License | Used for |
|---|---|---|---|
| PySide6-Essentials / shiboken6 | 6.12.0 | LGPL-3.0-only (or GPL-2/3) | Desktop UI (dynamically linked) |
| fastembed | 0.9.1 | Apache-2.0 | Embedding model download/cache/inference API |
| onnxruntime | 1.31.0 | MIT | Model inference (CPU) |
| onnxruntime-directml (optional, `-Gpu`) | 1.24.4 | MIT | GPU inference via DirectML |
| sqlite-vec | 0.1.9 | MIT / Apache-2.0 | Vector KNN inside SQLite |
| blake3 | 1.0.11 | CC0-1.0 / Apache-2.0 | Content hashes |
| pypdfium2 (bundles PDFium) | 5.14.0 | BSD-3 / Apache-2.0 (+ PDFium BSD-3) | PDF text |
| python-docx | 1.2.0 | MIT | DOCX text |
| python-pptx | 1.0.2 | MIT | PPTX text |
| openpyxl | 3.1.5 | MIT | XLSX text (read-only mode) |
| Pillow | 13.0.0 | MIT-CMU | Image metadata/EXIF, thumbnails |
| ImageHash | 4.3.2 | BSD-2-Clause | pHash / dHash |
| scikit-learn | 1.9.1 | BSD-3-Clause | Agglomerative clustering, TF-IDF |
| RapidFuzz | 3.14.6 | MIT | Text-overlap evidence |
| Send2Trash | 2.1.0 | BSD-3-Clause | Recycle Bin for quarantined files |
| watchdog | 6.0.0 | Apache-2.0 | Optional folder watching |

## Notable transitive dependencies

numpy 2.5.4 (BSD-3 et al.), scipy 1.18.1 (BSD-3), PyWavelets 1.10.0 (MIT), tokenizers 0.23.3
(Apache-2.0), huggingface_hub 1.33.0 (Apache-2.0), py_rust_stemmers 0.1.8 (MIT, verified from
bundled LICENSE), loguru 0.7.3 (MIT), lxml 6.1.3 (BSD-3), protobuf 7.36.2 (BSD-3),
requests 2.34.2 (Apache-2.0), certifi (MPL-2.0), tqdm 4.70.1 (MPL-2.0 AND MIT), joblib,
threadpoolctl (BSD-3).

## Development / packaging only (not shipped as libraries)

pytest 9.1.1 (MIT), pytest-qt 4.5.0 (MIT), pypdf 6.21.2 (BSD-3; creates encrypted test PDFs),
PyInstaller 6.22.3 (GPL-2.0 with bootloader exception, which allows distributing the built
app under any license), pyinstaller-hooks-contrib (Apache-2.0 / GPL-2.0).

## Models (downloaded on request, never bundled in the repository)

| Model | License | Source |
|---|---|---|
| BAAI/bge-small-en-v1.5 (ONNX int8 by Qdrant) | MIT | huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | Apache-2.0 | huggingface.co/Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q |
| minishlab/potion-base-8M | MIT | huggingface.co/minishlab/potion-base-8M |
| nomic-ai/nomic-embed-text-v1.5 | Apache-2.0 | via fastembed registry |
| Qwen/Qwen3-Embedding-0.6B (ONNX int8) | Apache-2.0 | huggingface.co/Qdrant/Qwen3-Embedding-0.6B-onnx |
| nomic-ai/nomic-embed-vision-v1.5 (int8 ONNX) - image search | Apache-2.0 (verified on model card) | huggingface.co/nomic-ai/nomic-embed-vision-v1.5 |
| nomic-ai/nomic-embed-text-v1.5 (int8 ONNX) - image-search queries | Apache-2.0 (verified on model card) | huggingface.co/nomic-ai/nomic-embed-text-v1.5 |
| OpenAI CLIP ViT-B/32 (ONNX by Qdrant) - optional | MIT | huggingface.co/Qdrant/clip-ViT-B-32-vision, -text |

## Algorithms adopted (re-implemented, credited)

* Staged duplicate hashing (size → first/last-block prehash → full hash): fclones (MIT) and
  Czkawka (MIT) both use this approach. `extract.prehash_file`, `indexer._hash_size_collisions`.
* Perceptual-hash near-duplicate grouping (Czkawka uses a BK-tree; we use multi-index hashing
  with 8 bands, which gives exact recall for Hamming distance ≤ 7 by the pigeonhole principle).
* Reciprocal Rank Fusion: Cormack, Clarke & Büttcher, SIGIR 2009.
* Class-based TF-IDF cluster labels: the idea popularised by BERTopic (MIT), re-implemented
  with scikit-learn's `TfidfVectorizer`.
* Simulate-before-apply and "append a counter on name conflict": from `organize` (MIT).

## Evaluated but not used

AI File Sorter (AGPL-3.0), LlamaFS (MIT; cloud/telemetry defaults), Local-File-Organizer
(MIT; unmaintained), FileWizardAI (no license), Czkawka CLI (MIT; external scanner, see
ARCHITECTURE.md), dupeGuru (GPL-3.0), sist2 / Recoll (GPL-3.0), Khoj (AGPL-3.0), PyMuPDF
(AGPL-3.0), MarkItDown / Docling / Kreuzberg (MIT; heavier than needed), LanceDB / Chroma /
FAISS / usearch (vector stores not needed at personal scale), sentence-transformers + PyTorch
(size), Magika (Apache-2.0; extension + content sniffing was sufficient).
