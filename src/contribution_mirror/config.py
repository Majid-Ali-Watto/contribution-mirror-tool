"""The user's settings, read from the .env settings file (and the real environment): who they are and where the mirror
is. Also `setup`: create the settings files from the templates and show where everything lives."""
import os
import re
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass

from . import envfile
from .fsutil import make_private, private_dir, write_private
from .timeutil import parse_day
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
    since: str = ""              # MIRROR_SINCE: only dates on or after this day (YYYY-MM-DD); empty = no limit
    until: str = ""              # MIRROR_UNTIL: only dates on or before this day; empty = no limit


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
    limits = {}
    for key in ("MIRROR_SINCE", "MIRROR_UNTIL"):
        value = e(key, "").strip()
        try:
            limits[key] = parse_day(value).isoformat() if value else ""
        except ValueError as err:
            raise envfile.EnvFileError(f"{key} in {path}: {err}") from None
    if limits["MIRROR_SINCE"] and limits["MIRROR_UNTIL"] and limits["MIRROR_SINCE"] > limits["MIRROR_UNTIL"]:
        raise envfile.EnvFileError(f"MIRROR_SINCE is after MIRROR_UNTIL in {path}")
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
        since=limits["MIRROR_SINCE"],
        until=limits["MIRROR_UNTIL"],
    )


# ---------------------------------------------------------------------------------------------------- profiles
# A profile is a second (third...) setup: its own settings file and project list, e.g. one per employer. The default
# profile is the usual .env / repos.json; profile NAME lives in profiles/NAME/ next to them. Every profile shares the
# log folder (so one run lock covers them all; each keeps its own api-state-NAME.json). A process works on one
# profile, chosen with `mirror.py --profile NAME ...` (MIRROR_PROFILE); scheduled runs go through every profile.
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")


def check_profile_name(name):
    """`name` if it can name a profile (letters, digits, - and _; '' = the default), else ValueError."""
    name = (name or "").strip()
    if name and not PROFILE_RE.match(name):
        raise ValueError(f"'{name}' cannot name a profile: use up to 40 letters, digits, - and _")
    return name


def current_profile():
    """The profile this process works on: MIRROR_PROFILE, or '' (the default profile)."""
    return check_profile_name(os.environ.get("MIRROR_PROFILE", ""))


def profile_paths(name, env_default=None, repos_default=None):
    """(settings file, project list) of profile `name` ('' = the default ones)."""
    env_default, repos_default = env_default or DEFAULT_ENV_FILE, repos_default or REPOS_FILE
    if not name:
        return env_default, repos_default
    return (os.path.join(os.path.dirname(env_default), "profiles", name, ".env"),
            os.path.join(os.path.dirname(repos_default), "profiles", name, "repos.json"))


def list_profiles(env_default=None):
    """Names of the profiles that have a settings file, the default one ('') first."""
    env_default = env_default or DEFAULT_ENV_FILE
    names = [""] if os.path.isfile(env_default) else []
    folder = os.path.join(os.path.dirname(env_default), "profiles")
    if os.path.isdir(folder):
        names += sorted(n for n in os.listdir(folder)
                        if PROFILE_RE.match(n) and os.path.isfile(os.path.join(folder, n, ".env")))
    return names


@contextmanager
def profile_environment(name):
    """Work on profile `name` inside the block: loading its settings puts them into the environment, so the
    environment is restored afterwards and the next profile starts clean (used by scheduled runs)."""
    saved = dict(os.environ)
    try:
        os.environ.pop("MIRROR_PROFILE", None)
        if name:
            os.environ["MIRROR_PROFILE"] = name
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def env_path():
    """The settings file this process uses: MIRROR_ENV_FILE, else the current profile's."""
    return os.environ.get("MIRROR_ENV_FILE", "").strip() or profile_paths(current_profile())[0]


def repos_path():
    """The project list this process uses: MIRROR_REPOS_FILE, else the current profile's."""
    value = os.environ.get("MIRROR_REPOS_FILE", "").strip()
    return os.path.abspath(os.path.expanduser(value)) if value else profile_paths(current_profile())[1]


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
    others = [n for n in list_profiles() if n]
    if others:
        print(f"profiles: default, {', '.join(others)}  (choose one with `mirror.py --profile NAME ...`)")
    print("Fill them in, or use the page: mirror.py ui")
    return 0
