# Packaging a Windows executable

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Dev          # once (add -Gpu for a DirectML build)
powershell -ExecutionPolicy Bypass -File scripts\package.ps1             # -> dist\LocalFileIntelligence\
powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -BundleModel  # also copy cached models (offline installs)
```

Output (one-folder build, about 315 MB without models, CPU ONNX Runtime):

```
dist\LocalFileIntelligence\
  LocalFileIntelligence.exe   desktop app (windowed)
  lfi.exe                     command-line interface (console): lfi.exe index, lfi.exe recover, ...
  _internal\                  Python runtime, Qt, ONNX Runtime, libraries
  models\                     only with -BundleModel; copied once into the user's model cache
```

`LocalFileIntelligence.exe --quick` starts the quick search window (tray icon, Alt+Shift+Space);
add `--hidden` to start in the tray only, as the optional sign-in shortcut does.

Bundled models are copied on first start into `%LOCALAPPDATA%\LocalFileIntelligence\models` (only
if that cache is empty). Reads and downloads always use that cache, so the program folder can be
read-only (Program Files) and replacing it on upgrade never loses downloaded models.
`package.ps1 -BundleModel` bundles the cache named by `LFI_MODELS`, or the default cache.

Distribute the whole folder (zip it, or wrap it in an installer such as Inno Setup or WiX).
User data still goes to `%LOCALAPPDATA%\LocalFileIntelligence`, so upgrading the program
folder keeps the index, quarantine and history.

## Design notes

* **One-folder, not one-file.** A one-file exe unpacks itself to `%TEMP%` on every launch,
  and the isolated extractor workers re-launch the executable. One-folder starts faster and
  avoids repeated unpacking.
* **Worker processes.** `packaging/launcher.py` calls `multiprocessing.freeze_support()` first,
  so spawned extractor workers work in the frozen app (verified: 39-file demo corpus indexed by
  `lfi.exe`).
* **DLL hygiene.** PyInstaller resolves some DLLs by searching `PATH`. On the development
  machine it picked up `libcrypto-3-x64.dll` from an unrelated HP utility, which broke `_ssl`
  (and therefore model loading). The spec now pins every DLL that Python itself ships to the
  interpreter's own copy, `package.ps1` builds with a minimal `PATH`, and
  `packaging/audit_binaries.py` **fails the build** if any bundled DLL/PYD comes from outside
  the venv, the Python installation or `System32` (current build: 270 binaries, 0 outside).
* **LGPL (Qt).** Qt libraries are shipped as separate DLLs in `_internal\PySide6`, so users can
  replace them, as LGPL-3.0 requires. Include the Qt/PySide6 license texts and THIRD_PARTY.md
  with any distribution.
* **Code signing** is not configured. Unsigned executables trigger SmartScreen warnings; sign
  both `.exe` files with `signtool` and your certificate before distributing.

## Verified (2026-10-27)

| Check | Result |
|---|---|
| Build | `package.ps1` exit 0; binary-source audit clean |
| `lfi.exe demo / roots add / index / search / recover` | worked; workers spawned; 39 files embedded on CPU |
| `LocalFileIntelligence.exe` | window opened, model loaded from cache in 0.3 s, stayed running |
| Image search in the frozen app | `lfi.exe index` embedded 16 images in the frozen vision worker; `lfi.exe search --scope images "a qr code"` returned the QR image |
| Rebuild after the review fixes (same day) | `package.ps1` exit 0, 270 binaries, 0 from outside; CLI round trip with a non-ASCII folder and piped output; `--quick` registered its hotkey, showed, stayed single-instance; main window started |

Not verified: installer creation, code signing, a clean machine without Python or VC++
runtime (the build bundles `vcruntime140.dll` from the Python installation), and a DirectML
build (`setup.ps1 -Gpu -Dev` then `package.ps1` should work the same way but wasn't run).
