"""Installed apps: list them, rank them by size, and hand the removal to the app's own uninstaller.

What this module does and does not do
-------------------------------------
* Listing is read-only: the Windows "Uninstall" registry keys (the same list as Settings > Apps) and,
  for Microsoft Store apps, ``Get-AppxPackage``. Nothing is written.
* Sizes come from what each installer recorded (``EstimatedSize``). Apps that recorded none show
  "unknown" until the user asks to measure them; measuring only adds up file sizes (nothing is
  opened or changed), never enters Windows' own folders or Store app storage, and never follows
  links.
* Removal never deletes files itself. It starts the app's own uninstaller (``UninstallString``, or
  ``Remove-AppxPackage`` for Store apps), which shows its own window and asks Windows for
  permission when it needs it; this program is never run as administrator.
* Windows updates, hidden system components and entries that cannot be removed are not listed.
  Runtimes, drivers and redistributables are listed separately as *components*, because other
  programs may depend on them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

from .safety import is_within, norm

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000

# Names of things other programs rely on: listed under "components", with an extra warning.
COMPONENT_RE = re.compile(
    r"(visual c\+\+|\.net (core|runtime|sdk|desktop|framework)|microsoft \.net|windows (software development "
    r"kit|sdk|driver)|webview2|redistributable|\bruntime\b|\bdriver\b|directx|update health|edge update|"
    r"windows subsystem|vulkan|openal|physx|java\(tm\)|java \d|nvidia (graphics|hd audio|physx|"
    r"frameview|install)|intel\(r\)|realtek|bonjour|hp support|ms ?xml)", re.I)
# Hotfixes and updates are never listed.
UPDATE_RE = re.compile(r"^(security update|hotfix|update for)\b|\(kb\d+\)|^kb\d{6,}", re.I)
_MSI_INSTALL = re.compile(r"^\s*\"?msiexec(?:\.exe)?\"?\s+/i\s*(\{[0-9a-f-]{36}\})\s*$", re.I)
_SAFE_PACKAGE = re.compile(r"^[A-Za-z0-9._~\-]+$")

UNINSTALL_KEYS = (
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "machine"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "machine"),
    ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "user"),
)


@dataclass
class App:
    key: str  # unique and stable: "<scope>:<registry key name>" or "store:<package full name>"
    name: str
    publisher: str = ""
    version: str = ""
    installed: str = ""  # YYYY-MM-DD, or ""
    size: int = 0  # bytes; 0 means unknown
    location: str = ""
    icon: str = ""  # a file Windows can take an icon from
    uninstall: str = ""
    scope: str = "machine"  # machine / user / store
    component: bool = False
    package: str = ""  # Store apps: the package full name
    size_note: str = ""  # "recorded by the installer" / "measured"

    @property
    def installed_ts(self) -> float:
        try:
            return time.mktime(time.strptime(self.installed, "%Y-%m-%d"))
        except (ValueError, OverflowError):
            return 0.0


# ------------------------------------------------------------------------------------ listing
def _registry_values(hive_name: str, path: str) -> Iterator[tuple[str, dict]]:
    """``(key name, {value name: value})`` for each subkey of a registry path. Missing paths and
    unreadable keys are skipped."""
    import winreg

    hive = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}[hive_name]
    try:
        root = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
    except OSError:
        return
    with root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                return
            i += 1
            values: dict = {}
            try:
                with winreg.OpenKey(root, sub, 0, winreg.KEY_READ) as k:
                    j = 0
                    while True:
                        try:
                            name, val, _ = winreg.EnumValue(k, j)
                        except OSError:
                            break
                        j += 1
                        values[name] = val
            except OSError:
                continue
            yield sub, values


def _icon_file(raw: str) -> str:
    """``"C:\\x\\app.exe",0`` -> ``C:\\x\\app.exe`` (only if it exists)."""
    p = (raw or "").strip()
    if p.startswith('"'):
        p = p[1:].split('"', 1)[0]
    else:
        p = re.sub(r",\s*-?\d+$", "", p)
    p = os.path.expandvars(p)
    return p if p and os.path.isfile(p) else ""


def from_registry(sub: str, v: dict, scope: str) -> App | None:
    """One Uninstall-key entry as an ``App``, or ``None`` if it should not be listed."""
    name = str(v.get("DisplayName") or "").strip()
    if not name or UPDATE_RE.search(name):
        return None
    if v.get("SystemComponent") == 1 or v.get("ParentKeyName") or v.get("ParentDisplayName"):
        return None
    if v.get("NoRemove") == 1:
        return None
    cmd = str(v.get("UninstallString") or v.get("QuietUninstallString") or "").strip()
    if not cmd:
        return None
    est = v.get("EstimatedSize")
    size = int(est) * 1024 if isinstance(est, int) and est > 0 else 0
    d = str(v.get("InstallDate") or "")
    installed = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if re.fullmatch(r"\d{8}", d) else ""
    loc = os.path.expandvars(str(v.get("InstallLocation") or "").strip().strip('"'))
    return App(
        key=f"{scope}:{sub}", name=name, publisher=str(v.get("Publisher") or "").strip(),
        version=str(v.get("DisplayVersion") or "").strip(), installed=installed, size=size,
        location=loc if loc and os.path.isdir(loc) else "", icon=_icon_file(str(v.get("DisplayIcon") or "")),
        uninstall=cmd, scope=scope, component=bool(COMPONENT_RE.search(name)),
        size_note="recorded by the installer" if size else "")


def list_registry_apps(reader: Callable[[str, str], Iterable[tuple[str, dict]]] = _registry_values) -> list[App]:
    out: dict[tuple[str, str], App] = {}
    for hive, path, scope in UNINSTALL_KEYS:
        for sub, values in reader(hive, path):
            app = from_registry(sub, values, scope)
            if app is not None:
                out.setdefault((app.name.lower(), app.version), app)  # 32- and 64-bit views list some apps twice
    return distrust_repeated_sizes(list(out.values()))


def distrust_repeated_sizes(found: list[App], at_least: int = 3, minimum: int = 100 * 2**20) -> list[App]:
    """Some installers (repacks especially) write the same large size for every app they install.
    When ``at_least`` apps claim exactly the same big size, none of them is believed: they show as
    unknown until measured, instead of distorting the ranking and the totals."""
    same = Counter(a.size for a in found if a.size >= minimum)
    for a in found:
        if same.get(a.size, 0) >= at_least:
            a.size, a.size_note = 0, ""
    return found


_STORE_SCRIPT = r"""
$names = @{}
Get-StartApps | Where-Object { $_.AppID -like '*!*' } | ForEach-Object { $names[($_.AppID -split '!')[0]] = $_.Name }
$r = @(Get-AppxPackage | Where-Object { -not $_.IsFramework -and -not $_.NonRemovable -and $names.ContainsKey($_.PackageFamilyName) } |
  ForEach-Object { [pscustomobject]@{ name = $names[$_.PackageFamilyName]; full = $_.PackageFullName;
                   publisher = $_.Publisher; version = $_.Version.ToString(); loc = $_.InstallLocation } })
