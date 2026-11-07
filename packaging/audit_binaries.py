"""Fail if the PyInstaller build bundled a DLL/PYD from outside the venv, the Python install, or
Windows itself (PyInstaller can otherwise resolve DLLs from unrelated software on PATH).

    python packaging/audit_binaries.py build/lfi/COLLECT-00.toc
"""

import re
import sys

ALLOWED = (r"\file_sorter\.venv", r"\uv\python", r"\windows\system32", r"\python3")


def main(toc_path: str) -> int:
    text = open(toc_path, encoding="utf-8").read()
    srcs = {s.replace("\\\\", "\\") for s in re.findall(r"'([A-Za-z]:\\\\[^']+?\.(?:dll|pyd))'", text)}
    bad = sorted(s for s in srcs if not any(k in s.lower() for k in ALLOWED) and "\\.venv" not in s.lower())
    print(f"bundled binaries: {len(srcs)}")
    print("from outside venv / Python / System32:", "none" if not bad else "")
    for b in bad:
        print("  ", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
