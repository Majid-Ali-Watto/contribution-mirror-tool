"""Paths, limits and constants. Nothing here writes anything; the paths only look at what already exists.

Where your files live:
  - a checkout that already has config/.env or config/repos.json (or logs/) keeps using them, so existing setups
    never move;
  - otherwise the per-user folders of the operating system, so the code can be updated, cleaned (`git clean -fdx`)
    or installed read-only without touching your settings:
      Linux    ~/.config/contribution-mirror           ~/.local/state/contribution-mirror   (XDG_* respected)
      macOS    ~/Library/Application Support/contribution-mirror   ~/Library/Logs/contribution-mirror
      Windows  %APPDATA%\\contribution-mirror           %LOCALAPPDATA%\\contribution-mirror\\logs
MIRROR_ENV_FILE, MIRROR_REPOS_FILE and MIRROR_LOG_DIR still override each file or folder."""
import os
import sys

MSG = "mirror"   # commit message of every mirror commit
APP = "contribution-mirror"

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(PACKAGE_DIR, "templates")
# src/contribution_mirror/settings.py -> project root is three levels up (only meaningful in a checkout).
PROJECT_ROOT = os.path.dirname(os.path.dirname(PACKAGE_DIR))
ENTRY = os.path.join(PROJECT_ROOT, "src", "mirror.py")
IN_CHECKOUT = os.path.isfile(ENTRY)
SIBLINGS = os.path.dirname(PROJECT_ROOT) if IN_CHECKOUT else None   # `sync <name>` looks for <name> here first


def user_dir(kind, platform=None, environ=None, home=None):
    """The per-user folder for `kind` = 'config' or 'logs' on `platform` (default: this one)."""
    platform, environ = platform or sys.platform, os.environ if environ is None else environ
    home = home or os.path.expanduser("~")
    if platform in ("win32", "cygwin"):
        if kind == "config":
            return os.path.join(environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming"), APP)
        return os.path.join(environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local"), APP, "logs")
    if platform == "darwin":
        return os.path.join(home, "Library", "Application Support" if kind == "config" else "Logs", APP)
    if kind == "config":
        return os.path.join(environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), APP)
    return os.path.join(environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state"), APP)


_LEGACY_CONFIG = os.path.join(PROJECT_ROOT, "config")
_LEGACY_LOGS = os.path.join(PROJECT_ROOT, "logs")
CONFIG_DIR = (_LEGACY_CONFIG if IN_CHECKOUT and any(os.path.isfile(os.path.join(_LEGACY_CONFIG, n))
                                                    for n in (".env", "repos.json"))
              else user_dir("config"))
LOG_DIR = _LEGACY_LOGS if IN_CHECKOUT and os.path.isdir(_LEGACY_LOGS) else user_dir("logs")
ENV_FILE = os.path.join(CONFIG_DIR, ".env")                     # your settings (private)
REPOS_FILE = os.path.join(CONFIG_DIR, "repos.json")             # your projects (private)
ENV_TEMPLATE = os.path.join(TEMPLATES, "env.example")
REPOS_TEMPLATE = os.path.join(TEMPLATES, "repos.example.json")


def command_name():
    """What the user types to run this tool, for messages and help: `mirror.py` (short for `python3 -I src/mirror.py`)
    in a checkout, `contribution-mirror` when installed (pipx)."""
    return "mirror.py" if IN_CHECKOUT else "contribution-mirror"


def self_command(python=None, extra_flags=()):
    """How to start this tool again (the scheduler, the page's runs): the checkout's src/mirror.py, or the installed
    package. Always in isolated mode (-I), so nothing in the current folder can shadow a module. `extra_flags` are
    interpreter options such as -u."""
    flags = [python or sys.executable, "-I", *extra_flags]
    return flags + ([ENTRY] if IN_CHECKOUT else ["-m", "contribution_mirror"])


LOG_MAX_BYTES = 5 * 1024 * 1024   # rotate to a new file at 5 MB
LOG_BACKUPS = 10                  # keep mirror.log + 10 older files (~55 MB worst case)
API_WINDOW_DAYS = 364             # GitHub's graph covers about one year
API_MARGIN_DAYS = 3               # incremental runs re-check this much before the last successful run
API_WORKERS = 8                   # parallel review lookups
SEARCH_CAP = 1000                 # GitHub search returns at most 1000 results per query