ConvertTo-Json -InputObject $r -Compress
"""


def list_store_apps(timeout: float = 30.0) -> list[App]:
    """Microsoft Store apps that have a Start-menu entry (the ones people think of as apps)."""
    if not IS_WINDOWS:
        return []
    try:
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _STORE_SCRIPT],
                              capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
                              encoding="utf-8", errors="replace")
        data = json.loads(proc.stdout.strip() or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        log.debug("store apps unavailable", exc_info=True)
        return []
    out = []
    for d in data if isinstance(data, list) else [data]:
        full = str(d.get("full") or "")
        if not full or not _SAFE_PACKAGE.match(full):
            continue
        cn = re.search(r"CN=([^,]+)", str(d.get("publisher") or ""))
        out.append(App(key=f"store:{full}", name=str(d.get("name") or full), publisher=cn.group(1).strip('" ') if cn else "",
                       version=str(d.get("version") or ""), scope="store", package=full,
                       uninstall="Remove-AppxPackage", location=""))
    return out


def list_apps(include_store: bool = True) -> list[App]:
    """Every removable app, de-duplicated, largest first (unknown sizes last)."""
    apps = list_registry_apps() if IS_WINDOWS else []
    if include_store:
        known = {a.name.lower() for a in apps}
        apps += [a for a in list_store_apps() if a.name.lower() not in known]
    apps.sort(key=lambda a: (-a.size, a.name.lower()))
    return apps


# ------------------------------------------------------------------------------------ sizes
def _too_broad() -> set[str]:
    """Folders that hold many apps (or everything): an app that recorded one of these as its
    location must not make us add up the whole thing."""
    names = (r"%ProgramFiles%", r"%ProgramFiles(x86)%", r"%ProgramW6432%", r"%ProgramData%", r"%USERPROFILE%",
             r"%APPDATA%", r"%LOCALAPPDATA%", r"%LOCALAPPDATA%\Programs", r"%PUBLIC%", "%SystemDrive%\\",
             r"%SystemDrive%\Users")
    return {norm(os.path.expandvars(v)) for v in names if "%" not in os.path.expandvars(v)}


def measurable(path: str) -> bool:
    """May we add up the sizes in ``path``? Real folders only; not Windows' own folders, Store
    app storage, a drive root, or a folder that holds many apps."""
    if not path or not os.path.isdir(path):
        return False
    key = norm(path)
    if os.path.splitdrive(key)[1].strip("\\/") == "" or key in _too_broad():
        return False
    win = norm(os.environ.get("SystemRoot", r"C:\Windows"))
    return not (is_within(key, win) or "\\windowsapps" in key + "\\")


def folder_size(path: str, seconds: float = 8.0, cancel: Callable[[], bool] | None = None) -> tuple[int, bool]:
    """``(bytes, complete)``: the summed size of the files under ``path``. Reads directory entries
    only (no file is opened), skips links and junctions, and stops after ``seconds``."""
    total, stack, end = 0, [path], time.monotonic() + seconds
    while stack:
        if time.monotonic() > end or (cancel is not None and cancel()):
            return total, False
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_symlink() or (e.is_dir(follow_symlinks=False) and _is_reparse(e)):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total, True


def _is_reparse(e: os.DirEntry) -> bool:
    try:
        return bool(getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400)
    except OSError:
        return False


# ------------------------------------------------------------------------------------ removal
def uninstall_command(app: App) -> str | list[str]:
    """What to start to uninstall ``app``: a command line for the app's own uninstaller, or a
    PowerShell argument list for a Store app. Windows Installer entries that point at the
    "change or repair" dialog (``/I{GUID}``) are turned into the uninstall (``/X{GUID}``)."""
    if app.scope == "store":
        if not _SAFE_PACKAGE.match(app.package):
            raise ValueError("unexpected package name")
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                f"Remove-AppxPackage -Package '{app.package}'"]
    cmd = app.uninstall.strip()
    if not cmd:
        raise ValueError("this app has no uninstaller")
    m = _MSI_INSTALL.match(cmd)
    return f"MsiExec.exe /X{m.group(1)}" if m else cmd


def split_command(cmd: str) -> tuple[str, str]:
    """``'"C:\\Program Files\\X\\unins000.exe" /SILENT'`` -> ``(exe, arguments)``. An unquoted
    path that contains spaces is resolved the way Windows does: the longest leading part that
    names an existing file (with or without ``.exe``)."""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        exe, _, rest = cmd[1:].partition('"')
        return exe, rest.strip()
    for i in [m.start() for m in re.finditer(r" ", cmd)] + [len(cmd)]:
        for cand in (cmd[:i], cmd[:i] + ".exe"):
            if os.path.isfile(cand):
                return cand, cmd[i:].strip()
    exe, _, rest = cmd.partition(" ")
    return exe, rest.strip()


def _shell_execute(exe: str, args: str) -> None:
    """Start ``exe`` the way Explorer does. If the program's manifest asks for administrator
    rights, Windows shows its own permission prompt; this process stays as it is."""
    import ctypes

    rc = ctypes.windll.shell32.ShellExecuteW(None, "open", exe, args or None, None, 1)
    if rc <= 32:
        raise OSError(f"Windows could not start {os.path.basename(exe)} (code {rc})")


def launch_uninstall(app: App, popen: Callable = subprocess.Popen, shell_execute: Callable | None = None):
    """Start the app's own uninstaller and return the process (``None`` if Windows started it
    after asking for permission). It shows its own window; this program does not elevate and
    does not touch the app's files."""
    cmd = uninstall_command(app)
    log.info("starting uninstaller for %s", app.name)
    try:
        return popen(cmd, shell=False, creationflags=CREATE_NO_WINDOW if isinstance(cmd, list) else 0)
    except OSError as e:
        if getattr(e, "winerror", None) != 740 or isinstance(cmd, list):  # 740: the program needs administrator rights
            raise
    exe, args = split_command(cmd)
    (shell_execute or _shell_execute)(exe, args)
    return None


def _key_exists(hive_name: str, path: str, sub: str) -> bool:
    import winreg

    hive = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}[hive_name]
    try:
        winreg.CloseKey(winreg.OpenKey(hive, path + "\\" + sub, 0, winreg.KEY_READ))
        return True
    except OSError:
        return False


def still_installed(app: App, exists: Callable[[str, str, str], bool] = _key_exists) -> bool:
    """Is the registry entry of a classic app still there? (Store apps: ask ``list_store_apps``.)"""
    if app.scope == "store":
        return any(a.package == app.package for a in list_store_apps())
    scope, _, sub = app.key.partition(":")
    return any(exists(h, p, sub) for h, p, s in UNINSTALL_KEYS if s == scope)
