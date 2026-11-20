# CLAUDE.md

Guidance for Claude Code (and other contributors) working in this repository.

## What this is

A command-line tool that copies the **timestamps** of work activity (commits, PRs opened, reviews) into a personal
repo as empty commits, so a personal GitHub contribution graph reflects work done elsewhere. Nothing but dates may ever
leave the work repos: no code, messages, paths, hashes, repo or PR names.

## Commands

```bash
make test                                                      # all tests (Linux/macOS), offline, ~35 s; `make help` for the rest
python -W error::ResourceWarning -m unittest discover -s tests # what CI runs (Ubuntu, macOS, Windows; Python 3.9 + 3.13)
python -m unittest discover -s tests -p "test_scan.py"         # one file (run from the repo root; tests import _path)
scripts/sync.sh --dry-run                                      # preview with the real config (changes nothing)
python3 -I src/mirror.py setup                                 # settings file and project list from the templates
python3 -I src/mirror.py ui --no-browser                       # the local setup/sync page (prints the link)
python3 -I src/mirror.py accounts                              # which login each host would use
python3 -I src/mirror.py schedule status                       # what automatic runs are set up (read from the OS)
python3 -I src/mirror.py --help                                # core options
```

There is no build step or linter config. `pyproject.toml` is packaging metadata only (for `pipx install .`); the tool has
no dependencies. The `Makefile` only wraps the commands above (Windows users run them directly), so every target must stay
a plain command that also works without make.

## Hard rules

- **Standard library only, Python 3.9+.** No third-party imports, no syntax newer than 3.9 (no `match`, no `X | Y`
  types). CI checks 3.9.
