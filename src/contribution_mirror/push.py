"""Pushing the mirror to your personal repo: safety check, first-run remote, nothing-to-push shortcut, optional
ssh key, and an https + token fallback when your normal git setup cannot push. Also fetching the personal repo
first, so a second computer never duplicates what is already there."""
import os
import subprocess

from .credentials import env_tokens, helper_token
from .gitutil import run_git, token_env, work_env
from .httpapi import Api
from .logsetup import redact
from .providers import host_of, provider_for
from .remotes import https_url, is_url, real_host, repo_key, repo_path

# Hosts where a personal repo's owner is the account's login, so a token can be checked against it before use.
OWNER_IS_LOGIN = ("github", "gitlab", "gitea")


class PushError(RuntimeError):
    pass


def mirror_host_note(expected_remote):
    """A one-line heads-up when the mirror is on a known host other than GitHub, or None. Pushing works on any host
    (it is plain git), but only GitHub is verified to build its contribution graph from commit dates; other hosts
    date activity when it happens, so history may appear on the day it is pushed (and some have no such graph)."""
    provider = provider_for(real_host(expected_remote), use_probe=False)
    if provider.name in ("github", "generic"):
        return None
    return (f"note: your mirror is on {provider.label}. Only GitHub is verified to build the contribution graph from "
            "commit dates; other hosts date activity when it happens, so history may appear on the day it is pushed "
            "(and some hosts have no such graph). See docs/hosts.md.")


