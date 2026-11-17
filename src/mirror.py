#!/usr/bin/env python3
"""Entry point: `python3 -I src/mirror.py [sync | scheduled | schedule | <core options>]`.

Python's isolated mode (-I) does not put this folder on the import path, so it is added here explicitly.
Only this folder is added (never the current directory), so nothing else can shadow the package."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from contribution_mirror.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
