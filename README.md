<p align="center">
  <img src="docs/assets/overview.svg" alt="Work repos on any git host are read for dates only; the tool writes empty commits with those dates to your personal repo, which fills your personal contribution graph." width="900">
</p>

<p align="center">
  <a href="https://github.com/Majid-Ali-Watto/contribution-mirror-tool/actions/workflows/tests.yml"><img alt="tests" src="https://github.com/Majid-Ali-Watto/contribution-mirror-tool/actions/workflows/tests.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-3776AB.svg">
  <img alt="Linux, macOS, Windows" src="https://img.shields.io/badge/platform-linux%20%7C%20macOS%20%7C%20windows-lightgrey.svg">
  <a href="https://majidev.netlify.app"><img alt="Portfolio" src="https://img.shields.io/badge/portfolio-majidev.netlify.app-0A66C2.svg"></a>
</p>

# contribution-mirror-tool

Copies the **timestamps** of your work activity (commits, pull requests, reviews) into a separate personal repo as empty commits, so your personal GitHub contribution graph reflects your work without exposing any work code.

- **Nothing private is copied.** Only dates are read and written: no code, file names, paths, commit messages, hashes, repo names or PR titles. Your work repos are never modified. [Details](docs/how-it-works.md)
- **Works on Linux, macOS and Windows.** One Python implementation, thin wrappers for each shell. [Platforms](docs/platforms.md)
- **Works with any git host, in any combination.** Work repos on GitHub, GitLab, Bitbucket, Azure DevOps, Gitea/Forgejo or anything else that speaks git, mirrored into a repo on the same host or another one. GitHub to GitHub is the verified setup. [Hosts](docs/hosts.md)
- **No setup beyond your project URLs.** It uses ssh, https or the host's CLI, whichever you already use. [Authentication](docs/authentication.md)
- **Fast and safe to re-run.** A normal run takes a few seconds, and only timestamps not already in the mirror are added. [Performance](docs/performance.md)

> **How much is verified.** The Linux path and GitHub are verified on real accounts. macOS, Windows and the non-GitHub
> hosts, and mirrors on a host other than GitHub, are implemented and covered by an offline test suite (a CI workflow runs it on all three systems), but I could
> not run them on a real Mac, Windows PC or GitLab / Bitbucket / Azure DevOps / Gitea account. Commits are mirrored
> everywhere; if an extra (a pull request lookup, a notification) fails you get a warning, not a failed run. See
> [platforms](docs/platforms.md) and [hosts](docs/hosts.md).

## Quick start

1. Create an empty **private** repo on your personal GitHub account (the "mirror").
2. Copy the two templates and fill them in (both copies are git-ignored, so nothing private is ever committed):

   | Linux / macOS | Windows (PowerShell) |
   | --- | --- |
   | `cp config/.env.example config/.env` | `Copy-Item config\.env.example config\.env` |
   | `cp config/repos.example.json config/repos.json` | `Copy-Item config\repos.example.json config\repos.json` |

   `config/.env` holds your emails and the mirror repo; `config/repos.json` lists your projects (clone paths and/or git URLs of any host).
3. Preview, then run:

   | | Linux / macOS | Windows |
   | --- | --- | --- |
   | preview (changes nothing) | `scripts/sync.sh --dry-run` | `scripts\sync.cmd --dry-run` |
   | mirror and push | `scripts/sync.sh` | `scripts\sync.cmd` |

   (Both are wrappers for `python3 -I src/mirror.py sync` / `py -3 -I src\mirror.py sync`.)
4. On GitHub enable **Profile > Contribution settings > Include private contributions on my profile**.
5. Optional, run it automatically at login and daily on any system: `python3 -I src/mirror.py schedule install` ([Scheduling](docs/scheduling.md)).

## Project layout

