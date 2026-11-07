"""Desktop shortcuts for the three ways to open the app, with the app's icon.

    Local File Intelligence   the everyday app
    LFI Advanced              the detailed developer window
    LFI Quick Search          the pop-up search bar (also lives in the tray)

``python -m lfi.shortcuts`` (startup.bat option 7) creates or refreshes them; running it again
simply rewrites the same three files, so a moved project or a new Python environment is fixed by
running it once more. Only these three files are ever written; nothing else on the Desktop is
touched, and deleting them is always safe.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from pathlib import Path

from . import config

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
ENTRIES = (
    ("Local File Intelligence", "lfi.ui.app", "", "Find files by describing them, clean up and tidy"),
    ("LFI Advanced", "lfi.ui.app", "--advanced", "Every detail and setting"),
    ("LFI Quick Search", "lfi.ui.quick", "", "Pop-up search bar (Alt+Shift+Space)"),
)


def write_ico(path: str | Path) -> str:
    """Write the app icon as a multi-size .ico (PNG-compressed frames, as Windows Vista and later read)."""
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication

    from .ui.quick import render_app_icon

    if QGuiApplication.instance() is None:
        QGuiApplication([])
    frames = []
    for s in ICON_SIZES:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        render_app_icon(s).save(buf, "PNG")
        frames.append(bytes(buf.data()))
    out = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    for s, png in zip(ICON_SIZES, frames):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(out + b"".join(frames))
    return str(path)


def launch_target(module: str, arg: str = "") -> tuple[str, str, str]:
    """``(program, arguments, working folder)`` that starts ``module`` without a console window."""
    if getattr(sys, "frozen", False):  # the packaged app: one exe, switches choose the window
        flag = {"lfi.ui.quick": "--quick"}.get(module, "")
        return sys.executable, " ".join(a for a in (flag, arg) if a), str(Path(sys.executable).parent)
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    project = Path(__file__).resolve().parents[2]
    return str(pyw if pyw.exists() else exe), " ".join(a for a in ("-m", module, arg) if a), str(project)


_SCRIPT = r"""
$w = New-Object -ComObject WScript.Shell
$items = [Console]::In.ReadToEnd() | ConvertFrom-Json
$desktop = [Environment]::GetFolderPath('Desktop')
foreach ($i in $items) {
  $dir = if ($i.folder) { $i.folder } else { $desktop }
  $path = Join-Path $dir ($i.name + '.lnk')
  $l = $w.CreateShortcut($path)
  $l.TargetPath = $i.target; $l.Arguments = $i.args; $l.WorkingDirectory = $i.cwd
  $l.IconLocation = $i.icon; $l.Description = $i.desc
  $l.Save()
  $path
}
"""


def create(folder: str | None = None) -> list[str]:
    """Create or refresh the three shortcuts (on the Desktop unless ``folder`` is given); returns their paths."""
    icon = write_ico(config.app_home() / "lfi.ico")
    items = []
    for name, module, arg, desc in ENTRIES:
        target, args, cwd = launch_target(module, arg)
        items.append({"name": name, "target": target, "args": args, "cwd": cwd, "icon": icon + ",0", "desc": desc,
                      "folder": folder or ""})
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
                          input=json.dumps(items), capture_output=True, text=True, timeout=60,
                          creationflags=0x08000000)
    if proc.returncode != 0:
        raise OSError((proc.stderr or proc.stdout or "PowerShell failed").strip()[:300])
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    folder = args[args.index("--folder") + 1] if "--folder" in args else None
    if os.name != "nt":
        print("Shortcuts are created on Windows only.", file=sys.stderr)
        return 1
    try:
        made = create(folder)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for p in made:
        print("  " + p)
    print("Shortcuts are ready (delete them any time; this only rewrites those three files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
