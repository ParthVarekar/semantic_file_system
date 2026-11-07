"""Frozen entry point.

* ``LocalFileIntelligence.exe``            -> desktop app (windowed)
* ``LocalFileIntelligence.exe --advanced`` -> the detailed tools window
* ``LocalFileIntelligence.exe --quick``    -> quick search window + tray icon (``--hidden`` to start in the tray)
* ``lfi.exe <command>``                     -> command-line interface (console), e.g. ``lfi.exe recover``
"""
import multiprocessing
import os
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # spawned extractor workers re-enter here first
    exe = os.path.splitext(os.path.basename(sys.executable))[0].lower()
    if exe == "lfi":
        from lfi.cli import main as cli_main

        cli_main(sys.argv[1:])
    elif "--quick" in sys.argv[1:]:
        from lfi.ui.quick import main as quick_main

        sys.exit(quick_main([a for a in sys.argv[1:] if a != "--quick"]))
    else:
        from lfi.ui.app import main

        sys.exit(main())
