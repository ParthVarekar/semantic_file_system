"""Scope enforcement and Windows filesystem safety checks.

Every component that touches the filesystem asks this module first:

* ``ScopePolicy.check`` decides whether a path is inside the user's explicitly selected roots,
  outside exclusions, and outside protected operating-system / application directories.
* ``classify_entry`` interprets reparse points. Symlinks and junctions are never followed,
  and cloud placeholders (OneDrive "online-only" files) are indexed by metadata only, because
  reading their content would trigger a download.
* ``find_project_root`` detects software projects whose internal layout must never be
  reorganized automatically.
"""

from __future__ import annotations

import fnmatch
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_CLOUD_TAG_MASK = 0x9000001A  # IO_REPARSE_TAG_CLOUD and its CLOUD_1..F variants share these bits

PROJECT_MARKERS = (
    ".git", ".hg", ".svn", "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "CMakeLists.txt",
    "composer.json", "Gemfile", "mix.exs", "pubspec.yaml", "deno.json", ".project",
)
PROJECT_MARKER_SUFFIXES = (".sln", ".csproj", ".vcxproj", ".xcodeproj", ".uproject")


def norm(path: str | os.PathLike) -> str:
    """Absolute, normalized, case-folded key used for every path comparison."""
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))


def is_within(child_key: str, parent_key: str) -> bool:
    """True if normalized ``child_key`` equals or is below normalized ``parent_key``."""
    if child_key == parent_key:
        return True
    parent = parent_key if parent_key.endswith(os.sep) else parent_key + os.sep
    return child_key.startswith(parent)


def long_path(path: str) -> str:
    r"""Prefix with ``\\?\`` on Windows so >260-char paths work without the registry opt-in."""
    if not IS_WINDOWS:
        return path
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\") or len(p) < 240:
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def real_path(path: str | os.PathLike) -> str:
    r"""Where ``path`` really is on disk right now: junctions and symbolic links resolved, 8.3
    short names expanded (``C:\PROGRA~1``), stream syntax (``::$INDEX_ALLOCATION``) and device
    prefixes (``\\?\``, ``\\.\``, ``\\?\GLOBALROOT\...``) removed. Missing trailing parts are kept."""
    p = os.path.realpath(os.fspath(path))
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[8:]
    if p.startswith("\\\\?\\") and len(p) >= 6 and p[5] == ":":
        return p[4:]
    return p


# Windows folders that can exist at the top of *any* drive (a selected D:\ must still skip them).
DRIVE_SYSTEM_DIRS = frozenset({
    "$recycle.bin", "system volume information", "recovery", "$winreagent", "config.msi", "$windows.~bt",
    "$windows.~ws", "$sysreset", "$getcurrent", "windows.old", "onedrivetemp", "$extend",
})


def drive_system_dir(key: str) -> str | None:
    """The drive-level Windows folder that normalized ``key`` lies in, if any."""
    if not IS_WINDOWS:
        return None
    _, rest = os.path.splitdrive(key)
    first = rest.lstrip("\\/").split(os.sep, 1)[0]
    return first if first in DRIVE_SYSTEM_DIRS else None


# Folders at the top of a drive that hold programs, drivers or scratch data rather than anyone's
# documents. Unlike the protected folders they may be selected on their own; they are only skipped
# when a wider folder (a whole drive) is scanned, or picked together with other folders.
DRIVE_TOOL_DIRS = (
    "python*", "msys*", "cygwin*", "mingw*", "anaconda*", "miniconda*", "strawberry", "ruby*",
    "swsetup", "hp", "dell", "lenovo", "asus", "acer", "system.sav", "esd", "edb", ".gamingroot",
    "chrome-debug", "tmp", "temp", "llama", "$*", "*.~*",
)


def drive_tool_dir(key: str) -> str | None:
    """Why normalized ``key`` is program / non-personal data at drive level, or ``None``:
    a DRIVE_TOOL_DIRS folder at the top of a drive, or a profile under ``<drive>\\Users`` that
    isn't the signed-in user's (Windows service accounts, Default, TEMP, other people)."""
    if not IS_WINDOWS:
        return None
    _, rest = os.path.splitdrive(key)
    parts = [p for p in rest.split(os.sep) if p]
    if not parts:
        return None
    if any(fnmatch.fnmatch(parts[0], g) for g in DRIVE_TOOL_DIRS):
        return f"program folder ({parts[0]})"
    if parts[0] == "users" and len(parts) > 1:
        me = os.path.basename(os.environ.get("USERPROFILE", "")).lower()
        if parts[1] not in (me, "public"):
            return f"another account's profile ({parts[1]})"
    return None


def _unc_parts(path: str) -> tuple[str, str] | None:
    p = path.replace("/", "\\")
    if p.lower().startswith("\\\\?\\unc\\"):
        p = "\\\\" + p[8:]
    elif p.startswith("\\\\?\\") or p.startswith("\\\\.\\"):
        return None
    if not p.startswith("\\\\"):
        return None
    parts = p[2:].split("\\")
    return (parts[0].lower(), parts[1].lower() if len(parts) > 1 else "")


