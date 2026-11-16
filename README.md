# contribution-mirror-tool

Copies the **timestamps** of your work commits into a separate personal repo as empty commits, so your personal GitHub contribution graph reflects your work activity without exposing any work code.

## What it reads and writes

- **Reads** (from work repos): the author email and author date of each commit, and nothing else. No hashes, messages or paths are read.
- **Reads** (from the work GitHub account, via the `gh` CLI): only the timestamp of each PR you opened and each PR review you submitted in the last 365 days. Nothing else is stored (no titles, repos, PR numbers).
- **Never copies** into the mirror: code, file names, paths, commit messages, hashes or repo names.
- **Writes**: empty commits with the message `mirror`, your personal name and email, and the original date, into the mirror repo only.
- **Never modifies** your work: before scanning it runs `git fetch` in the work clones, which only updates remote-tracking refs (no branches, working tree, uncommitted changes or `--prune`). Repos that are not cloned locally are cloned into a temp folder as a bare, tree-less clone (commit metadata only, about 100 KB), scanned, and **deleted right after**.

## Files

| File | Purpose |
| --- | --- |
| `mirror.py` | Core script. Dry run by default; `--apply` creates commits. Never pushes. |
| `sync.sh` | Wrapper: runs `mirror.py --apply`, then pushes to the personal mirror remote. |
| `scheduled-sync.sh` | Entry point for the systemd timer: waits for network, runs `sync.sh`, shows desktop notifications. |
| `load-env.sh` | Reads `.env` safely (plain `KEY=VALUE` parsing, the file is never executed as shell). |
| `.env.example` | Template for your private settings. Copy to `.env` (git-ignored). |
| `repos.example.json` | Template for the list of work repos. Copy to `repos.json` (git-ignored). |

## Usage

```bash
./sync.sh            # mirror new commits and push
./sync.sh --dry-run  # preview only: no commits, no push
./sync.sh my-repo other-folder                   # only these sibling folders
./sync.sh --dry-run my-repo                      # preview for one folder
```

Folder names are resolved as siblings of `contribution-mirror-tool` first, then as paths, and are searched recursively. With no names, the repos in `repos.json` are scanned.

Running it again is safe. Only timestamps not already in the mirror are added.

