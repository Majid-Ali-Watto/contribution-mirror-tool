# Getting started

A step-by-step guide for a first setup, from cloning this repository to seeing your work on your GitHub graph. It takes
about ten minutes. Each step shows the **page** way (a setup page in your browser) and the **command** way; use
whichever you like, they read and write the same files.

Commands are shown for Linux and macOS. On Windows, use `py -3 -I src\mirror.py ...` wherever this guide says
`python3 -I src/mirror.py ...` (Windows has no `make`; every `make` target is a short form of one of those commands,
see `make help` or the [Makefile](../Makefile)).

## 1. What you need

- **Python 3.9 or newer** and **git**. Check with `python3 --version` and `git --version` (Windows: `py -3 --version`).
  Nothing else is installed: the tool uses the Python standard library only.
- A **personal GitHub account**, where the mirror will live.
- Your **work repos**: either cloned on this computer, or reachable with the git access you already use (ssh keys, a
  credential helper, or a host CLI such as `gh`). See [authentication](authentication.md).
- Optional, for pull requests and reviews: a login to your work host that can see your projects, e.g. `gh auth login`.
  Without one, commits are still mirrored.

Before you start, check your employer's policy: commit timestamps are still information about work done for them.

## 2. Get the tool

```bash
git clone https://github.com/Majid-Ali-Watto/contribution-mirror-tool.git
cd contribution-mirror-tool
make test            # optional: the offline test suite, about 30 s (Windows: py -3 -m unittest discover -s tests)
```

