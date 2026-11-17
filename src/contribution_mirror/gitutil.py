"""Running git: streaming commit dates out of a repo, and the environment for talking to work remotes."""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import Counter

# Variables that hold the tokens the user chose for this tool. They are kept out of every other program's environment
# (git, ssh, credential helpers, vendor CLIs, notifications): only this process needs them.
_SECRET_VARS = re.compile(r"^WORK_([A-Z_]+_)?TOKEN$")

# What a commit is read as: hash (only to recognise the same commit in two repos during a run; never stored or
# written anywhere), author email, author date.
LOG_FORMAT = "--format=%H %ae %aI"
# Branches, tags and remote-tracking branches: the refs that hold real work. `--all` would add refs/stash (your latest
# stash, counted as 2-3 commits), refs/notes and refs/original (filter-branch backups).
LOG_REFS = ("--branches", "--tags", "--remotes")


def child_env(base=None):
    """A copy of the environment for another program, without this tool's own token variables."""
    return {k: v for k, v in (os.environ if base is None else base).items() if not _SECRET_VARS.match(k)}


def canonical_date(iso):
    """One spelling per instant and offset. git prints a UTC date as `...Z` since 2.45 and as `...+00:00` before, so
    both are made `Z`: otherwise the mirror and the sources disagree about the same date and every run adds it again."""
    if iso.endswith(("+00:00", "-00:00")):
        return iso[:-6] + "Z"
    return iso


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


def source_commits(repo, emails, env=None):
    """{commit hash: author date} of the commits authored with one of `emails` (lowercase). git does the filtering
    (`--author`, fixed strings, any case, the raw email not the .mailmap one), so only your commits reach Python; the
    exact email is checked again here. Lazy fetching is disabled: all commits are local, so this never needs the
    network (or credentials)."""
    out = {}
    env = {**(env or os.environ), "GIT_NO_LAZY_FETCH": "1"}
    authors = [f"--author=<{e}>" for e in sorted(emails)]
    for line in git_lines(repo, "log", *LOG_REFS, "--no-use-mailmap", "--no-show-signature",
                          "--fixed-strings", "--regexp-ignore-case", *authors, LOG_FORMAT, env=env):
        sha, _, rest = line.partition(" ")
        email, _, date = rest.rpartition(" ")
        if date and email.lower() in emails:
            out[sha] = canonical_date(date)
    return out


def source_dates(repo, emails, env=None):
    """Counter of author dates of matching commits (each commit once)."""
    return Counter(source_commits(repo, emails, env).values())


def user_ssh_command(repo=None):
    """True if the user's git config chooses an ssh command (core.sshCommand) for `repo`, or, without a repo (a
    temporary clone), in the global or system config only: never the config of whatever folder we happen to run in."""
    if repo:
        cmds = [["git", "-C", repo, "config", "--get", "core.sshCommand"]]
    else:
        cmds = [["git", "config", scope, "--get", "core.sshCommand"] for scope in ("--global", "--system")]
    try:
        return any(subprocess.run(c, capture_output=True, timeout=30).returncode == 0 for c in cmds)
    except (OSError, subprocess.TimeoutExpired):
        return False


def work_env(repo=None, batch_ssh=False):
    """Environment for git calls that talk to the WORK remotes: the caller's own git/ssh/credential setup is used,
    but nothing may prompt, so an unattended run fails fast with a warning instead of hanging or opening a window.
    With batch_ssh (the parallel fetches and clones) ssh may not ask for a passphrase or a host key either, since
    several fetches would ask at once: use ssh-agent. A user's own ssh command (GIT_SSH_COMMAND, GIT_SSH or
    core.sshCommand) is never replaced."""
    env = child_env()
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    if batch_ssh and not (env.get("GIT_SSH_COMMAND") or env.get("GIT_SSH")) and not user_ssh_command(repo):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return env


def token_env(auth_header, host, base=None):
    """work_env() (or `base`) plus an HTTP auth header for `host` passed through the environment (not the command
    line, so it is not visible in `ps`). `auth_header` is the ready Authorization value, e.g. 'Basic ...' (see
    Provider.git_header: every host wants the token in a slightly different shape)."""
    env = dict(base) if base is not None else work_env()
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