def _local_host_names() -> set[str]:
    import socket

    names = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0", "."}
    for n in (os.environ.get("COMPUTERNAME"), socket.gethostname()):
        if n:
            names.add(n.lower())
    return names


def alias_problem(path: str) -> str | None:
    """Why ``path`` is a spelling we refuse for a selected folder (loopback / administrative
    network shares, raw device paths), or ``None``."""
    unc = _unc_parts(path)
    if unc:
        server, share = unc
        if share.endswith("$"):
            return "Administrative network shares (such as C$ or ADMIN$) can't be selected."
        if server in _local_host_names() or server.startswith("127.") or server.endswith(".localhost"):
            return "This network path points back to this computer; select the folder by its local path."
    if path.startswith("\\\\?\\") or path.startswith("\\\\.\\"):
        return "Use a normal folder path (a drive letter or a network share), not a device path."
    return None


def protected_directories() -> list[str]:
    """Operating-system and application locations that are never scanned or modified."""
    env_dirs = [
        "SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
        "ProgramData", "APPDATA", "LOCALAPPDATA",
    ]
    out: list[str] = []
    for name in env_dirs:
        v = os.environ.get(name)
        if v:
            out.append(norm(v))
    profile = os.environ.get("USERPROFILE")
    if profile:
        out.append(norm(os.path.join(profile, "AppData")))  # also LocalLow
    if IS_WINDOWS:
        sysdrive = os.environ.get("SystemDrive", "C:")
        for drive_dir in ("$Recycle.Bin", "System Volume Information", "Recovery", "$WinREAgent",
                          "PerfLogs", "Config.Msi", "Windows", "Program Files",
                          "Program Files (x86)", "ProgramData", "Boot", "Windows.old", "$Windows.~BT",
                          "$Windows.~WS", "$SysReset", "$GetCurrent", "OneDriveTemp",
                          "Users\\Default", "Users\\Default User", "Users\\All Users",
                          # driver and vendor installers that sit at the top of the system drive
                          "Intel", "AMD", "NVIDIA", "Drivers", "inetpub", "MSOCache", "XboxGames"):
            out.append(norm(sysdrive + "\\" + drive_dir))
    else:
        out += [norm(p) for p in ("/bin", "/boot", "/dev", "/etc", "/lib", "/proc", "/sbin",
                                  "/sys", "/usr", "/var", "/System", "/Library")]
    # Our own state (index, models, quarantine) is never treated as user content.
    from .config import app_home

    out.append(norm(app_home()))
    return sorted(set(out))


class ScopeError(Exception):
    pass