Keep the folder where it is: automatic runs point at it (if you move it later, see [Moving or updating](#moving-or-updating)).
This guide runs the tool from the folder (`python3 -I src/mirror.py ...`). `pipx install .` instead gives a
`contribution-mirror` command; see [usage](usage.md#installing-optional).

## 3. Prepare GitHub (once)

1. **Create the mirror repo:** on your personal account, create a new **private** repository, for example
   `work-contributions`, **empty** (no README, licence or .gitignore). Copy its ssh or https URL.
2. **Verify your personal email:** in GitHub, Settings > Emails. The mirror commits use this email; if it is not verified on
   your account, they do not appear on your graph.
3. **Show private work on your profile:** Profile > Contribution settings > tick **Include private contributions on my
   profile**. Without it, a private mirror's commits only show to you.

## 4. Enter your details and projects

### The page way

```bash
make ui              # or: python3 -I src/mirror.py ui
```

Your browser opens the setup page (the terminal shows the link too; it is the key to the page, so do not share it).

1. **Setup > You:** your work email (the author email of your commits in the work repos; several are fine), your name
   and verified personal email, and the mirror repo URL from step 3. Press **Check** to test the mirror URL, then
   **Save**.
2. **Setup > Projects:** add each work repo as a local clone path or a git URL, or use **Find repos in a folder** to
   add every repo under a folder at once. A green dot means the entry is usable. Press **Save projects**.

Not sure which email your work commits use? In a work repo, run `git log --format=%ae | sort | uniq -c | sort -rn | head`.

### The command way

```bash
make setup           # or: python3 -I src/mirror.py setup (Windows: py -3 -I src\mirror.py setup)
```

`setup` creates your settings file (`.env`) and project list (`repos.json`) from the templates, never overwrites them,
and prints where they are ([where the files are](configuration.md#where-the-files-are)). Then edit both:

- your settings file (`.env`): `WORK_EMAIL`, `PERSONAL_NAME`, `PERSONAL_EMAIL`, `EXPECTED_REMOTE` (the mirror URL). Optional
  settings are explained in the file and in [configuration](configuration.md).
- your project list (`repos.json`): your projects, `{"repos": ["/path/to/clone", "git@github.com:org/repo.git"]}`.

Both are private to you and kept outside the code, so nothing private is ever committed.

## 5. Preview (changes nothing)

- Page: **Sync > Preview**.
- Command: `make preview` (or `python3 -I src/mirror.py sync --dry-run`).

You see how many of your contributions were found, how many are already in the mirror, and how many would be added,
per project; the page also draws a calendar of the last 12 months. If a project shows **0 found**, its email does not
match `WORK_EMAIL` (see the tip in step 4) or the repo could not be read (the warnings say why).

## 6. First sync

- Page: **Sync > Sync now** (asks for confirmation).
- Command: `make sync` (or `python3 -I src/mirror.py sync`).

The new dates are written to your mirror folder (`~/work-contributions` by default) as empty commits and pushed to your
mirror repo. Your graph can take a few minutes to update. Running it again is safe: only new dates are added.

If the push fails, your normal git setup cannot push to the mirror repo: check the URL and your access (ssh key, `gh
auth login`, or a credential helper). See [authentication](authentication.md).

## 7. Pull requests and reviews (optional)

- Page: **Accounts > Check logins**.
- Command: `make accounts` (or `python3 -I src/mirror.py accounts`).

For each host of your projects it shows which login is used (for example `you-at-work via gh:you-at-work`). If a host
says *no login found*, log in to it (`gh auth login`, `glab auth login`, or an https credential helper), or set a token in
your settings file (see [authentication](authentication.md)). Commits are mirrored either way.

## 8. Automatic runs (optional)

- Page: **Schedule**: tick **At login** and/or **Every day at** a time, then **Apply**. **Turn all off** stops them.
- Command: `make schedule` (login and daily at 18:00), `make schedule TIME=14:00`, `make schedule-login`,
  `make schedule-daily TIME=09:30`, `make schedule-off`, `make schedule-status`.

It uses your system's own scheduler (systemd, launchd or Task Scheduler). Each automatic run reads your settings file
and project list when it starts, so changes you save later are used without reinstalling. Automatic runs cannot type
passwords: use an ssh agent or an https credential helper. Details: [scheduling](scheduling.md).

## Checking the setup

If something looks wrong, or nothing shows on your graph, let the tool check itself: **Setup > Check my setup** on the
page, or `make doctor` / `python3 -I src/mirror.py doctor`. It only reads (nothing is changed or pushed) and prints each
check with how to fix it:

| Check | What it finds |
| --- | --- |
| Settings file, project list | Missing or unreadable files, an empty list, entries that are not repos, files other users can read. |
| Work email | How many of your commits each local clone has; 0 means `WORK_EMAIL` differs from your author email. |
| Mirror repo, mirror folder | Whether git can reach the mirror repo, and whether the mirror folder is really your mirror. |
| Personal account (GitHub) | Whether `PERSONAL_EMAIL` is verified on the account that owns the mirror, and whether the repo's default branch is `main`. Needs that account's login on this computer (e.g. `gh auth login`); reading your emails needs the `user:email` scope (`gh auth refresh -h github.com -s user:email`). |
| Logins | The login each work host uses for pull requests and reviews, as a sync would choose it. |
| Automatic runs | Whether they are set up, when the last sync was, and whether the last scheduled run failed. |

The command exits with code 1 if a check found a problem (warnings do not count), so a script can use it too.

## Checking on it

- Page: **Logs** shows a bar per sync and the log, newest first.
- Files: `mirror.log` in your log folder (`mirror.py setup` prints where; [logs](logs.md)).
- Desktop notifications after each automatic run say how many commits were added, or what failed.

## Moving or updating

- **Update:** `git pull` in this folder. Your settings file, project list, logs and the mirror are untouched.
- **Moved the folder, or changed Python?** Apply the schedule again (Schedule > Apply, or `make schedule`) so the
  scheduler points at the new place.
- **A second computer:** clone the tool there and use the same mirror repo. Each sync first fetches the mirror repo and
  builds on it, so nothing is ever added twice.

## Troubleshooting

| You see | What to do |
| --- | --- |
| `missing <path> -- run mirror.py setup` | Do step 4 (page: Setup > Save; command: `make setup`, then edit the file). |
| `no project list: <path> does not exist` | Do step 4 to create the project list, or name folders with `--src` / `sync <folder>`. Nothing else is scanned. |
| `0 found` for a project | The author email differs from `WORK_EMAIL`: see the tip in step 4. |
| `fetch failed ... scanning what is already local` | Your git access to that work repo failed (offline, or no credentials for this run); the local copy was still read. |
| `refusing to push: origin is ...` | The mirror folder points at another repo than `EXPECTED_REMOTE`. Fix the URL in Setup or in your settings file. |
| `refusing to write to <folder>: it has commits this tool did not make` | `MIRROR_DIR` points at a folder with other work in it. Use a new folder, or mark your own mirror as the message says ([the mirror folder](configuration.md#the-mirror-folder)). |
| `another run is in progress` | A manual sync and an automatic run overlapped; try again in a minute. |
| Commits pushed but not on the graph | Run **Check my setup** (or `mirror.py doctor`). Usually: verify the personal email (step 3.2), enable private contributions (step 3.3), wait a few minutes. |
| Anything else | `mirror.log` in your log folder has the `WARNING`/`ERROR` lines (never tokens); include them, your OS and git host in a bug report. |

## Removing it

1. Stop automatic runs: Schedule > **Turn all off**, or `make schedule-off`.
2. Delete this folder, the mirror folder (`~/work-contributions` by default), and your settings, project list and log folders (`mirror.py setup` shows where they are).
3. To remove the history from your graph, delete the mirror repo on GitHub.
