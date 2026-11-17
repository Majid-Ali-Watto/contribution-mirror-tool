"""Paths, limits and constants. Nothing here has side effects."""
import os

MSG = "mirror"   # commit message of every mirror commit

# src/contribution_mirror/settings.py -> project root is three levels up.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SIBLINGS = os.path.dirname(PROJECT_ROOT)                        # its children are the "sibling folders"
REPOS_FILE = os.path.join(PROJECT_ROOT, "config", "repos.json")  # your projects (git-ignored)
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

LOG_MAX_BYTES = 5 * 1024 * 1024   # rotate to a new file at 5 MB
LOG_BACKUPS = 10                  # keep mirror.log + 10 older files (~55 MB worst case)
API_WINDOW_DAYS = 364             # GitHub's graph covers about one year
API_MARGIN_DAYS = 3               # incremental runs re-check this much before the last successful run
API_WORKERS = 8                   # parallel review lookups
SEARCH_CAP = 1000                 # GitHub search returns at most 1000 results per query
