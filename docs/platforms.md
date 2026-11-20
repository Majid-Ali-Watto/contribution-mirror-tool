# Operating systems: Linux, macOS, Windows

One implementation runs everywhere: all the logic is Python (3.9+, standard library only) and git. The shell scripts
in `scripts/` are one-line wrappers.

| | Linux | macOS | Windows |
| --- | --- | --- | --- |
| Run by hand | `scripts/sync.sh` | `scripts/sync.sh` | `scripts\sync.cmd` |
| Same thing without a wrapper | `python3 -I src/mirror.py sync` | `python3 -I src/mirror.py sync` | `py -3 -I src\mirror.py sync` |
| Automatic runs | systemd user timer | launchd agents | Task Scheduler |
| Desktop notification | `notify-send` | `osascript` | PowerShell balloon |
| Credentials | ssh keys / agent, credential helper, CLIs | the same, plus the macOS Keychain helper | the same, plus Git Credential Manager |
| **Status** | **verified** (real runs) | code and tests portable, **not run on a Mac by me** | code and tests portable, **not run on Windows by me** |

**What "status" means.** I could only run Linux. For macOS and Windows I wrote the code to be portable, generated and
tested the OS-specific parts (the scheduler files and commands, the notification commands, path handling) from Linux
for all three platforms, and added a CI workflow (`.github/workflows/tests.yml`) that runs the whole test suite on
Ubuntu, macOS and Windows with Python 3.9 and 3.13 on every push. Until that has been green on your machine or in CI,
treat macOS and Windows as untested, and please report problems.

## Requirements per system

- **Linux:** Python 3.9+, git. `notify-send` is optional (package `libnotify-bin`).
- **macOS:** Python 3.9+ and git (Xcode command line tools, or Homebrew). If you use the python.org installer, run
  `Install Certificates.command` once, otherwise HTTPS lookups fail with `CERTIFICATE_VERIFY_FAILED`.
- **Windows:** [Git for Windows](https://git-scm.com/download/win) (includes git, ssh and Git Credential Manager),
  Python 3.9+ from python.org (with the `py` launcher). The OpenSSH client (built into Windows 10+) is needed for ssh URLs.

## Automatic runs

```bash
python3 -I src/mirror.py schedule install --dry-run    # show exactly what would be written and run
python3 -I src/mirror.py schedule install              # install (default: at login, then daily at 18:00 local time)
python3 -I src/mirror.py schedule status
python3 -I src/mirror.py schedule uninstall
```

(Windows: `py -3 -I src\mirror.py schedule install`.) Options: `--time HH:MM` for the daily run, `--no-login` /
`--no-daily` to have only one of the two, and on Linux `--timezone Europe/London`. The Schedule tab of the setup page
does the same ([GUI](gui.md)). Details in [scheduling](scheduling.md).

| System | What is created |
| --- | --- |
| Linux | `~/.config/systemd/user/contribution-mirror.service` and `.timer`; the timer runs 5 minutes after login and daily |
| macOS | `~/Library/LaunchAgents/dev.contribution-mirror.login.plist` (runs at login, after a 60 s delay) and `...daily.plist` |
| Windows | Task Scheduler tasks `contribution-mirror-login` (at logon, 5 minute delay) and `contribution-mirror-daily`, run with `pythonw` so no console window appears |

Each run waits up to about 5 minutes for the network, syncs, and shows a notification. Details in
[scheduling](scheduling.md).

## Known differences and limits

- The daily time is local time on every system (a `--timezone` name is a Linux/systemd feature).
- If the computer is off at the daily time: systemd runs it at next boot (`Persistent=true`). launchd catches up on a
  job missed while the Mac was *asleep* (not powered off), and Task Scheduler's "run as soon as possible after a missed
  start" option is not enabled by the simple task this tool creates. On macOS and Windows the run at login covers that
  gap, so a day is rarely missed.
- On Windows git marks its pack files read-only, so a plain delete of the temporary clone would fail and leave it
  behind. The tool handles this (and has a test for it), but if you ever see `mirror-scan-*` folders in the `tmp/` folder of
  your log folder, they are safe to delete (the next run removes them).
- Notifications are best effort everywhere: if the tool is missing the run still works and the result is in your `mirror.log`.
