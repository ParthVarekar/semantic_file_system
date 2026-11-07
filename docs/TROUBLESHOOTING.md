# Troubleshooting and recovery

## Recovering files

**Undo an organize batch**: Activity → select the batch → *Undo selected organize batch*
(or `lfi undo <batch-id>`; `lfi history` lists batches). Files move back to where they were,
and folders the batch created are removed if empty. A file is skipped (never overwritten) if
something else now occupies its original path.

**Restore quarantined files**: Quarantine → check → *Restore checked* (or
`lfi quarantine list` then `lfi quarantine restore <id> ...`). If the original path is occupied,
the file is restored as `name (restored).ext` or left in quarantine, depending on your choice.
Before restoring, the app verifies the quarantined copy against its recorded BLAKE3 hash.

**Without the app or its database**: each quarantine folder contains `manifest.jsonl`; every
line records `original` and `quarantined` paths. Quarantined files are ordinary files at
`...\quarantine\<batch>\<operation>\<original name>` and can be moved back by hand.

**Recycle Bin**: files sent from quarantine to the Recycle Bin can be restored from the
Windows Recycle Bin until it is emptied. Permanently deleted files cannot be recovered by the
app.

## After a crash or power loss

At start-up (GUI and CLI) the app reconciles any operation journaled as in progress:

| Situation found on disk | Action |
|---|---|
| Source present, destination absent | Marked "not performed"; file untouched |
| Source absent, destination present with expected size | Marked done (move had completed) |
| Leftover `*.lfi-partial` (interrupted cross-drive copy) | Removed (it is only ever our own incomplete copy) |
| Both present after a verified cross-drive copy | Our copy removed, original kept |
| Anything else | Marked **needs review**, nothing deleted; shown on the Activity page |

Run it manually with `lfi recover`. The full audit trail is in
`%LOCALAPPDATA%\LocalFileIntelligence\logs\operations.jsonl`.

## Common problems

| Symptom | Cause / fix |
|---|---|
| "Keyword search only - Embedding model ... is not downloaded" | Settings → Download, or `lfi models download "BAAI/bge-small-en-v1.5"`. |
| Status shows CPU although you have a GPU | Run `scripts\setup.ps1 -Gpu`; check Settings → providers list contains `DmlExecutionProvider`. A fallback reason is shown in the status bar. |
| "Folder not allowed / protected location" | Windows, Program Files, ProgramData, AppData and driver folders can't be indexed by design. You can select a whole drive (even C:\); those folders inside it are skipped. |
| "Folder overlaps an already selected folder" | That folder is already covered by a wider one you selected. (Adding a whole drive such as C:\ takes over the folders inside it and keeps their index.) |
| Some files show *error / timeout / locked / encrypted* | Folders & indexing → *Show problem files*. These files are skipped and indexing continues. Locked files are retried on the next run. Encrypted PDFs are indexed by name only. |
| "file was modified since it was indexed; re-index first" | Safety check: run indexing again, then retry. |
| "another window or command is moving files right now" | File operations from the app and the CLI run one at a time. Wait for the other one to finish (it is listed in Activity / `lfi history`), then try again. |
| "the path goes through a junction or symbolic link" | A folder inside your selected folder was replaced by a link since indexing; nothing was moved. Re-index, or select the link's real target as a folder. |
| "destination is on another drive" | Cross-drive moves are disabled by default; enable them in Settings → File operations (copy, verify, then remove). |
| "inside a software project" | Project contents are never reorganized. Move the whole project folder yourself if needed. |
| "online-only cloud file" | OneDrive placeholders aren't read or moved; use *Always keep on this device* first. |
| Indexing is slow on CPU | Expected with the default model (~6 files/s for typical documents). Use `-Gpu`, or choose `minishlab/potion-base-8M` in Settings. Indexing is resumable, so you can cancel and continue later. |
| High memory use while indexing | Set batch size to 0 (auto) or 8; lower *Reader processes*. |
| "Image search: image model not downloaded" | Settings → Image search → Download, or `lfi models download-vision nomic-q` (~230 MB). Then run indexing again; only images are processed. |
| Image results look unrelated | Visual scores are relative to your collection; the reasons column shows strength (strong / good / possible). Describe what is *visible* ("whiteboard with diagrams") rather than text inside the image (no OCR). |
| Image indexing too slow / too heavy | Settings → *CPU threads for images* (default: a quarter of the CPU), or switch to `clip-b32` with `-Gpu`, or untick image search. |
| A folder isn't searchable | Tool-made folders (`.venv*`, `node_modules`, `.next`, caches, and `dist`/`build`/`target`/`out` inside software projects) are skipped on purpose. Remove the pattern in Advanced tools > Folders > exclusions if you need one. |
| A folder at the top of a drive isn't indexed | When a whole drive (or several of its folders at once) is selected, folders that hold programs rather than documents are left out: `Python*`, `msys*`, `cygwin*`, `mingw*`, `anaconda*`, PC-maker folders (`SWSetup`, `hp`, `dell`, `lenovo`, `System.Sav`), `tmp`, `temp`, `llama`, `chrome-debug`, `edb`, `.GamingRoot`, and profiles under `Users` other than yours and `Public`. Add such a folder on its own to index it anyway. |
| A picture is found by name but not by describing it | Tiny images (icons) and most frames in a folder of video frames are not run through the image model, to save time; an even sample of each recording is. |
| Apps shows "Unknown size" | The installer didn't record a size (or several apps claimed the identical huge size, which is not believed). Press **Measure sizes** in Apps: it adds up each app's folder (read-only). Store apps keep their files in protected storage and stay "unknown". |
| An app is missing from Apps | Windows updates, hidden system components and entries that can't be removed are not listed, and runtimes/drivers are behind *Show runtimes and drivers*. Apps without a registered uninstaller can't be removed from here. |
| Uninstall opened a window and nothing happened yet | That window is the app's own uninstaller; finish it there (Windows may ask for permission). The list updates by itself when the app is gone. |
| Desktop shortcuts have no icon, or point to the wrong place | Run `startup.bat`, option 7 (or `python -m lfi.shortcuts`): it rewrites *Local File Intelligence*, *LFI Advanced* and *LFI Quick Search* with the right program, folder and icon. |
| Paths longer than 260 characters | Supported (`\\?\` prefix is used internally). |
| `uv sync` removed GPU support | Re-run `scripts\setup.ps1 -Gpu`. Launch with `scripts\run.ps1` (not `uv run`). |
| Quick search shortcut does nothing | Another program owns it. The tray icon's tooltip shows the shortcut actually in use; the default falls back to Win+Alt+Space, Ctrl+Alt+K or Win+Shift+F. Pick another in Settings → Quick search, then quit and restart quick search from the tray. |
| Quick search shows only name/text matches at first | The search models load on the first search (a second or two) and are released after 10 idle minutes; keyword results are shown meanwhile. |
| Quick search finds nothing | It searches the existing index only: add folders and run indexing in the full app (Shift+Enter in quick search opens it). |
| Remove quick search from sign-in | Settings → Quick search → untick *Start quick search when I sign in*, or delete `Local File Intelligence Quick Search.lnk` from `shell:startup`. |
| App log | `%LOCALAPPDATA%\LocalFileIntelligence\logs\app.log` (local only); quick search logs to `quick.log` in the same folder. |

## Resetting

* Clear index: Settings → *Clear index...* (or `lfi clear-index --yes`). Files on disk,
  quarantine records and history are untouched.
* Full reset: restore or dispose of quarantined files, close the app, delete
  `%LOCALAPPDATA%\LocalFileIntelligence`.
