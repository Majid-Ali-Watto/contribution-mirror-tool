"""Running git: streaming commit dates out of a repo, and the environment for talking to work remotes."""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import Counter

def git(repo, *args, env=None, check=True):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, check=check).stdout

def git_lines(repo, *args, env=None):
    """Yield stdout lines of a git command as they are produced (constant memory). A failing git command raises
    RuntimeError with git's own message: an error must never look like 'no commits'."""
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as err:
        p = subprocess.Popen(["git", "-C", repo, *args], stdout=subprocess.PIPE, stderr=err, text=True,
                             encoding="utf-8", errors="replace", env=env)
        try:
            yield from (line.rstrip("\n") for line in p.stdout)
        finally:
            p.stdout.close()
            rc = p.wait()
        if rc != 0:
            err.seek(0)
            lines = [l for l in err.read().strip().splitlines() if l.strip()]
            raise RuntimeError(f"git {args[0]} failed: {' | '.join(lines[-2:]) if lines else f'exit {rc}'}")

def source_dates(repo, emails, env=None):
    """Author dates of matching commits. `git log --all` lists every commit once, so no dedupe is needed.
    Lazy fetching is disabled: all commits are local, so this never needs the network (or credentials)."""
    c = Counter()
    env = {**(env or os.environ), "GIT_NO_LAZY_FETCH": "1"}
    for line in git_lines(repo, "log", "--all", "--format=%ae %aI", env=env):
        email, date = line.split(" ", 1)
        if email.lower() in emails:
            c[date] += 1
    return c

def work_env():
    """Environment for git calls that talk to the WORK remotes: the caller's own git/ssh/credential setup is used
    unchanged. Only prompting is disabled, so an unattended run fails fast with a warning instead of hanging."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

def token_env(auth_header, host):
    """work_env() plus an HTTP auth header for `host` passed through the environment (not the command line, so
    it is not visible in `ps`). `auth_header` is the ready Authorization value, e.g. 'Basic ...' (see
    Provider.git_header: every host wants the token in a slightly different shape)."""
    env = work_env()
    n = int(env.get("GIT_CONFIG_COUNT") or 0)
    env["GIT_CONFIG_COUNT"] = str(n + 1)
    env[f"GIT_CONFIG_KEY_{n}"] = f"http.https://{host}/.extraheader"
    env[f"GIT_CONFIG_VALUE_{n}"] = f"Authorization: {auth_header}"
    return env

def run_git(args, env, timeout):
    """Run git; returns None on success or the last line of its error output."""
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s"
    if r.returncode == 0:
        return None
    lines = [l.strip() for l in r.stderr.strip().splitlines() if l.strip()]
    return " | ".join(lines[-2:]) if lines else f"exit {r.returncode}"

def origin_url(repo):
    return subprocess.run(["git", "-C", repo, "config", "--get", "remote.origin.url"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()


def rmtree(path):
    """Delete a folder tree. git marks its pack files read-only, which makes a plain rmtree fail on Windows and
    leave the temporary clone behind, so read-only entries are made writable and retried."""
    def retry(func, p, *_):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)
