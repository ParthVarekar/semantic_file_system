# Privacy and threat model

## Data that stays on this computer

Everything: file contents, extracted text, embeddings, paths, metadata, search queries,
preferences, feedback, logs. All of it lives in one folder:

```
%LOCALAPPDATA%\LocalFileIntelligence\
  index.sqlite3 (+ -wal, -shm)   metadata, text excerpts, vectors, journal, preferences
  settings.json                  your settings
  models\                        downloaded embedding models
  quarantine\                    quarantined files from the system drive (+ manifest.jsonl)
  logs\app.log, operations.jsonl application log and audit trail of file operations
```

Quarantined files from other drives go to a hidden `.lfi-quarantine` folder at the top of the
selected folder on that drive, so moves stay atomic and never cross volumes.

**The index is sensitive.** It contains file names, folder structure and text excerpts of your
documents. It is protected by your Windows account's file permissions like any other file in
your profile. Controls in Settings → Privacy & data:

* **Remove extracted text**: blanks stored text (keyword search and previews stop working;
  hashes, metadata and vectors stay).
* **Keep extracted text** toggle: when off, text is discarded right after embedding.
* **Clear index** / **Rebuild**: removes all indexed data. Quarantine records and the
  operation history are kept so quarantined files stay restorable.
* **Exclusions**: folders and name patterns that are never read.
* Deleting the whole data folder removes everything the app stored. Restore any quarantined
  files first; the manifest lists their original paths.

Image search adds image vectors and auto-tags (e.g. "a photo of a receipt") to the index.
Like text excerpts, they describe your pictures, so treat them as sensitive. They are removed by
*Clear index*. Image files are decoded only inside the isolated low-priority worker process.

Note: embeddings can partially reveal the content they were computed from. Treat them like the
text itself.

## Network use

| When | What | Where |
|---|---|---|
| `scripts\setup.ps1` | Python packages; document model (~67 MB) and image model (~230 MB) unless `-NoModel` / `-NoImageModel` | pypi.org (via uv/pip), huggingface.co |
| Model download (setup, or Settings → Download, or `lfi models download`) | Model weights | huggingface.co |

Offline mode is lifted only for the duration of an explicit download and restored right after
(huggingface_hub caches the setting at import, so both the variable and its cached value are
switched). Quick search makes no network requests at all.

Nothing else. At startup the app sets `HF_HUB_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1` and
`DO_NOT_TRACK=1`, and loads models with `local_files_only=True`. A test
(`test_real_model_offline_cpu`) blocks every socket connection and verifies that embedding still
works. There is no telemetry, crash reporting, update check or remote logging. The Ollama
backend only accepts loopback URLs (`127.0.0.1`, `localhost`, `::1`).
`AzureExecutionProvider` ships inside the ONNX Runtime wheel but is never selected.

## Threat model

| Threat | Mitigation |
|---|---|
| Malicious or malformed documents exploit a parser (PDFium, zip/XML parsers, Pillow) | Parsing runs in separate low-priority processes. A crash or hang kills only that worker (per-file timeout, default 60 s). File content is never executed, and archives are listed, not extracted. Pillow's decompression-bomb limit stays enabled. Office macros are never run. |
| Accidental data loss through cleanup | No automatic deletion. Quarantine is a move; permanent deletion only applies to quarantined files and requires typing `DELETE PERMANENTLY`. Every operation is journaled, verified and reversible. |
| Acting on stale information (file changed after indexing) | Size, mtime and file ID are re-checked before every move. Duplicates are re-hashed, along with the keeper, before quarantine. |
| Overwriting a file | Same-volume `MoveFileExW` without `REPLACE_EXISTING`; restore conflicts never overwrite. |
| Path tricks (`C:\data` vs `C:\database`, case, `..`, junctions, symlinks) | Normalized, case-folded absolute paths with separator-aware prefix checks. Symlinks, junctions and mount points are never followed or moved. |
| Aliases of protected folders (`C:\PROGRA~1`, `\\?\C:\Windows`, `\\.\`, `::$INDEX_ALLOCATION`, `\\localhost\C$`, `GLOBALROOT`, the whole system drive) | A selected folder is checked both as typed and as resolved on disk (8.3 names expanded, device prefixes and stream syntax removed) and stored in its canonical form; loopback and administrative shares and the system drive root are refused. |
| A folder replaced by a junction after indexing (so an in-scope path leads outside, e.g. into the app's own data or another drive) | Every move, quarantine, restore and undo re-checks the filesystem as it is at that moment: no component between the selected folder and the file may be a junction or link, and the resolved location must not be protected. Otherwise the file is left untouched. |
| Two windows or commands working at once (the app and the CLI, a double-clicked button) | A cross-process writer lock: batches of file operations run one after another, and crash recovery never touches a batch another process is running. |
| Touching system or application folders | Windows, Program Files, ProgramData, AppData, `$Recycle.Bin`, System Volume Information and the app's own folder are always excluded. A root inside them is refused. |
| OneDrive online-only files being downloaded or deleted from the cloud | Placeholders (`RECALL_ON_DATA_ACCESS`/`OFFLINE`) are indexed by metadata only (they are never opened, not even to hash them; a test watches file opens) and are never moved. |
| Breaking software projects | Folders with project markers (`.git`, `package.json`, `pyproject.toml`, `*.sln`, ...) are detected; their contents are never moved and are penalized in cleanup scores. |
| Privilege misuse | Runs as a normal user; warns if started elevated; needs no admin rights. |
| Index exposure (backups, sync tools, other users) | Stored under `%LOCALAPPDATA%` (not roamed, not in OneDrive by default); clear/remove controls; documented here. |
| Crash or power loss mid-operation | Write-ahead journal with `synchronous=FULL`; `recover()` reconciles at the next start and reports anything that needs review. |
| Supply chain | Small dependency set, locked with hashes in `uv.lock`, all from PyPI. No downloaded executables. Models come only from the documented Hugging Face repositories. |

Out of scope: an attacker who already controls your Windows account (they can read your files
directly), and full-disk encryption (use BitLocker).