def _git(mirror, *args, env=None):
    r = subprocess.run(["git", "-C", mirror, *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def sh_quote(value):
    """`value` as one single-quoted shell word: git runs GIT_SSH_COMMAND through sh (its own sh on Windows), where
    nothing inside single quotes is expanded ($, backticks, double quotes), and a ' is written as '\\''."""
    return "'" + value.replace("'", "'\\''") + "'"


def _push_env(ssh_key):
    """Your own git setup, plus (only if configured) one specific ssh key for this push."""
    env = work_env()
    if ssh_key:
        key = ssh_key.replace("\\", "/")                      # forward slashes work for ssh on every system
        env["GIT_SSH_COMMAND"] = f"ssh -i {sh_quote(key)} -o IdentitiesOnly=yes -o BatchMode=yes"
    return env


def _token_owns(provider, host, tok, owner):
    """True if `tok` belongs to the account that owns the mirror, so the fallback never pushes with a work account
    that merely has access. Only asked on hosts where a personal repo's owner is the login (see _mirror_tokens). A gh
    CLI token asked for by the owner's name is that account already."""
    if tok.source.lower() == f"gh:{owner}".lower():
        return True
    try:
        login = provider.whoami(Api(provider.api_url(host), provider.headers(tok)))
    except Exception:  # noqa: BLE001 - an invalid token is simply not used
        return False
    return login.lower() == owner.lower()


def _mirror_tokens(cfg):
    """(provider, host, owner, tokens) for the personal repo. Where the owner is the login (GitHub, GitLab, Gitea):
    the gh CLI account named like the owner, conventional variables (only for a host whose kind is known) and the git
    credential helper, each checked against the owner before use. Elsewhere a token's account cannot be checked, so
    only the git credential helper's entry for that host is used: what a plain `git push` would use anyway."""
    host = real_host(cfg.expected_remote)
    provider = provider_for(host)
    owner = repo_path(cfg.expected_remote).split("/")[0]
    tokens = list(provider.cli_tokens(host, owner)) + env_tokens(provider, host) if provider.name in OWNER_IS_LOGIN \
        else []
    helper = helper_token(host)
    tokens += [helper] if helper else []
    return provider, host, owner, tokens


def owner_token(cfg):
    """(provider, host, owner, token) with a token of the account that owns the mirror repo, or None. Only on hosts
    where that can be checked (OWNER_IS_LOGIN); used to read the account's own settings (doctor), never to push."""
    provider, host, owner, tokens = _mirror_tokens(cfg)
    if provider.name not in OWNER_IS_LOGIN:
        return None
    tok = next((t for t in tokens if _token_owns(provider, host, t, owner)), None)
    return (provider, host, owner, tok) if tok else None


def _with_token(cfg, args, timeout):
    """Run git `args` (inside the mirror) over https with a token of the account that owns the mirror repo. Returns
    an error string or None."""
    provider, host, owner, tokens = _mirror_tokens(cfg)
    last = "no token found (log in with the host's CLI, or set up ssh or an https credential helper)"
    for tok in tokens:
        if provider.name in OWNER_IS_LOGIN and not _token_owns(provider, host, tok, owner):
            last = f"the token from {tok.source} is not the account that owns '{owner}', not used"
            continue
        env = token_env(provider.git_header(tok), host_of(host))
        last = run_git(["-C", cfg.mirror_dir, *args], env, timeout)
        if last is None:
            return None
    return last


def _push_with_token(cfg):
    """Last resort: https push with a token of the account that owns the mirror repo. Returns an error or None."""
    err = _with_token(cfg, ["push", https_url(cfg.expected_remote), "main:main"], 300)
    if err is None:
        _git(cfg.mirror_dir, "update-ref", "refs/remotes/origin/main", "main")
    return err


def check_remote(remote):
    """Can your normal git setup reach the mirror repo? (`git ls-remote`, never prompts). Returns (ok, message)."""
    if not remote:
        return False, "enter the mirror repo first"
    if not is_url(remote) and not os.path.exists(os.path.expanduser(remote)):
        return False, "not a URL, and no such local path"
    try:
        r = subprocess.run(["git", "ls-remote", "--heads", "--", remote], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30, env=work_env(batch_ssh=True),
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, "no answer within 30 s"
    except OSError as e:
        return False, f"git could not be started: {e}"
    if r.returncode == 0:
        branches = len([l for l in r.stdout.splitlines() if l.strip()])
        return True, "reachable" + (" (empty repo: the first sync fills it)" if branches == 0 else "")
    lines = [l for l in r.stderr.strip().splitlines() if l.strip()]
    return False, redact(" | ".join(lines[-2:]) if lines else f"git exit {r.returncode}") + \
        " (the push may still work with a token found automatically)"


def ensure_remote(cfg):
    """Connect the mirror to your personal repo on the first run, and refuse any other repo (compared as owner/repo,
    so ssh, https, tokens in the URL and ssh host aliases are all the same repo)."""
    mirror = cfg.mirror_dir
    if _git(mirror, "remote", "get-url", "origin")[0] != 0:          # first run: connect the mirror to your repo
        code, _, err = _git(mirror, "remote", "add", "origin", cfg.expected_remote)
        if code != 0:
            raise PushError(f"cannot set the mirror's remote: {redact(err)}")
    remote = _git(mirror, "remote", "get-url", "origin")[1]
    if repo_key(remote) != repo_key(cfg.expected_remote):
        raise PushError(f"refusing to push: origin is '{redact(remote)}', expected '{redact(cfg.expected_remote)}'")


def fetch_mirror(cfg):
    """Fetch `main` of your personal repo into refs/remotes/origin/main. Returns None on success (also when the repo
    is still empty) or an error string; the caller carries on (the push will tell if something is really wrong)."""
    refspec = "+refs/heads/main:refs/remotes/origin/main"
    code, _, err = _git(cfg.mirror_dir, "fetch", "--quiet", "origin", refspec, env=_push_env(cfg.ssh_key))
    if code == 0 or "couldn't find remote ref" in err:
        return None
    fallback = _with_token(cfg, ["fetch", "--quiet", https_url(cfg.expected_remote), refspec], 300)
    if fallback is None or "couldn't find remote ref" in fallback:
        return None
    return redact(f"{err.splitlines()[-1] if err else 'unknown error'} | https fallback: {fallback}")


def push_mirror(cfg):
    """Push `main` of the mirror. Returns 'pushed' or 'nothing to push'; raises PushError otherwise.
    Refuses to push anywhere but the configured personal repo."""
    mirror = cfg.mirror_dir
    ensure_remote(cfg)
    local = _git(mirror, "rev-parse", "main")[1]
    tracked = _git(mirror, "rev-parse", "-q", "--verify", "refs/remotes/origin/main")[1]
    if local and local == tracked:
        return "nothing to push"
    code, _, err = _git(mirror, "push", "origin", "main", env=_push_env(cfg.ssh_key))
    if code == 0:
        return "pushed"
    fallback = _push_with_token(cfg)
    if fallback is None:
        return "pushed"
    raise PushError(redact(f"push failed: {err.splitlines()[-1] if err else 'unknown error'} | "
                           f"https fallback: {fallback}"))
