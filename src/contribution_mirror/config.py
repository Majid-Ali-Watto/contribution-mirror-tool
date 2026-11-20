"""The user's settings, read from the .env settings file (and the real environment): who they are and where the mirror
is. Also `setup`: create the settings files from the templates and show where everything lives."""
import os
import stat
import sys
from dataclasses import dataclass

from . import envfile
from .fsutil import make_private, private_dir, write_private
from .settings import ENV_FILE, ENV_TEMPLATE, LOG_DIR, REPOS_FILE, REPOS_TEMPLATE

DEFAULT_ENV_FILE = ENV_FILE
REQUIRED = ("WORK_EMAIL", "PERSONAL_NAME", "PERSONAL_EMAIL", "EXPECTED_REMOTE")


@dataclass
class Config:
    work_emails: list
    personal_name: str
    personal_email: str
    expected_remote: str
    mirror_dir: str
    ssh_key: str = ""            # used only for pushing the mirror
    gh_user: str = ""            # force one gh account as the work account
    pr_reviews: bool = True      # False: commits only
    log_dir: str = ""
    clone_cache: str = ""        # keep clones of URL entries here between runs (opt-in; empty = temporary clones)
    repos_file: str = ""         # the project list: MIRROR_REPOS_FILE (set in the shell), else the default


def warn_if_readable_by_others(path):
    """On Linux/macOS, a settings file that other users can read is reported (it may hold tokens and emails)."""
    if os.name == "nt":
        return
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"warning: {path} can be read by other users of this computer; run: chmod 600 \"{path}\"",
              file=sys.stderr)


def load_config(path=None):
    """Load the env file (MIRROR_ENV_FILE overrides the default location) and return a Config.
    Raises envfile.EnvFileError with a readable message for a missing file or missing settings."""
    path = path or env_path()
    envfile.load(path)
    warn_if_readable_by_others(path)
    envfile.require(REQUIRED)
    e = os.environ.get
    return Config(
        work_emails=e("WORK_EMAIL", "").split(),
        personal_name=e("PERSONAL_NAME", ""),
        personal_email=e("PERSONAL_EMAIL", ""),
        expected_remote=e("EXPECTED_REMOTE", ""),
        mirror_dir=os.path.abspath(os.path.expanduser(e("MIRROR_DIR", "") or os.path.join("~", "work-contributions"))),
        ssh_key=e("PERSONAL_SSH_KEY", ""),
        gh_user=e("WORK_GH_USER", ""),
        pr_reviews=e("MIRROR_PR_REVIEWS", "auto").strip().lower() != "off",
        log_dir=e("MIRROR_LOG_DIR", ""),
        clone_cache=os.path.abspath(os.path.expanduser(e("MIRROR_CLONE_CACHE"))) if e("MIRROR_CLONE_CACHE", "").strip()
        else "",
        repos_file=repos_path(),
    )


def env_path():
    """The settings file this process uses: MIRROR_ENV_FILE, else the default."""
    return os.environ.get("MIRROR_ENV_FILE", "").strip() or DEFAULT_ENV_FILE


def repos_path():
    """The project list this process uses: MIRROR_REPOS_FILE, else the default."""
    value = os.environ.get("MIRROR_REPOS_FILE", "").strip()
    return os.path.abspath(os.path.expanduser(value)) if value else REPOS_FILE


def setup_main(argv):
    """`setup`: create the settings file and the project list from the templates (never overwrites), owner-only, and
    print where the settings, the project list and the logs are."""
    import argparse
    argparse.ArgumentParser(prog="mirror.py setup", description=setup_main.__doc__).parse_args(argv)
    for path, template in ((env_path(), ENV_TEMPLATE), (repos_path(), REPOS_TEMPLATE)):
        if os.path.exists(path):
            make_private(path)
            print(f"exists:  {path}")
            continue
        private_dir(os.path.dirname(os.path.abspath(path)))
        with open(template, encoding="utf-8") as f:
            text = f.read()
        write_private(path, text)
        print(f"created: {path}")
    log_dir = os.environ.get("MIRROR_LOG_DIR", "").strip() or envfile.read(env_path()).get("MIRROR_LOG_DIR", "").strip()
    print(f"logs:    {os.path.abspath(os.path.expanduser(log_dir)) if log_dir else LOG_DIR}")
    print("Fill them in, or use the page: mirror.py ui")
    return 0
