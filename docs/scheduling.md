# Scheduling (automatic runs)

Run the sync automatically at login and once a day, with whatever scheduler your system has. One command installs it:

```bash
python3 -I src/mirror.py schedule install --dry-run    # show exactly what would be written and run
python3 -I src/mirror.py schedule install              # install
python3 -I src/mirror.py schedule status
python3 -I src/mirror.py schedule uninstall
```

(Windows: `py -3 -I src\mirror.py schedule ...`.) Options: `--time HH:MM` (daily run, local time, default 18:00) and, on
Linux, `--timezone Europe/London`.

| System | Scheduler | What is created |
| --- | --- | --- |
| Linux | systemd user timer | `~/.config/systemd/user/contribution-mirror.service` + `.timer`: 5 minutes after login, and daily; `Persistent=true` runs a missed daily run at next boot |
| macOS | launchd agents | `~/Library/LaunchAgents/dev.contribution-mirror.login.plist` (at login, after 60 s) and `.daily.plist` |
| Windows | Task Scheduler | tasks `contribution-mirror-login` (at logon, 5 minute delay) and `contribution-mirror-daily` |

More per-system detail: [platforms](platforms.md). Use `--dry-run` to see the exact files and commands before installing.
If you move this folder, run `schedule install` again so the scheduler points at the new place.

## What a scheduled run does

`python3 -I src/mirror.py scheduled` (also available as `scripts/scheduled-sync.sh` / `scripts\scheduled-sync.cmd`):

1. Loads `config/.env` (a problem here is reported as a failure, not ignored).
2. Waits up to about 5 minutes until the network is reachable: a TCP connection to the mirror's host or `github.com` on port 443 or 22, so it works for https and ssh setups alike.
3. Runs the sync and shows a desktop notification (optional):
   - **"sync OK"** with the number of new commits and which sources they came from, or "Up to date, no new commits".
   - If the run fails (network unreachable after about 5 minutes, or the push is rejected), a critical notification is shown and an `ERROR` line is added to `logs/mirror.log`. The next run retries.

Warnings (a fetch that failed, a PR/review lookup that was skipped) do not fail the run: they are written to `logs/mirror.log` as `WARNING` lines. See [logs](logs.md).

## Credentials for unattended runs

Git never prompts, so the run needs credentials that work without typing: an ssh key without a passphrase or a running agent, or an https credential helper. If `PERSONAL_SSH_KEY` is set in `config/.env`, the push (and only the push) uses that key. If your credentials live in the system keyring, the scheduled run only works while you are logged in, which is when the keyring is unlocked; check `logs/mirror.log` for a `WARNING` after your first reboot. More in [authentication](authentication.md).

## systemd by hand (Linux)

`schedule install` is the same as writing the two files yourself and running:

```bash
systemctl --user daemon-reload && systemctl --user enable --now contribution-mirror.timer
systemctl --user list-timers contribution-mirror.timer     # next run
systemctl --user start contribution-mirror.service         # run now
journalctl --user -u contribution-mirror.service -n 30     # output of the last runs
systemctl --user disable --now contribution-mirror.timer   # turn off
```
