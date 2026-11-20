"""Which projects to scan: the project list (repos.json: paths and URLs), folder scanning, and grouping by host.
The command line and the setup page read, check and write the list through the same functions here."""
import functools
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from .fsutil import write_private
from .gitutil import origin_url
from .logsetup import warn
from .remotes import is_url, repo_key, repo_path, scope_of, url_host
from .settings import REPOS_TEMPLATE, command_name

SKIP_DIRS = ("node_modules",)          # never searched for repos (hidden folders are skipped too)


class ReposFileError(ValueError):
    """The project list cannot be used (missing, bad JSON, or not {"repos": [...]})."""


def origin_urls(repos):
    """{repo: its origin URL ('' if none)}, looked up in parallel (one git process each)."""
    if not repos:
        return {}
    with ThreadPoolExecutor(min(8, len(repos))) as ex:
        return dict(zip(repos, ex.map(origin_url, repos)))


@functools.lru_cache(maxsize=None)
def example_entries():
    """The entries of the project-list template: placeholders, never real projects (so never cloned or scanned)."""
    try:
        with open(REPOS_TEMPLATE, encoding="utf-8") as f:
            return frozenset(e.strip() for e in json.load(f).get("repos", []) if isinstance(e, str))
    except (OSError, ValueError, AttributeError):
        return frozenset()


def is_example(entry):
    return entry.strip() in example_entries()


def is_repo(path):
    """A git working tree: a `.git` folder, or a `.git` file (a worktree or submodule)."""
    return os.path.exists(os.path.join(path, ".git"))


def read_entries(path):
    """(entries as written, whether the older "remote_repos" key is used). Raises ReposFileError."""
    try:
        with open(path, encoding="utf-8-sig") as f:              # -sig: a BOM from Windows editors is fine too
            cfg = json.load(f)
    except OSError as e:
        raise ReposFileError(f"{path} cannot be read: {e.strerror or e}") from None
    except ValueError as e:
        raise ReposFileError(f"{path} is not valid JSON: {e}") from None
    listed = cfg.get("repos") if isinstance(cfg, dict) else None
    extra = cfg.get("remote_repos", []) if isinstance(cfg, dict) else []
    if not isinstance(listed, list) or not isinstance(extra, list) \
            or not all(isinstance(x, str) for x in listed + extra):
        raise ReposFileError(f'{path} must look like {{"repos": ["/path/to/clone", "git@host:org/repo.git"]}}')
    return list(dict.fromkeys(listed + extra)), bool(extra)


def save_entries(path, entries):
    """Write the project list (owner-only, atomically)."""
    write_private(path, json.dumps({"repos": entries}, indent=2, ensure_ascii=False) + "\n")


def clean_entries(entries):
    """Submitted entries -> the list to save: trimmed, one line each, no blanks or duplicates. Raises ValueError."""
    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
        raise ValueError("expected a list of paths and URLs")
    out = []
    for e in entries:
        e = e.strip()
        if "\n" in e or "\r" in e:
            raise ValueError("an entry must be one line")
        if e and e not in out:
            out.append(e)
    return out


def check_entry(entry):
    """What the page shows next to one entry. Never touches the network."""
    if is_example(entry):
        return {"entry": entry, "kind": "url" if is_url(entry) else "path", "ok": False,
                "detail": "example from the template: replace it with one of your projects"}
    if is_url(entry):
        return {"entry": entry, "kind": "url", "ok": True, "detail": url_host(entry) or "URL"}
    path = os.path.abspath(os.path.expanduser(entry))
    if is_repo(path):
        return {"entry": entry, "kind": "path", "ok": True, "detail": "git repo"}
    detail = "not a git repo" if os.path.isdir(path) else "folder not found"
    return {"entry": entry, "kind": "path", "ok": False, "detail": detail}


def load_repos_file(path):
    """Projects from the list: each entry is a local clone path OR a git URL (ssh or https; the older "remote_repos"
    list of URLs is still accepted, with a note). No directory walking.
    Returns (local repo paths, URLs to scan from a temporary clone)."""
    entries, legacy = read_entries(path)
    examples = [e for e in entries if is_example(e)]
    entries = [e for e in entries if not is_example(e)]
    if examples and not entries:
        raise ReposFileError(f"{path} still lists only the template's examples: replace them with your own projects "
                             f"(in the file, or on the page: {command_name()} ui)")
    if examples:
        warn(f"skipping {len(examples)} example entr{'y' if len(examples) == 1 else 'ies'} from the template in {path}: "
             "remove them")
    if legacy:
        print(f'note: {path} uses the older "remote_repos" key. It still works; URLs can simply go in "repos" '
              'together with paths.')
    repos, urls = [], []
    for r in entries:
        if is_url(r):
            urls.append(r.strip())
            continue
        r = os.path.abspath(os.path.expanduser(r))
        if not is_repo(r):
            warn(f"skipping '{r}' (not a git repo)")
        elif r not in repos:
            repos.append(r)
    return repos, list(dict.fromkeys(urls))


def walk_repos(root):
    """Git repos at or under `root`, not inside each other, skipping hidden folders and node_modules."""
    for dp, dn, _ in os.walk(root):
        if is_repo(dp):
            yield dp
            dn[:] = []                                           # don't descend into repos
        else:
            dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith("."))


def find_repos(roots):
    for root in roots:
        yield from walk_repos(root)


def find_under(folder, limit=300, seconds=4.0):
    """Git repos under `folder` for the page, stopping after `limit` results or a few seconds so a huge folder cannot
    hang it. Returns (paths, complete). Raises ValueError for a missing folder."""
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        raise ValueError(f"folder not found: {folder}")
    deadline, found = time.monotonic() + seconds, []
    for repo in walk_repos(folder):
        found.append(repo)
        if len(found) >= limit or time.monotonic() > deadline:
            return found, False
    return found, True


def _inside(path, folder):
    path, folder = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(folder))
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def select_sources(src, repos_file, mirror):
    """Local repo paths and URL entries to scan: the --src folders if given, else the project list. The mirror itself
    is never scanned, and a mirror inside a source repo is refused (symlinks resolved)."""
    remotes = []
    if src:
        repos = [os.path.abspath(r) for r in find_repos(src)]
    elif os.path.exists(repos_file):
        repos, remotes = load_repos_file(repos_file)
    else:
        raise ReposFileError(f"no project list: {repos_file} does not exist. Create it with `{command_name()} setup` "
                             f"(or on the page, `{command_name()} ui`), or name folders with --src")
    if any(_inside(mirror, r) and os.path.realpath(mirror) != os.path.realpath(r) for r in repos):
        raise ReposFileError("the mirror must not be inside a source repo")
    repos = [r for r in repos if os.path.realpath(r) != os.path.realpath(mirror)]
    if remotes:  # a URL that is already cloned locally and listed would be counted twice
        local_keys = {repo_key(u) for u in origin_urls(repos).values()}
        remotes = [u for u in remotes if repo_key(u) not in local_keys]
    return repos, remotes


def group_by_scope(repos, remotes, origins=None):
    """{scope: [owner/repo, ...]} of everything listed (scope = host, or host/organisation on Azure DevOps).
    Used to pick the account that has your commits on each host. `origins` = origin_urls(repos), if known."""
    by_scope = {}
    origins = origin_urls(repos) if origins is None else origins
    for u in [origins.get(r, "") for r in repos] + remotes:
        if u and is_url(u):
            by_scope.setdefault(scope_of(u), []).append(repo_path(u))
    return {s: list(dict.fromkeys(v)) for s, v in by_scope.items()}
