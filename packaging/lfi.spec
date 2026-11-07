# PyInstaller spec: one-folder build (faster start-up than one-file and friendlier to the
# spawned worker processes, which re-launch the executable).
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

hidden = collect_submodules("fastembed") + collect_submodules("lfi") + ["sklearn.cluster", "imagehash"]
datas = collect_data_files("sqlite_vec") + collect_data_files("pypdfium2") + collect_data_files("pypdfium2_raw") \
    + collect_data_files("fastembed") + collect_data_files("docx") + collect_data_files("pptx")
binaries = collect_dynamic_libs("sqlite_vec") + collect_dynamic_libs("pypdfium2_raw") + collect_dynamic_libs("onnxruntime")

a = Analysis(["launcher.py"], pathex=["../src"], binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "matplotlib", "IPython", "pytest", "PySide6.QtWebEngineCore"], noarchive=False)
# Pin DLLs that Python itself ships (e.g. OpenSSL) to the interpreter's own copies. Without this,
# PyInstaller may resolve them from unrelated software on PATH (seen: an HP utility's
# libcrypto-3-x64.dll), which breaks `_ssl` and is a supply-chain risk.
import os, sys
_py_dlls = os.path.join(sys.base_prefix, "DLLs")
_own = {n.lower(): os.path.join(_py_dlls, n) for n in os.listdir(_py_dlls) if n.lower().endswith(".dll")}
a.binaries = [(name, _own.get(os.path.basename(name).lower(), src), kind) for name, src, kind in a.binaries]

pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LocalFileIntelligence", console=False,
          icon=None, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="lfi", console=True, icon=None, upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="LocalFileIntelligence")
