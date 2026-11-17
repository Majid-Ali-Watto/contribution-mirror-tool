# Usage

## `sync`: the command you run

```bash
scripts/sync.sh                       # Linux / macOS: mirror new activity and push
scripts\sync.cmd                      # Windows
python3 -I src/mirror.py sync         # the same thing, without a wrapper (Windows: py -3 -I src\mirror.py sync)

scripts/sync.sh --dry-run             # preview only: no commits, no push
scripts/sync.sh my-repo other-folder  # only these folders (names of sibling folders, or paths)
scripts/sync.sh --dry-run my-repo     # preview for one folder
```

- All of them run the same Python code; the wrappers only find your Python. Set `PYTHON=/path/to/python` to choose one.
- It reads your settings from `config/.env` and your projects from `config/repos.json` (see [configuration](configuration.md)).
- Folder names are resolved as siblings of the project folder first, then as paths, and are searched recursively. With no names, the projects in `config/repos.json` are scanned.
- Running it again is safe: only timestamps not already in the mirror are added.
- `sync` runs the core with `--apply` and then pushes. The push is skipped (`nothing to push`) when nothing changed, and it refuses to push to any repo other than `EXPECTED_REMOTE`. On the first run it connects the mirror to `EXPECTED_REMOTE` itself.

## Other commands

| Command | Purpose |
| --- | --- |
| `mirror.py sync [--dry-run] [folder ...]` | mirror and push (above) |
| `mirror.py scheduled [--startup-delay SECONDS]` | what an OS scheduler runs: wait for the network, sync, notify ([scheduling](scheduling.md)) |
| `mirror.py schedule install\|uninstall\|status [--dry-run]` | set up automatic runs for this OS ([scheduling](scheduling.md)) |
| `mirror.py <options>` | the core, below (no command word) |

## The core: `src/mirror.py <options>`

Dry run by default; `--apply` creates commits. It never pushes.

```bash
python3 -I src/mirror.py \
  [--src ../some-repo ../some-folder] \
  --work-email you@company.com \
  --mirror ~/work-contributions \
  --name "Your Name" --email your-verified-personal@email.com \
  [--apply]
```

| Option | Meaning |
| --- | --- |
| `--src` | Optional. Repo(s), or folders containing repos (searched recursively, skipping `node_modules`). **Default: the projects in `config/repos.json`**, or every sibling folder if that file is missing. The mirror repo itself is never scanned. |
| `--repos-file` | Optional. JSON file `{"repos": [paths or URLs]}` to use instead of `config/repos.json`. |
| `--work-email` | Your author email(s) in the work repos. Several allowed. |
| `--mirror` | Path of the mirror repo. Created if missing. Must not be inside a source repo. |
| `--name`, `--email` | Author identity for mirror commits. The email must be **verified** on your personal GitHub account. |
| `--apply` | Actually create commits. Without it, nothing is written. |
| `--gh-user` | Optional override: use this `gh` account as your work account. Normally not needed, the account is chosen automatically ([authentication](authentication.md)). |
| `--no-api` | Optional. Commits only: do not mirror PRs and reviews. |
| `--full-api` | Optional. Re-scan the whole 365-day PR/review window instead of only the recent days ([performance](performance.md)). |
| `--no-fetch` | Optional. Skip `git fetch` of the work repos and the temporary clones of URL entries (offline / fastest). |
| `--log-dir` | Optional. Folder for `mirror.log` and `api-state.json` (default `logs/` in the project folder). |

`-I` (Python's isolated mode) is used so a stray file in the current directory can never shadow the code; `src/mirror.py` adds only its own folder to the import path.

## How `--src` / folder names are scanned

A folder that is itself a git repo is scanned as one repo. A folder that is not a repo is searched recursively and every repo inside it is scanned. E.g. a folder `projects` that is not a repo but contains `app-fe` and `app-api` has both of them scanned. The search does not descend into a repo or into `node_modules`.

## Tests

```bash
tests/run.sh
```