@dataclass
class ScopePolicy:
    roots: list[str]
    exclude_globs: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    extra_protected: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._root_keys = [norm(r) for r in self.roots]
        self._exclude_keys = [norm(p) for p in self.exclude_paths]
        self._protected = protected_directories() + [norm(p) for p in self.extra_protected]
        # A protected directory that *contains* a selected root (e.g. the user picked a folder
        # inside %LOCALAPPDATA%) would make that root unusable; only the app's own home stays
        # protected in that case, the rest are relaxed for that explicitly chosen root.
        self._protected_for_root: dict[str, list[str]] = {}
        from .config import app_home

        home = norm(app_home())
        for rk in self._root_keys:
            self._protected_for_root[rk] = [
                p for p in self._protected
                if not (is_within(rk, p) and p != home) or is_within(p, rk)
            ]

    @staticmethod
    def validate_root(path: str) -> str | None:
        """Return an error message if ``path`` must not be selected as a scan root. Both the
        spelling given and the real location are checked, so aliases (``C:\\PROGRA~1``,
        ``\\\\?\\C:\\Windows``, ``\\\\localhost\\C$\\...``) can't smuggle in a protected folder."""
        if not os.path.isdir(path):
            return "Folder does not exist."
        real = real_path(path)
        for spelling in (os.fspath(path), real):
            err = alias_problem(spelling)
            if err:
                return err
        # A whole drive (even the system drive) may be selected: the protected folders inside it
        # are still never entered, because ScopePolicy.check prunes them during the scan.
        for key in (norm(path), norm(real)):
            for p in protected_directories():
                if is_within(key, p):
                    return f"This location is protected (inside {p})."
            sysdir = drive_system_dir(key)
            if sysdir:
                return f"This location is protected (Windows folder {sysdir})."
        try:
            st = os.lstat(path)
        except OSError as e:
            return f"Cannot access folder: {e.strerror}"
        if getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT and not is_cloud_reparse(st):
            return "Folder is a symbolic link or junction; select its target instead."
        return None

    def root_for(self, path: str) -> str | None:
        key = norm(path)
        best = None
        for rk, original in zip(self._root_keys, self.roots):
            if is_within(key, rk) and (best is None or len(rk) > len(norm(best))):
                best = original
        return best

    def excluded_name(self, name: str) -> bool:
        low = name.lower()
        return any(fnmatch.fnmatch(low, g.lower()) for g in self.exclude_globs)

    def check(self, path: str) -> str | None:
        """Return ``None`` if ``path`` is in scope, otherwise the reason it is not."""
        key = norm(path)
        root = None
        for rk in self._root_keys:
            if is_within(key, rk):
                root = rk
                break
        if root is None:
            return "outside selected folders"
        for p in self._protected_for_root.get(root, self._protected):
            if is_within(key, p):
                return f"protected location ({p})"
        if drive_system_dir(key):
            return f"protected location (Windows folder {drive_system_dir(key)})"
        tool = drive_tool_dir(key)
        if tool and drive_tool_dir(root) is None:  # skipped inside a wider scan, not when chosen itself
            return f"skipped {tool}"
        for ex in self._exclude_keys:
            if is_within(key, ex):
                return "excluded folder"
        rel = key[len(root):].strip(os.sep)
        if rel:
            for part in rel.split(os.sep):
                if self.excluded_name(part):
                    return f"excluded name ({part})"
        return None

    def check_physical(self, path: str) -> str | None:
        """``check`` plus the filesystem as it is right now, for anything that moves files.
        ``check`` only looks at the spelling; if a folder between the selected root and ``path``
        has since been replaced by a junction or symbolic link, the spelling is in scope but the
        file is not. Scanning never follows links, so it can rely on ``check`` alone."""
        reason = self.check(path)
        if reason:
            return reason
        root = self.root_for(path)
        link = link_between(path, root)
        if link:
            return f"the path goes through a junction or symbolic link ({link})"
        real = norm(real_path(path))
        for p in self._protected_for_root.get(norm(root), self._protected) if root else self._protected:
            if is_within(real, p):
                return f"protected location ({p})"
        if drive_system_dir(real):
            return f"protected location (Windows folder {drive_system_dir(real)})"
        return None

    def require(self, path: str) -> None:
        reason = self.check(path)
        if reason:
            raise ScopeError(f"{path}: {reason}")


def link_between(path: str, stop_at: str | None = None) -> str | None:
    """The first existing component of ``path`` - the path itself, then each parent up to and
    including ``stop_at`` (or the drive root) - that is a symbolic link, junction or mount point."""
    cur = os.path.abspath(path)
    stop = norm(stop_at) if stop_at else None
    while True:
        try:
            st = os.lstat(long_path(cur))
        except OSError:
            st = None
        if st is not None and is_link_like(st):
            return cur
        if stop is not None and norm(cur) == stop:
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def is_cloud_reparse(st: os.stat_result) -> bool:
    tag = getattr(st, "st_reparse_tag", 0)
    return bool(tag) and (tag & 0xFFFF0FFF) == _CLOUD_TAG_MASK


def is_link_like(st: os.stat_result) -> bool:
    """Symlink, junction, mount point, or other non-cloud reparse point: never follow or move."""
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
        return not is_cloud_reparse(st)
    return False


def is_cloud_placeholder(st: os.stat_result) -> bool:
    """Online-only cloud file: reading it would download it, so only metadata is indexed."""
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
                         | FILE_ATTRIBUTE_RECALL_ON_OPEN))


def is_system_file(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_SYSTEM)


def has_project_marker(dir_path: str, names: set[str] | None = None) -> bool:
    if names is None:
        try:
            names = set(os.listdir(dir_path))
        except OSError:
            return False
    if any(m in names for m in PROJECT_MARKERS):
        return True
    return any(n.endswith(PROJECT_MARKER_SUFFIXES) for n in names)


def find_project_root(path: str, stop_at: str) -> str | None:
    """Outermost ancestor of ``path`` (up to ``stop_at``) that looks like a software project."""
    stop_key = norm(stop_at)
    cur = os.path.dirname(os.path.abspath(path))
    found = None
    while is_within(norm(cur), stop_key):
        if has_project_marker(cur):
            found = cur
        if norm(cur) == stop_key:
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return found


def same_volume(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return os.path.splitdrive(os.path.abspath(a))[0].lower() == os.path.splitdrive(os.path.abspath(b))[0].lower()


def nearest_existing(path: str) -> str:
    p = os.path.abspath(path)
    while not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def is_elevated() -> bool:
    if not IS_WINDOWS:
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


__all__ = [
    "ScopePolicy", "ScopeError", "norm", "is_within", "long_path", "is_link_like",
    "is_cloud_placeholder", "is_cloud_reparse", "find_project_root", "has_project_marker",
    "protected_directories", "same_volume", "nearest_existing", "is_elevated", "Path",
    "real_path", "alias_problem", "drive_system_dir", "link_between",
]
