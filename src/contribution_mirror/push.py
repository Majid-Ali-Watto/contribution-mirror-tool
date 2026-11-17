"""Pushing the mirror to your personal repo: safety check, first-run remote, nothing-to-push shortcut, optional
ssh key, and an https + token fallback when your normal git setup cannot push."""
import os
import subprocess

from .credentials import helper_token
from .gitutil import run_git, token_env, work_env
from .providers import Token, host_of, provider_for
from .remotes import https_url, real_host, repo_key, repo_path


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


def _push_env(ssh_key):
    """Your own git setup, plus (only if configured) one specific ssh key for this push."""
    env = work_env()
    if ssh_key:
        key = ssh_key.replace("\\", "/")                      # git runs this through its own sh, even on Windows
        env["GIT_SSH_COMMAND"] = f'ssh -i "{key}" -o IdentitiesOnly=yes -o BatchMode=yes'
    return env


def _push_with_token(cfg):
    """Last resort: https push with a token of the account that owns the mirror repo (gh CLI account named like
    the owner, a conventional env var, or the git credential helper). Returns an error string or None."""
    host = real_host(cfg.expected_remote)
    provider = provider_for(host)
    owner = repo_path(cfg.expected_remote).split("/")[0]
    tokens = list(provider.cli_tokens(host, owner))
    tokens += [Token(v, os.environ[v]) for v in provider.env_vars if os.environ.get(v, "").strip()]
    helper = helper_token(host)
    tokens += [helper] if helper else []
    last = "no token found (log in with the host's CLI, or set up ssh or an https credential helper)"
    for tok in tokens:
        env = token_env(provider.git_header(tok), host_of(host))
        last = run_git(["-C", cfg.mirror_dir, "push", https_url(cfg.expected_remote), "main:main"], env, 300)
        if last is None:
            _git(cfg.mirror_dir, "update-ref", "refs/remotes/origin/main", "main")
            return None
    return last


def push_mirror(cfg):
    """Push `main` of the mirror. Returns 'pushed' or 'nothing to push'; raises PushError otherwise.
    Refuses to push anywhere but the configured personal repo (compared as owner/repo, so ssh, https, tokens in
    the URL and ssh host aliases are all the same repo)."""
    mirror = cfg.mirror_dir
    if _git(mirror, "remote", "get-url", "origin")[0] != 0:          # first run: connect the mirror to your repo
        code, _, err = _git(mirror, "remote", "add", "origin", cfg.expected_remote)
        if code != 0:
            raise PushError(f"cannot set the mirror's remote: {err}")
    remote = _git(mirror, "remote", "get-url", "origin")[1]
    if repo_key(remote) != repo_key(cfg.expected_remote):
        raise PushError(f"refusing to push: origin is '{remote}', expected '{cfg.expected_remote}'")
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
    raise PushError(f"push failed: {err.splitlines()[-1] if err else 'unknown error'} | "
                    f"https fallback: {fallback}")
