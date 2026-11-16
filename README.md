# contribution-mirror-tool

Copies the **timestamps** of your work commits into a separate personal repo as empty commits, so your personal GitHub contribution graph reflects your work activity without exposing any work code.

## What it reads and writes

- **Reads** (from work repos): author email and author date of each commit. The commit hash is used in memory only, to avoid counting a commit twice when it is on several branches.
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
| `--log-dir` | Optional. Folder for `mirror.log` (default `logs/` here). |

## Automatic runs (systemd user timer)

`contribution-mirror.timer` (in `~/.config/systemd/user/`) runs `scheduled-sync.sh`, which waits for network and then runs `sync.sh`:

- 2 minutes after you log in (i.e. when the laptop is turned on).
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

`git log --all --no-merges` is used, so these are included:

- Local branches, tags, and remote-tracking branches that have been **fetched**. Unfetched remote work is missed, so `git fetch` in the work repos first if you want it included.
- Unmerged feature-branch commits, which GitHub itself would not count in the work repo.
- Merge commits are skipped.
- Rebased or cherry-picked commits have different hashes, so they can count twice.
- The mirror only grows. Deleting or rewriting a branch later does not remove commits already mirrored.

## Requirements

Python 3 and git.

## Caveats

- Commits count on GitHub only if the author email is verified on your account, they are on the repo's default branch, and the repo is not a fork.
- The graph can take a few minutes to refresh.
- Check your employer's policy before using this. Commit timestamps are still information about work done for them.
- Push with your **personal** credentials only, not a work-managed account.
