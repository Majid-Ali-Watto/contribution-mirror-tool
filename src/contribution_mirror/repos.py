"""Which projects to scan: the repos file (paths and URLs), folder scanning, and grouping by host."""
import json
import os
import sys

from .gitutil import origin_url
from .logsetup import log, warn
from .remotes import is_url, repo_key, repo_path, scope_of
from .settings import SIBLINGS

def load_repos_file(path):
    """Projects from a JSON file: {"repos": [...]}. Each entry is a local clone path OR a git URL (ssh or https;
    "remote_repos" is still accepted for URLs). No directory walking.
    Returns (local repo paths, URLs to scan from a temporary clone)."""
    with open(path) as f:
        cfg = json.load(f)
    listed = cfg["repos"]
    repos, urls = [], []
    for r in listed:
        if is_url(r):
            urls.append(r.strip())
            continue
        r = os.path.abspath(os.path.expanduser(r))
        if not os.path.exists(os.path.join(r, ".git")):
            warn(f"skipping '{r}' (not a git repo)")
        elif r not in repos:
            repos.append(r)
    return repos, list(dict.fromkeys(urls + [u.strip() for u in cfg.get("remote_repos", [])]))

def find_repos(roots):
    for root in roots:
        if os.path.isdir(os.path.join(root, ".git")):
            yield root
            continue
        for dp, dn, _ in os.walk(root):
            if ".git" in dn:
                yield dp
                dn[:] = []  # don't descend into repos
            dn[:] = [d for d in dn if d not in ("node_modules", ".git")]

def select_sources(src, repos_file, mirror):
    """Local repo paths and URL entries to scan. Precedence: --src folders > the repos file > every sibling
    folder (only if the repos file does not exist). The mirror itself is never scanned."""
    remotes = []
    if src:
        repos = [os.path.abspath(r) for r in find_repos(src)]
    elif os.path.exists(repos_file):
        repos, remotes = load_repos_file(repos_file)
    else:
        repos = [os.path.abspath(r) for r in find_repos([SIBLINGS])]
    if any(mirror == r or mirror.startswith(r + os.sep) for r in repos):
        log.error("mirror must not be inside a source repo")
        sys.exit("mirror must not be inside a source repo")
    repos = [r for r in repos if r != mirror]
    if remotes:  # a URL that is already cloned locally and listed would be counted twice
        local_keys = {repo_key(origin_url(r)) for r in repos}
        remotes = [u for u in remotes if repo_key(u) not in local_keys]
    return repos, remotes


def group_by_scope(repos, remotes):
    """{scope: [owner/repo, ...]} of everything listed (scope = host, or host/organisation on Azure DevOps).
    Used to pick the account that has your commits on each host."""
    by_scope = {}
    for u in [origin_url(r) for r in repos] + remotes:
        if u and is_url(u):
            by_scope.setdefault(scope_of(u), []).append(repo_path(u))
    return {s: list(dict.fromkeys(v)) for s, v in by_scope.items()}
