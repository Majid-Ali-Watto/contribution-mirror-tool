"""Imported first by every test: puts src/ on the import path and gives tests a way to build throwaway git repos."""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# Never read the developer's real git config in tests: point it at an empty file (os.devnull is not reliable for
# git on every platform).
_EMPTY_CONFIG = os.path.join(tempfile.mkdtemp(prefix="mirror-test-gitconfig-"), "empty")
open(_EMPTY_CONFIG, "w").close()
atexit.register(shutil.rmtree, os.path.dirname(_EMPTY_CONFIG), True)
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": _EMPTY_CONFIG, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def make_repo(path, commits, merge=None):
    """Create a git repo at `path` with empty commits: commits = [(author_email, iso_date), ...].
    merge = (author_email, iso_date) adds a --no-ff merge commit authored by that email."""
    os.makedirs(path, exist_ok=True)
    git = lambda *a, env=None: subprocess.run(["git", "-C", path, "-c", "commit.gpgsign=false", *a],
                                              capture_output=True, text=True, check=True, env=env or GIT_ENV)
    git("init", "-q", "-b", "main")

    def commit(email, date, msg="x"):
        env = {**GIT_ENV, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date,
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": date}
        git("commit", "-q", "--allow-empty", "-m", msg, env=env)
        return env

    for email, date in commits:
        commit(email, date)
    if merge:
        git("checkout", "-q", "-b", "feature")
        commit("feature@x", "2026-02-01T10:00:00+00:00")
        git("checkout", "-q", "main")
        env = {**GIT_ENV, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": merge[0], "GIT_AUTHOR_DATE": merge[1],
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": merge[0], "GIT_COMMITTER_DATE": merge[1]}
        git("merge", "-q", "--no-ff", "-m", "merge", "feature", env=env)
    return path
