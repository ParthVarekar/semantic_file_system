# Verified references

Checked 2026-10-27: repository metadata via the GitHub REST API, package metadata via the
PyPI JSON API, documentation pages read directly.

## Projects evaluated
* AI File Sorter: https://github.com/hyperfield/ai-file-sorter (AGPL-3.0)
* LlamaFS: https://github.com/iyaja/llama-fs (MIT)
* Local-File-Organizer: https://github.com/QiuYannnn/Local-File-Organizer (MIT)
* FileWizardAI: https://github.com/AIxHunter/FileWizardAI (no license)
* organize: https://github.com/tfeldmann/organize (MIT)
* Czkawka / Krokiet: https://github.com/qarmin/czkawka (README: core and CLI MIT; Krokiet GPL-3.0)
* fclones: https://github.com/pkolaczk/fclones (MIT)
* dupeGuru: https://github.com/arsenetar/dupeguru (GPL-3.0)
* sist2: https://github.com/sist2app/sist2 (GPL-3.0)
* Khoj: https://github.com/khoj-ai/khoj (AGPL-3.0)
* Kreuzberg: https://github.com/kreuzberg-dev/kreuzberg (MIT)
* MarkItDown: https://github.com/microsoft/markitdown (MIT)
* Magika: https://github.com/google/magika (Apache-2.0)

## Components used
* fastembed: https://github.com/qdrant/fastembed, supported models: https://qdrant.tech/documentation/embeddings/fastembed/
* ONNX Runtime: https://onnxruntime.ai/ , execution providers: https://onnxruntime.ai/docs/execution-providers/
* sqlite-vec: https://github.com/asg017/sqlite-vec (releases: v0.1.7 "sqlite-vec is back", v0.1.9 on PyPI)
* SQLite FTS5: https://www.sqlite.org/fts5.html
* BLAKE3: https://github.com/BLAKE3-team/BLAKE3 , Python binding https://pypi.org/project/blake3/
* pypdfium2: https://github.com/pypdfium2-team/pypdfium2
* python-docx / python-pptx: https://python-docx.readthedocs.io , https://python-pptx.readthedocs.io
* openpyxl read-only mode: https://openpyxl.readthedocs.io/en/stable/optimized.html
* ImageHash: https://github.com/JohannesBuchner/imagehash
* scikit-learn AgglomerativeClustering: https://scikit-learn.org/stable/modules/clustering.html
* RapidFuzz: https://github.com/rapidfuzz/RapidFuzz
* Send2Trash: https://github.com/arsenetar/send2trash
* watchdog: https://github.com/gorakhargosh/watchdog
* Qt for Python: https://doc.qt.io/qtforpython-6/ (licensing: https://doc.qt.io/qtforpython-6/licenses.html)
* PyInstaller: https://pyinstaller.org/ (license and bootloader exception: https://pyinstaller.org/en/stable/license.html)
* uv: https://docs.astral.sh/uv/

## Models
* BAAI/bge-small-en-v1.5: https://huggingface.co/BAAI/bge-small-en-v1.5 (ONNX: https://huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q)
* Qwen3-Embedding-0.6B: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B (ONNX: https://huggingface.co/Qdrant/Qwen3-Embedding-0.6B-onnx)
* paraphrase-multilingual-MiniLM-L12-v2: https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
* potion-base-8M (Model2Vec): https://huggingface.co/minishlab/potion-base-8M
* nomic-embed-text-v1.5: https://huggingface.co/nomic-ai/nomic-embed-text-v1.5

## Algorithms and Windows behaviour
* Reciprocal Rank Fusion: G. V. Cormack, C. L. A. Clarke, S. Büttcher, "Reciprocal rank fusion outperforms Condorcet and individual rank learning methods", SIGIR 2009.
* Perceptual hashing: https://www.hackerfactor.com/blog/index.php?/archives/432-Looks-Like-It.html (aHash/pHash) and dHash (same author).
* c-TF-IDF topic labels: M. Grootendorst, "BERTopic", arXiv:2203.05794.
* MoveFileExW (no replace without `MOVEFILE_REPLACE_EXISTING`): https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-movefileexw
* Python `os.rename` on Windows raises if the destination exists: https://docs.python.org/3/library/os.html#os.rename
* File attribute constants (REPARSE_POINT, RECALL_ON_DATA_ACCESS, OFFLINE): https://learn.microsoft.com/windows/win32/fileio/file-attribute-constants
* Reparse tags (IO_REPARSE_TAG_CLOUD*): https://learn.microsoft.com/windows/win32/fileio/reparse-point-tags
* Long paths and the `\\?\` prefix: https://learn.microsoft.com/windows/win32/fileio/maximum-file-path-limitation
* `os.stat` on Windows (`st_ino` from FILE_ID_INFO, `st_reparse_tag`): https://docs.python.org/3/library/os.html#os.stat_result
* SQLite WAL and `synchronous`: https://www.sqlite.org/wal.html , https://www.sqlite.org/pragma.html#pragma_synchronous

## Added with quick search and the 2026-10-27 review (URLs checked to resolve)
* RegisterHotKey / WM_HOTKEY: https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-registerhotkey , https://learn.microsoft.com/windows/win32/inputdev/wm-hotkey
* GetFinalPathNameByHandleW (what `os.path.realpath` uses on Windows): https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-getfinalpathnamebyhandlew , https://docs.python.org/3/library/os.path.html
* Windows file naming (reserved names, trailing dots/spaces): https://learn.microsoft.com/windows/win32/fileio/naming-a-file
* SQLite FTS5 external content tables and their triggers; unicode61 tokenizer options: https://www.sqlite.org/fts5.html
* `msvcrt.locking` (writer lock): https://docs.python.org/3/library/msvcrt.html
* `HF_HUB_OFFLINE` (read once at import): https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables
* OpenBLAS (per-thread buffers; `OPENBLAS_NUM_THREADS`): https://github.com/OpenMathLib/OpenBLAS
* Flow Launcher (design inspiration for quick search): https://www.flowlauncher.com/