Your settings (work email, personal identity, mirror remote, accounts) are read from `.env`, and your repo list from `repos.json`. Both are **git-ignored**, so nothing private is ever committed. See [Configuration](#configuration).

### Running `mirror.py` directly

```bash
python3 -I mirror.py \
  [--src ../some-repo ../some-folder] \
  --work-email you@company.com \
  --mirror ~/work-contributions \
  --name "Your Name" --email your-verified-personal@email.com \
  [--apply]
```

| Option | Meaning |
| --- | --- |
| `--src` | Optional. Repo(s), or folders containing repos (searched recursively, skipping `node_modules`). **Default: the repos listed in `repos.json`**, or every sibling folder if that file is missing. The mirror repo itself is never scanned. |
| `--repos-file` | Optional. JSON file `{"repos": [paths]}` to use instead of `repos.json`. Listed repos are read directly, with no deeper search. |
| `--work-email` | Your author email(s) in the work repos. Several allowed. |
| `--mirror` | Path of the mirror repo. Created if missing. Must not be inside a source repo. |
| `--name`, `--email` | Author identity for mirror commits. The email must be **verified** on your personal GitHub account. |
| `--apply` | Actually create commits. Without it, nothing is written. |
| `--gh-user` | Optional. `gh` CLI account of your **work** login (`gh auth login` must have been done for it). Also mirrors PR and review timestamps. `sync.sh` sets it from `WORK_GH_USER`. If the lookup fails, a warning is logged and commits are still mirrored. |
| `--full-api` | Optional. Re-scan the whole 365-day PR/review window instead of only the recent days (see Performance). |
| `--no-fetch` | Optional. Skip `git fetch` of the work repos and the temporary clones of `remote_repos` (offline / fastest). |
| `--log-dir` | Optional. Folder for `mirror.log` (default `logs/` here). |

## Automatic runs (systemd user timer)

`contribution-mirror.timer` (in `~/.config/systemd/user/`) runs `scheduled-sync.sh`, which waits for network and then runs `sync.sh`:

- 5 minutes after you log in (i.e. when the laptop is turned on).
- Daily at 18:00 in the timezone set in `OnCalendar` (see the templates below). If the laptop was off then, it runs when it is next on (`Persistent=true`).

Every run shows a desktop notification: **"sync OK"** with the number of new commits (and which repos they came from), or "Up to date, no new commits". If a run fails (GitHub unreachable after ~5 min, or `sync.sh` exits non-zero, e.g. push rejected), a critical **desktop notification** (`notify-send`) is shown and an `ERROR` line is added to `logs/mirror.log`. The next run retries.

Pushes use the key in `PERSONAL_SSH_KEY` (`.env`) explicitly, so no ssh-agent is needed. Fetches and clones of the work repos do not use it: they use your normal `~/.ssh/config` (work key). Re-runs are harmless: already-mirrored timestamps are skipped.

Install (replace `/path/to/contribution-mirror-tool` and the timezone):

```ini
# ~/.config/systemd/user/contribution-mirror.service
[Unit]
Description=Mirror work contribution timestamps to personal repo
[Service]
Type=oneshot
ExecStart="/path/to/contribution-mirror-tool/scheduled-sync.sh"

# ~/.config/systemd/user/contribution-mirror.timer
[Unit]
Description=Run contribution-mirror at login and daily
[Timer]
OnStartupSec=5min
OnCalendar=*-*-* 18:00:00 Your/Timezone
Persistent=true
[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now contribution-mirror.timer
systemctl --user list-timers contribution-mirror.timer   # next run
systemctl --user start contribution-mirror.service       # run now
journalctl --user -u contribution-mirror.service -n 30   # logs
systemctl --user disable --now contribution-mirror.timer # turn off
```

## Choosing repos: `repos.json`

Copy [repos.example.json](repos.example.json) to `repos.json` (git-ignored) and list the exact repo paths in it. Each path must be a git repo (contain `.git`); the script reads it directly and does **not** search inside it or anywhere else.

```json
{
  "repos": [
    "/path/to/your/work-repo-1",
    "/path/to/your/work-repo-2"
  ],
  "remote_repos": [
    "git@github.com:YourOrg/repo-you-have-not-cloned.git"
  ]
}
```

- `~` is expanded. Duplicates are ignored.
- A path that is missing or not a git repo is skipped with a warning (on stderr), the rest still run.
- To add a repo, add its path to the list. No code change needed.
- **Repos you have not cloned:** list their clone URLs under `remote_repos` (use the same host alias as your work remotes, e.g. `git@github-work:YourOrg/repo.git` if you keep a separate work SSH key). Each run clones them into a temp folder, reads only the commit timestamps, and deletes the clone. A URL that matches the `origin` of a listed local repo is skipped, so nothing is counted twice. Both fetches and temp clones use your normal `~/.ssh/config` (work key), never the personal key the timer uses for pushing.
- Precedence: `--src` or folder names passed to `sync.sh` > `repos.json` > scanning all sibling folders (only if `repos.json` does not exist).
- A different file can be used with `mirror.py --repos-file <path>`.

## How `--src` / folder names are scanned

A folder that is itself a git repo is scanned as one repo. A folder that is not a repo is searched recursively and every repo inside it is scanned. E.g. a folder `projects` that is not a repo but contains `app-fe` and `app-api` has both of them scanned. The search does not descend into a repo or into `node_modules`.

## Logs

Every run (manual, `sync.sh` or the timer) appends **one `SUMMARY` line** to `logs/mirror.log`, in addition to printing the full output on screen:

```
2026-11-17 03:10:56,255 INFO SUMMARY mode=apply repos=2 matching=320 already_mirrored=317 created=3 | new per repo: app-fe=3 app-api=0
```

- `mode` is `dry-run` or `apply`. `matching` = commits by your work email, `already_mirrored` = their timestamps already in the mirror, `created` (`would_create` in a dry run) = new commits, and the per-repo list shows where the new ones came from.
- Skipped repos get a `WARNING` line. A failed scheduled run gets an `ERROR` line with the reason and the last lines of the error output, e.g. `ERROR scheduled sync failed: sync.sh exited with code 1 -- ... ; refusing to push: origin is ..., expected ...`.
- Handy: `grep SUMMARY logs/mirror.log`, or `grep "mode=apply.*created=[1-9]" logs/mirror.log` for runs that added commits.
- **Rotation:** at 5 MB the file rolls over to `mirror.log.1`, `.2`, … and a new `mirror.log` starts. The 10 most recent rolled files are kept (about 55 MB at most). One line per run is far below that.
- Repo folder names appear **only** in this local log, never in the mirror repo.
- Another location: `mirror.py --log-dir <folder>`.

## Configuration

Private values live in two git-ignored files, so the repo is safe to publish and every developer uses their own:

| File | Holds | Template |
| --- | --- | --- |
| `.env` | Emails, name, mirror folder and remote, work GitHub account, SSH key path | `.env.example` |
| `repos.json` | Local work repo paths and `remote_repos` URLs | `repos.example.json` |

`.env` keys: `WORK_EMAIL` (several allowed, space-separated), `PERSONAL_NAME`, `PERSONAL_EMAIL` (must be verified on your personal GitHub), `MIRROR_DIR`, `EXPECTED_REMOTE` (`sync.sh` refuses to push anywhere else), and the optional `WORK_GH_USER` (PRs and reviews) and `PERSONAL_SSH_KEY` (timer pushes). Format is plain `KEY=VALUE`, comments on their own line. A variable already set in your shell overrides the file, and `MIRROR_ENV_FILE=/other/path` points to a different file.

## First-time setup

1. Create an empty **private** repo on your personal GitHub account.
2. `cp .env.example .env` and `cp repos.example.json repos.json`, then fill them in.
3. Run `./sync.sh --dry-run` and check the numbers.
4. Run `mirror.py` with `--apply` (or `sync.sh` after adding the remote):
   ```bash
   cd "$MIRROR_DIR"   # the folder from .env
   git remote add origin <the EXPECTED_REMOTE from .env>
   git push -u origin main
   ```
5. On GitHub, enable **Profile > Contribution settings > Include private contributions on my profile**.

## What gets counted

**PRs and reviews** (when `--gh-user` is set): PRs opened by you (`author:` search, by creation time) and every review you submitted on other people's PRs, last 365 days. GitHub hides private-org items from the contributions GraphQL API, so search and REST are used. Each becomes one empty commit, deduplicated by timestamp on re-runs. Mirrored items older than 365 days are never removed, so the mirror keeps them.

`git log --all` is used (merge commits included, because GitHub counts merges you author on the default branch), so these are included:

- Local branches, tags, and remote-tracking branches. The work repos are fetched at the start of every run, so stale clones are not a problem. If a fetch fails (offline, no access), a warning is logged and the local data is used.
- Unmerged feature-branch commits, which GitHub itself would not count in the work repo.
- Merge commits are included (hash-deduplicated like any other commit).
- Rebased or cherry-picked commits have different hashes, so they can count twice.
- The mirror only grows. Deleting or rewriting a branch later does not remove commits already mirrored.

## Performance

A normal run takes about 3 seconds (it was about 2 minutes). Nothing is skipped to get there:

- **Incremental PR/review scan.** After a successful `--apply`, the start time is saved in `logs/api-state.json`. The next run only asks GitHub about the period since then, **minus a 3-day safety margin** (a review always updates its PR's `updated` time, and overlap is harmless because duplicates are skipped by timestamp). A **full 365-day scan** is used automatically on the first run, when the mirror is empty, when the work account changes, when the state file is missing or unreadable, or with `--full-api` (about 15 s). The state is saved only after the commits are written, and never after a failed API call.
- **Parallel API calls.** Review lookups use 8 threads over keep-alive HTTPS (no `gh` process per call). Rate limits and 5xx errors are retried, honouring `Retry-After`.
- **No silent search cap.** GitHub search returns at most 1000 results per query. A date range that would exceed it is split in half until each part fits.
- **Streaming and low memory.** `git log` output is read line by line into per-timestamp counters. No hash set is kept, because `git log --all` already lists each commit once.
- **One `git fast-import`** writes all new commits at once, instead of one `git commit` process each (identical commit metadata, empty tree).
- **No push when nothing changed.** `sync.sh` skips `git push` if `main` equals `origin/main` (it prints `nothing to push`). Unpushed commits from a failed earlier push are still pushed.

In incremental runs the `matching` / `already_mirrored` numbers in the summary only cover the recent PR/review window (commits are always counted in full). `created` is exact.

## Requirements

Python 3 and git.

## Caveats

- Commits count on GitHub only if the author email is verified on your account, they are on the repo's default branch, and the repo is not a fork.
- The graph can take a few minutes to refresh.
- Check your employer's policy before using this. Commit timestamps are still information about work done for them.
- Push with your **personal** credentials only, not a work-managed account.
