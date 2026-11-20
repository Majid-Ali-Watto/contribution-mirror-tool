# Changelog

What changed in each version. `mirror.py --version` (`contribution-mirror --version` when installed) prints the
version you have; the setup page shows it under its title. To update a checkout: `git pull`. An installed copy:
`pipx install --force <wheel URL>` with the wheel of the newest release, or `pipx install --force .` from a checkout.

## 1.1.1 (2026-11-21)

### Fixed
- An installed copy (`pipx install`) now names its own command in every message, help text and on the page:
  `contribution-mirror setup`, `contribution-mirror ui`, ... instead of `mirror.py ...`, which only exists in a checkout.
  The settings template says how to run a command either way.

## 1.1.0 (2026-11-21)

### New
- **More of your work counts.** Commits that name you in a `Co-authored-by:` line (pair programming, a squash merge
  someone else made), and **issues you opened** on GitHub, GitLab and Gitea / Forgejo, as GitHub's own graph counts them.
- **Date limits:** `MIRROR_SINCE` / `MIRROR_UNTIL` (or `--since` / `--until`, or the page's "Only dates from / until")
  leave out activity before you joined or after you left.
- **Profiles:** several setups side by side, e.g. one per employer, each with its own settings and project list.
  `mirror.py --profile NAME ...`, a Profile menu on the page, and automatic runs that sync every profile.
- **`mirror.py doctor`** and the page's **Check my setup**: every check a sync depends on (settings, projects, your
  commits found, the mirror repo and folder, personal email verified on GitHub, default branch, logins, automatic runs,
  file permissions), each problem with how to fix it. Reads only.
- **`mirror.py setup`**: creates the settings file and project list from templates and shows where everything is.
- **Install as a command:** `pipx install .` gives `contribution-mirror`; `python -m contribution_mirror` works too.
- **Version:** `mirror.py --version`, this changelog, and the version on the page.
- **Tooltips** on every control of the page, shown on hover, keyboard focus and a long press on touch screens.

### Changed
- New setups keep their settings and logs in the per-user folders of the operating system; a checkout that already
  has `config/` or `logs/` keeps using them.
- Without a project list (and without `--src`) a run stops with an error instead of scanning every neighbouring folder.
- The PR / review sources are labelled `api:pull-requests`, `api:reviews` (and the new `api:issues`) for every host.

### Security and safety
- The browser is opened with a one-time code instead of the page's key, so other users of the computer cannot reuse
  the link from a command line.
- Stopping a run (the page's Stop, a scheduler) deletes its temporary clones; the next run removes any left by a crash.
- Logs, `api-state.json`, the settings and the project list are readable only by you.
- The mirror folder is marked as yours; a folder holding other work is never written to.
- The https push fallback only uses a token it can match to the mirror's owner.
- More credentials are hidden in messages (passwords in any URL, `Authorization` headers).

## 1.0.0 (2026-11-17)

The first release: commits from local clones and git URLs on any host, pull requests and reviews from GitHub, GitLab,
Bitbucket, Azure DevOps and Gitea, one `git fast-import` into the mirror, safe push, the setup and sync page, automatic
runs with systemd, launchd or Task Scheduler, and desktop notifications.
