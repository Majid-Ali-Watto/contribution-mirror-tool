# Scheduling (automatic runs)

Run the sync automatically at login, once a day, or both, with whatever scheduler your system has. Set it up on the
**Schedule** tab of the setup page ([GUI](gui.md)), with `make`, or with one command:

```bash
python3 -I src/mirror.py schedule install --dry-run    # show exactly what would be written and run
python3 -I src/mirror.py schedule install              # at login and daily at 18:00
python3 -I src/mirror.py schedule install --time 14:00 # change the daily time (the login run stays as it is)
python3 -I src/mirror.py schedule install --no-daily   # at login only
python3 -I src/mirror.py schedule install --no-login   # daily only
python3 -I src/mirror.py schedule status               # what is set up now, read from the scheduler itself
python3 -I src/mirror.py schedule uninstall            # stop all automatic runs
```

(Windows: `py -3 -I src\mirror.py schedule ...`. With make: `make schedule TIME=14:00`, `make schedule-login`,
`make schedule-daily TIME=09:30`, `make schedule-off`, `make schedule-status`.) On Linux, `--timezone Europe/London` sets
the zone of the daily time. From an installed copy (see [usage](usage.md#installing-optional)), write `contribution-mirror schedule ...`
instead; the scheduler then starts that copy.

**The page, the command and `make` always agree**, because there is no separate record of what is set up: the scheduler
itself is read back (the timer file on Linux, the agents on macOS, the tasks on Windows). Running `install` again with
other options changes only what differs:

- Turning a run **off** removes it from the scheduler (both off = `uninstall`: nothing is left behind).
- Changing the **time** updates the daily run only; the login run is not reloaded, so on macOS it does not fire
  straight away. On Linux the timer is restarted to pick up the new time; if the new time already passed today and
  today's run has not happened yet, systemd runs it once right away (`Persistent=true`), which is harmless.
- **Settings and projects are not part of the schedule.** Every scheduled run reads your settings file (`.env`) and
  project list (`repos.json`) when it starts (the same files the setup page saves), so a change there applies to the next run
  without reinstalling anything.

| System | Scheduler | What is created |
| --- | --- | --- |
| Linux | systemd user timer | `~/.config/systemd/user/contribution-mirror.service` + `.timer`: 5 minutes after login, and daily; `Persistent=true` runs a missed daily run at next boot |
| macOS | launchd agents | `~/Library/LaunchAgents/dev.contribution-mirror.login.plist` (at login, after 60 s) and `.daily.plist` |
| Windows | Task Scheduler | tasks `contribution-mirror-login` (at logon, 5 minute delay) and `contribution-mirror-daily` |

More per-system detail: [platforms](platforms.md). Use `--dry-run` to see the exact files and commands before installing.
If you move this folder, or switch to another Python, run `schedule install` again so the scheduler points at the new
place. A timer written by hand (as long as it keeps the `contribution-mirror` names) is read and replaced the same way.

## What a scheduled run does

`python3 -I src/mirror.py scheduled` (also available as `scripts/scheduled-sync.sh` / `scripts\scheduled-sync.cmd`):

1. Goes through every [profile](configuration.md#profiles-several-setups) that has a settings file, one after the other (only one if
   `MIRROR_PROFILE` or `MIRROR_ENV_FILE` is set). For each it loads the settings file (`.env`); a problem here is reported
   as a failure for that profile, not ignored, and the next profile still runs.
2. Waits (once) up to about 5 minutes until the network is reachable: a TCP connection to the mirror's host or `github.com` on port 443 or 22, so it works for https and ssh setups alike.
3. Runs the sync and shows a desktop notification (optional):
   - **"sync OK"** with the number of new commits and which sources they came from, or "Up to date, no new commits".
   - If the run fails (network unreachable after about 5 minutes, or the push is rejected), a critical notification is shown and an `ERROR` line is added to `mirror.log` (in your log folder). The next run retries.

Warnings (a fetch that failed, a PR/review lookup that was skipped) do not fail the run: they are written to `mirror.log` as `WARNING` lines. See [logs](logs.md).

## Credentials for unattended runs

Git never prompts, so the run needs credentials that work without typing: an ssh key without a passphrase or a running agent, or an https credential helper. If a run starts while another one is still going (a manual `sync`, or the login and daily runs together), it stops straight away and says so; that is not reported as a failure. If `PERSONAL_SSH_KEY` is set in your settings file, the push (and only the push) uses that key. If your credentials live in the system keyring, the scheduled run only works while you are logged in, which is when the keyring is unlocked; check `mirror.log` for a `WARNING` after your first reboot. More in [authentication](authentication.md).

## systemd by hand (Linux)

`schedule install` is the same as writing the two files yourself and running:

```bash
systemctl --user daemon-reload && systemctl --user enable --now contribution-mirror.timer
systemctl --user list-timers contribution-mirror.timer     # next run
systemctl --user start contribution-mirror.service         # run now
journalctl --user -u contribution-mirror.service -n 30     # output of the last runs
systemctl --user disable --now contribution-mirror.timer   # turn off
```
