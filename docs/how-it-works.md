# How it works

## What it reads and writes

- **Reads** (from work repos): the author email and author date of each commit, plus its hash, which is kept only in memory during the run so that a commit found in two listed repos (a fork, or the same project cloned twice) counts once. No messages or paths are read, and the hash is never stored or written.
- **Reads** (from your work host's REST API, using a login found automatically): only the timestamp of each pull / merge request you opened and each review you made in the last 365 days, in the repos of the owners (organisations, users, workspaces) of the projects you listed (on GitLab: the listed projects), as far as each host provides them: see [hosts](hosts.md). Nothing else is stored (no titles, repos, PR numbers).
- **Never copies** into the mirror: code, file names, paths, commit messages, hashes or repo names.
- **Writes**: empty commits with the message `mirror`, your personal name and email, and the original date, into the mirror repo only.
- **Never modifies** your work: before scanning it runs `git fetch` in the work clones, which only updates remote-tracking refs (no branches, working tree, uncommitted changes or `--prune`). Repos that are not cloned locally are cloned into a temp folder as a bare, tree-less clone (commit metadata only: a few hundred KB for a small project, tens of MB for a long history, and everything if the host does not support tree-less clones), scanned, and **deleted right after**. Set `MIRROR_CLONE_CACHE` to keep those clones between runs instead and only fetch new commits ([configuration](configuration.md)).
- Repo folder names appear **only** in the local log, never in the mirror repo.

## The steps of a run

`mirror.py sync` loads your settings (`config.py`), runs the core (`cli.py`, below), then pushes (`push.py`). The core runs
these in order; each step lives in its own module:

Before the core, `sync` fetches your personal repo and builds on what is already there (`mirror_repo.py`), so running it on a second computer, or with a new mirror folder, never adds a date twice. Only one run at a time is allowed (`runlock.py`).

1. **Choose projects** (`repos.py`): your `config/repos.json` entries, paths and URLs of any host.
2. **Find a login** (`credentials.py`, `providers/`): lazily, once per host, only when needed.
3. **Read commit dates** (`scan.py`, `gitutil.py`): fetch each local clone, or make a temporary clone for each URL, in parallel; stream `git log` line by line.
4. **Read the mirror** (`mirror_repo.py`): which timestamps are already in it.
5. **PRs and reviews** (`activity.py`, `providers/`, `httpapi.py`, `state.py`): ask each host in its own API, only for the window that is not already covered.
6. **Compare** (`report.py`): what is new, and which source it came from.
7. **Write** (`mirror_repo.py`): one `git fast-import` creates all the new commits. Then the push (`push.py`).

## What gets counted

**PRs and reviews** (on whenever a login that can see your projects is found; `--no-api` turns it off): the pull / merge requests you opened and the reviews you made, last 365 days, for the hosts that expose them ([hosts](hosts.md)). On GitHub that is PRs opened (search, by creation time) and every review submitted on other people's PRs; GitHub hides private-org items from the contributions GraphQL API, so search and REST are used. Each becomes one empty commit, deduplicated by timestamp on re-runs. Mirrored items older than 365 days are never removed, so the mirror keeps them.

**Commits:** `git log --branches --tags --remotes` is used (merge commits included, because GitHub counts merges you author on the default branch). Not `--all`: that would also count your latest stash (as 2-3 commits) and backup refs. So these are included:

- Local branches, tags, and remote-tracking branches. The work repos are fetched at the start of every run, so stale clones are not a problem. If a fetch fails (offline, no access), a warning is logged and the local data is used.
- Unmerged feature-branch commits, which GitHub itself would not count in the work repo.
- Merge commits (git lists each commit once, so none is counted twice).
- A commit that is in several listed repos (a fork and its parent, the same project cloned twice) is counted once.
- Rebased or cherry-picked commits have different hashes, so they can count twice.
- The mirror only grows. Deleting or rewriting a branch later does not remove commits already mirrored.

## Caveats

- Commits count on GitHub only if the author email is verified on your account, they are on the repo's default branch, and the repo is not a fork.
- The graph can take a few minutes to refresh.
- Check your employer's policy before using this. Commit timestamps are still information about work done for them.
- Push with your **personal** credentials only, not a work-managed account.
