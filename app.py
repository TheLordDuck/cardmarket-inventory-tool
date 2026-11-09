"""Entry point for the packaged release (see build.ps1).

    CardmarketInventoryTool.exe              -> opens the GUI
    CardmarketInventoryTool.exe --cli <cmd>  -> runs one CLI command
                                                (what the GUI does per click)
"""
import os
import sys


def _ensure_stdio() -> None:
    # A windowed (no-console) build has no stdout/stderr unless a parent
    # process gave it a pipe -- give logging somewhere harmless to write.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


if __name__ == "__main__":
    _ensure_stdio()
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        del sys.argv[1]
        from src.main import main
    else:
        from src.gui import main
    main()
