"""`python -I -m contribution_mirror ...`: the same commands as src/mirror.py, for an installed copy."""
import sys

from .cli import main

sys.exit(main())