```
contribution-mirror-tool/
├── README.md
├── config/                     everything you edit
│   ├── .env.example            template for your private settings        (copy to config/.env)
│   ├── repos.example.json      template for your project list            (copy to config/repos.json)
│   └── .env, repos.json        YOUR files, git-ignored
├── scripts/                    one-line wrappers around the Python entry point
│   ├── sync.sh, sync.cmd                       mirror + push
│   └── scheduled-sync.sh, scheduled-sync.cmd   what an OS scheduler runs
├── src/
│   ├── mirror.py               Python entry point (sync | scheduled | schedule | core options)
│   └── contribution_mirror/    the code, one module per responsibility
│       ├── cli.py              command dispatch, and the core run's steps in order
│       ├── sync.py             mirror + push          scheduled.py   network wait, notify, errors
│       ├── schedule.py         systemd / launchd / Task Scheduler      notify.py   desktop notifications
│       ├── push.py             safe push, first-run remote, token fallback
│       ├── config.py, envfile.py, settings.py      your settings, safe .env parser, paths and limits
│       ├── repos.py            which projects to scan (paths, URLs, grouping by host)
│       ├── scan.py, gitutil.py reading commit dates, running git, auth environment
│       ├── remotes.py          URL parsing: owner/repo, ssh alias -> real host, Azure DevOps forms
│       ├── credentials.py      find a login automatically and pick the right account
│       ├── httpapi.py          small HTTPS JSON client (retries, https-only tokens)
│       ├── providers/          one small file per git host: github, gitlab, bitbucket, azure, gitea
│       ├── activity.py, state.py   PRs and reviews: account, window, incremental scans
│       ├── mirror_repo.py      read the mirror, write new empty commits
│       ├── report.py, logsetup.py, timeutil.py   output, logging, timestamps from every vendor
├── tests/                      offline tests, stdlib only (python -m unittest discover -s tests)
├── docs/                       usage, configuration, authentication, hosts, platforms, scheduling, how it works, performance, logs, privacy, terms
│   └── assets/overview.svg     the diagram at the top of this README
├── .github/workflows/tests.yml runs the tests on Ubuntu, macOS and Windows
├── .github/rulesets/           the default-branch ruleset, importable (see docs/branch-protection.md)
├── LICENSE                     MIT
└── logs/                       generated: mirror.log and api-state.json (git-ignored)
```

## Documentation

| Topic | Read |
| --- | --- |
| Commands, `sync`, core options, scanning folders | [docs/usage.md](docs/usage.md) |
| `config/.env`, `config/repos.json`, every setting | [docs/configuration.md](docs/configuration.md) |
| ssh, https, CLIs, tokens, how the right account is chosen | [docs/authentication.md](docs/authentication.md) |
| GitHub, GitLab, Bitbucket, Azure DevOps, Gitea: what each supports, and which source/mirror combinations work | [docs/hosts.md](docs/hosts.md) |
| Linux, macOS, Windows: requirements, differences | [docs/platforms.md](docs/platforms.md) |
| Automatic runs (systemd, launchd, Task Scheduler), notifications | [docs/scheduling.md](docs/scheduling.md) |
| What is read and written, what gets counted, caveats | [docs/how-it-works.md](docs/how-it-works.md) |
| Why it is fast and light | [docs/performance.md](docs/performance.md) |
| `logs/mirror.log` and the SUMMARY line | [docs/logs.md](docs/logs.md) |
| The default-branch ruleset used on this repo, and how to apply it | [docs/branch-protection.md](docs/branch-protection.md) |
| What the tool reads, writes and contacts (no telemetry) | [docs/privacy.md](docs/privacy.md) |
| Terms of use: licence, no warranty, your responsibilities | [docs/terms.md](docs/terms.md) |

## Requirements

- Python 3.9+ (standard library only) and git, on Linux, macOS or Windows. Nothing else to install: the host CLIs (`gh`, `glab`, `az`) are optional.
- For PR and review mirroring: any login that can see your projects (an https credential helper, the host's CLI, or a `WORK_*TOKEN`). Without one, commits are still mirrored.
- Desktop notifications and automatic runs use what your system has (`notify-send` / systemd, `osascript` / launchd, PowerShell / Task Scheduler) and are optional.

## Tests

```bash
tests/run.sh                                  # Linux / macOS
python -m unittest discover -s tests          # any system (Windows: py -3 -m unittest discover -s tests)
```

Offline: needs only Python and git, touches no network and no real config.

## Author and contact

**Majid Ali**, Full Stack Web Developer (Islamabad, Pakistan).

- Email: [majidaliqau@gmail.com](mailto:majidaliqau@gmail.com)
- Portfolio: [majidev.netlify.app](https://majidev.netlify.app)
- LinkedIn: [majid-ali-074b97245](https://www.linkedin.com/in/majid-ali-074b97245/)
- GitHub: [@Majid-Ali-Watto](https://github.com/Majid-Ali-Watto)

Questions, bug reports and pull requests are welcome. For bugs, say which operating system and git host you use, and
paste the `WARNING` / `ERROR` lines from `logs/mirror.log` (they never contain tokens).

## License, privacy and terms

[MIT License](LICENSE) · [Privacy](docs/privacy.md) · [Terms of use](docs/terms.md)

## Caveats

- Commits count on GitHub only if the author email is verified on your account, they are on the repo's default branch, and the repo is not a fork.
- The graph can take a few minutes to refresh.
- Check your employer's policy before using this. Commit timestamps are still information about work done for them.
- Push with your **personal** credentials only, not a work-managed account.
