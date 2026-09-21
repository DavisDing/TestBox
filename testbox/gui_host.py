"""Console entry point for the frozen GUI plugin Host.

The desktop GUI is built with PyInstaller's windowed subsystem, which does not
provide a usable stdout pipe on Windows.  GUI tasks therefore use this small
console-mode companion executable for the JSON Host protocol instead of
re-launching the windowed desktop executable.
"""
from __future__ import annotations

from testbox.core.host import main


if __name__ == "__main__":
    main()
