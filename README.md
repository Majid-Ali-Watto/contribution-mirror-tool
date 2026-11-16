# contribution-mirror-tool

Copies the **timestamps** of your work commits into a separate personal repo as empty commits, so your personal GitHub contribution graph reflects your work activity without exposing any work code.

## What it reads and writes

- **Reads** (from work repos): author email and author date of each commit. The commit hash is used in memory only, to avoid counting a commit twice when it is on several branches.
- **Reads** (from the work GitHub account, via the `gh` CLI): only the timestamp of each PR you opened and each PR review you submitted in the last 365 days. Nothing else is stored (no titles, repos, PR numbers).
- **Never copies** into the mirror: code, file names, paths, commit messages, hashes or repo names.
- **Writes**: empty commits with the message `mirror`, your personal name and email, and the original date, into the mirror repo only.
- **Never modifies** the work repos.

## Files

| File | Purpose |
| --- | --- |
| `mirror.py` | Core script. Dry run by default; `--apply` creates commits. Never pushes. |
| `sync.sh` | Wrapper: runs `mirror.py --apply`, then pushes to the personal mirror remote. |

## Usage

```bash
./sync.sh            # mirror new commits and push
./sync.sh --dry-run  # preview only: no commits, no push
./sync.sh docker-learning projects            # only these sibling folders
./sync.sh --dry-run docker-learning              # preview for one folder
```

Folder names are resolved as siblings of `contribution-mirror-tool` first, then as paths, and are searched recursively. With no names, the repos in `repos.json` are scanned.

Running it again is safe. Only timestamps not already in the mirror are added.

Settings (work email, personal identity, paths, expected remote) are variables at the top of `sync.sh`.

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
| `--log-dir` | Optional. Folder for `mirror.log` (default `logs/` here). |

## Automatic runs (systemd user timer)

`contribution-mirror.timer` (in `~/.config/systemd/user/`) runs `scheduled-sync.sh`, which waits for network and then runs `sync.sh`:

- 5 minutes after you log in (i.e. when the laptop is turned on).
- Daily at 18:00 Asia/Karachi (PKT). If the laptop was off at 6pm, it runs when it is next on (`Persistent=true`).

Every run shows a desktop notification: **"sync OK"** with the number of new commits (and which repos they came from), or "Up to date, no new commits". If a run fails (GitHub unreachable after ~5 min, or `sync.sh` exits non-zero, e.g. push rejected), a critical **desktop notification** (`notify-send`) is shown and an `ERROR` line is added to `logs/mirror.log`. The next run retries.

Pushes use `~/.ssh/github_personal` explicitly, so no ssh-agent is needed. Re-runs are harmless: already-mirrored timestamps are skipped.

```bash
systemctl --user list-timers contribution-mirror.timer   # next run
systemctl --user start contribution-mirror.service       # run now
journalctl --user -u contribution-mirror.service -n 30   # logs
systemctl --user disable --now contribution-mirror.timer # turn off
```

## Choosing repos: `repos.json`

List the exact repo paths in [repos.json](repos.json). Each path must be a git repo (contain `.git`); the script reads it directly and does **not** search inside it or anywhere else.

```json
{
  "repos": [
    "/path/to/app-fe",
    "/path/to/app-api"
  ]
}
```

- `~` is expanded. Duplicates are ignored.
- A path that is missing or not a git repo is skipped with a warning (on stderr), the rest still run.
- To add a repo, add its path to the list. No code change needed.
- Precedence: `--src` or folder names passed to `sync.sh` > `repos.json` > scanning all sibling folders (only if `repos.json` does not exist).
- A different file can be used with `mirror.py --repos-file <path>`.

## How `--src` / folder names are scanned

A folder that is itself a git repo is scanned as one repo. A folder that is not a repo is searched recursively and every repo inside it is scanned. E.g. `projects` is not a repo; it contains `app-fe` and `app-api`, which are each scanned. The search does not descend into a repo or into `node_modules`.

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

## Current setup

- Source repos: those listed in `repos.json` (currently `projects/app-fe` and `projects/app-api`). New repos are not picked up until you add them to the file.
- Mirror repo: `~/work-contributions` (private, `Majid-Ali-Watto/work-contributions`)
- `sync.sh` refuses to push unless `origin` is the expected personal remote.

## First-time setup

1. Create an empty **private** repo on your personal GitHub account.
2. Run `./sync.sh --dry-run` and check the numbers.
3. Run `mirror.py` with `--apply` (or `sync.sh` after adding the remote):
   ```bash
   cd ~/work-contributions
   git remote add origin git@github.com:<you>/work-contributions.git
   git push -u origin main
   ```
4. On GitHub, enable **Profile > Contribution settings > Include private contributions on my profile**.

## What gets counted

**PRs and reviews** (when `--gh-user` is set): PRs opened by you (`author:` search, by creation time) and every review you submitted on other people's PRs, last 365 days. GitHub hides private-org items from the contributions GraphQL API, so search and REST are used. Each becomes one empty commit, deduplicated by timestamp on re-runs. Mirrored items older than 365 days are never removed, so the mirror keeps them.

`git log --all` is used (merge commits included, because GitHub counts merges you author on the default branch), so these are included:

- Local branches, tags, and remote-tracking branches that have been **fetched**. Unfetched remote work is missed, so `git fetch` in the work repos first if you want it included.
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