- **Privacy is tested.** `tests/test_repo_files.py` fails if:
  - a new `http(s)://` address appears anywhere in `src/` (comments included). Update `docs/privacy.md` and the
    allow-list together, or write the example without a scheme;
  - a real home path (`/home/<name>/`, `/Users/<name>/`, `C:\Users\<name>\`) appears in a published file;
  - a relative link in `README.md` or `docs/*.md` is broken.
- **Never leak a token.** Every user-visible or logged message that can contain a URL goes through
  `logsetup.redact()` (`warn()` already does). Tokens reach git only through the environment (`gitutil.token_env`),
  never the command line. `gitutil.child_env()` strips `WORK_*TOKEN` from every program the tool starts.
- **Never modify work repos** beyond `git fetch` (no `--prune`, no checkout/merge). Temporary clones are always deleted
  (`gitutil.rmtree` handles read-only Windows pack files).
- **Tests are offline and isolated**: no network, no real git config (`_path.GIT_ENV`), no real tokens. Tests run
  with `ResourceWarning` as an error, so close every file, socket and subprocess pipe.
- The user's settings file (`.env`) and project list (`repos.json`) are private and never part of the repo: they live in
  per-user folders (or in `config/` of a checkout that already had them; `settings.py` has the rules). Never commit them.

## Layout

`src/mirror.py` is the entry point; everything else is in `src/contribution_mirror/`, one module per responsibility:

| Module | Role |
| --- | --- |
| `cli.py` | Argument parsing and the core run: scan, PRs/reviews, compare, write. Holds the run lock. `main` also makes SIGTERM end a run through its `finally` blocks (`_exit_on_sigterm`). |
| `sync.py` | `sync`: catch up with the personal repo, core run with `--apply`, push. |
| `ui.py`, `ui_static/` | `ui`: local web page (Setup, Sync). JSON API over `App`; runs are `mirror.py sync` child processes. |
| `scheduled.py`, `schedule.py`, `notify.py` | OS-scheduler entry, scheduler install (systemd/launchd/schtasks; commands from `settings.self_command()`), notifications |
| `accounts.py` | `accounts`: the login per host, via the same `Creds` as a run (`--json` for the page) |
| `config.py`, `envfile.py`, `settings.py` | Where the files are (`settings.py`, rules in its docstring), the settings file (parsed, never executed), `setup`; `envfile.update` (with `update_text`; `read` for the values) edits it in place. Constants |
| `repos.py` | The project list, `repos.json` (paths and URLs in one `"repos"` list; legacy `"remote_repos"` still read). The page's helpers: `read_entries`, `save_entries`, `clean_entries`, `check_entry`, `find_under` |
| `scan.py`, `gitutil.py` | Fetch/clone in parallel, stream `git log`, count each commit once across repos. Temporary clones go in `<log_dir>/tmp`; `clear_tmp` empties it at the start of each run |
| `mirror_repo.py` | Read mirror dates, reconcile with the remote, write via one `git fast-import`. `ensure_repo` writes only to a repo marked `contribution-mirror.mirror=true` (or one with only mirror commits, then marked) |
| `push.py` | Remote safety check, push, https-token fallback (token must own the mirror on GitHub/GitLab/Gitea; elsewhere only the credential helper) |
| `credentials.py`, `providers/` | Find logins, pick the work account, one file per host API |
| `activity.py`, `state.py` | PR/review scan per host, incremental window, `api-state.json` in the log folder (version 2) |
| `fsutil.py` | Private files and folders: `write_private` (0600, atomic), `private_dir` (0700), `make_private`. Settings, logs and state go through these |
| `report.py` | What a run prints and logs, and reading them back: `parse_result`, `read_logs`, `SUMMARY_RE`. The page depends on these formats |
| `providers/__init__.py` | `WORK_TOKEN_VARS`, the one list of the `WORK_*` token variables. `reset()` runs at the start of each core run, with `remotes.reset()` |
| `templates/` | `env.example`, `repos.example.json`: what `setup` and the page copy |
| `__main__.py` | `python -I -m contribution_mirror`: the entry point of an installed copy |
| `remotes.py`, `httpapi.py`, `timeutil.py`, `runlock.py`, `logsetup.py` | Helpers (`logsetup.redact()`, the owner-only log file) |

## Things that are easy to get wrong

- **Dedupe is by timestamp string.** Every date must be in one spelling: `gitutil.canonical_date` (UTC is `Z`, since
  git < 2.45 prints `+00:00`) for git output, `timeutil.to_utc_z` for API output. A new date source must use them, or
  every run re-creates commits.
- **Commits are read with `--branches --tags --remotes`**, not `--all` (that would count stashes).
- **Token trust:** `WORK_TOKEN`, `WORK_<HOST>_TOKEN` and conventional env vars only go to hosts in `KNOWN_HOSTS`,
  `GIT_HOST_PROVIDERS` or with a `WORK_*_API_URL` (`providers.trusted`). A probed host only gets its CLI login and
  credential-helper entry.
- **Mirror marker:** a new mirror is marked `contribution-mirror.mirror=true`. An existing folder without the mark is used only
  if all its commits are mirror commits (then it is marked); otherwise the run refuses. A personal repo that started with a
  README is refused on purpose: its owner marks the clone by hand (see `docs/configuration.md`).
- **Temporary clones** live in `<log_dir>/tmp` (owner-only, never the system temp folder). `clear_tmp` empties it at the start
  of each run, under the run lock, so a killed run leaves nothing behind for long.
- **Run output is an interface:** the page reads `SUMMARY` and `repo <label>: ...` lines through `report.parse_result`. Labels
  are `api:pull-requests`, `api:reviews` and `remote:<name>`; keep the formats or update `report.py` and its tests together.
- **PRs/reviews are limited** to the owners of the listed repos (`providers.base.owned_by`); GitLab filters by project
  id. With no listed repos, or an item without repo info, nothing is filtered.
- **State is per host** (`state.save_state(log_dir, {scope: (login, source)}, started, hosts)`), saved only after the
  commits are written, and only for hosts whose scan succeeded.
- **`run_lock` is re-entrant** in one process (`sync` holds it around `cli.run`, which takes it too).
- **ssh:** parallel work fetches get `BatchMode=yes` unless the user set `GIT_SSH_COMMAND`, `GIT_SSH` or
  `core.sshCommand`. `user_ssh_command(None)` reads only global/system config, never the current folder's repo.

## Automatic runs (`schedule.py`)

- **The OS scheduler is the only record** of what is set up: `current()` reads it back (timer file, plists, `schtasks
  /Query /XML`), `apply(login, daily, time, tz)` makes it match. Never add a separate "schedule settings" file: it
  would drift from what is really installed.
- `plan()` is pure and takes `existing` file contents: only changed launchd agents are reloaded (reloading the login
  agent would run a sync because of `RunAtLoad`); a changed systemd timer is restarted; both off = uninstall.
  Commands that may fail harmlessly are `Cmd(..., may_fail=True)`.
- Scheduled runs read the settings file and project list at start (`config.DEFAULT_ENV_FILE`, `settings.REPOS_FILE`),
  the same defaults as `ui.App()`: `tests/test_ui.py::SameFilesAsScheduledRuns` pins this.
- Tests must never touch the real scheduler: pass `home=` a temp dir **and** patch `XDG_CONFIG_HOME`, and a fake
  `run` (see `tests/test_platforms.py::Recorder`); `ui.App(schedule_ops=...)` takes fakes.

## The GUI (`ui.py`)

- **Security is part of the contract** (tested in `tests/test_ui.py`): bind `127.0.0.1` only; every `/api/` call
  needs the `X-Mirror-Token` header (the session token: the browser gets a one-time launch code in `#c=`, traded once at
  `POST /api/session`, and a second use stops the server; the terminal link carries `#t=`); the `Host` header must be this server
  (DNS rebinding); POSTs need a same-origin `Origin` (if sent) and `Content-Type: application/json`; strict CSP, so
  **no inline scripts or styles** in `ui_static/` and no external resources; token values never go to the page.
- The page inserts server data with `textContent` only, never `innerHTML`.
- Themes: colour tokens on `:root` (light), repeated for dark under `@media (prefers-color-scheme: dark)
  { :root:not([data-theme="light"]) }` and `:root[data-theme="dark"]`; add a new colour to all three blocks.
  `theme.js` (not deferred, in `<head>`) sets `data-theme` from `localStorage` before the first paint. New static
  files must be added to `ui.STATIC_FILES` (favicon included).
- Settings are written with `envfile.update` (edits only the managed `FIELDS` lines in place, keeps everything else)
  through `fsutil.write_private` (mode 600, atomic). The UI process never loads `.env` into its own environment.
- Runs pass `MIRROR_ENV_FILE` / `MIRROR_REPOS_FILE` to the child, so tests use temporary files. The page reads results
  by parsing the child's output (`SUMMARY`, `repo ...:` lines): keep those formats stable or update `report.parse_result`.
- `src/` may not contain new web addresses: build loopback URLs from variables (`f"http://{LOOPBACK}:{port}"`).
- Tabs: Setup, Sync (+ calendar from `--dates-out`), Accounts (`mirror.py accounts --json` child), Schedule
  (`schedule_ops`), Logs (`report.read_logs` parses `mirror.log*`; the log dir comes from `MIRROR_LOG_DIR` in the shell, then
  the settings file). Tests that read logs must set `MIRROR_LOG_DIR`, or they read the developer's real log folder.

## Screenshots (`docs/assets/ui-*-light.png` / `-dark.png`)

- Take them from demo data under a neutral folder (e.g. `/tmp/alex/...`) with a fake scheduler
  (`ui.App(schedule_ops=...)`), never from a real setup: the page shows full paths, emails and the mirror URL.
- Check every image by eye before adding it, and crop away anything that shows a real path (the Sync tab's output
  log does). The hygiene test skips binary files, so it cannot catch text drawn in an image.
- Each screenshot has a light and a dark version, used through a `<picture>` element in the README.

## Adding a git host

One file in `providers/` subclassing `Provider` (`api_base`, `headers`, `whoami`, `score`, `fetch`), registered in
`providers/__init__.py` (`PROVIDERS`, `KNOWN_HOSTS`). `fetch` must apply `owned_by` and return ISO timestamps. Test it
against `tests/_server.FakeHost` (see `tests/test_providers.py`) and add it to the `SOURCES`/`MIRRORS` tables in
`tests/test_cross_host.py`. Document it in `docs/hosts.md`.

## Testing helpers

- `_path.make_repo(path, [(email, iso_date), ...], merge=...)`: a throwaway repo with empty commits.
- `_path.GIT_ENV`: environment with an empty global git config; patch it into `os.environ` for end-to-end tests.
- `_server.FakeHost`: a local HTTP server; `routes[path] = (status, json)` or a callable taking the query; every
  request is recorded in `requests` (path, query, auth header).
- Fake servers must return **fixed** timestamps (a value computed per request would look new on the second run).

## Docs

User docs live in `docs/` (one topic per file, indexed in `README.md`). When behaviour changes, update the matching
page: `getting-started.md` (the new-user walkthrough: keep it in step with the page and the Makefile),
`gui.md` (the page and its security), `configuration.md` (settings, `repos.json`, where the files live), `authentication.md`
(tokens, accounts, ssh), `hosts.md` (per-host support), `how-it-works.md` (what is read/written/counted), `performance.md`,
`logs.md`, `privacy.md`, `usage.md` (CLI options, installing), `scheduling.md`.
