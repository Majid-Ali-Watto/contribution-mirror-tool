# The setup and sync page (GUI)

New to the tool? The [getting started](getting-started.md) guide walks through a first setup with this page.

A small page in your browser for setting the tool up and running it, instead of editing files and typing commands:
**Setup**, **Sync** (with a calendar preview), **Accounts**, **Schedule** and **Logs**.
It is part of the tool (standard library only, nothing to install) and runs only on your own computer.

| Linux / macOS | Windows |
| --- | --- |
| `python3 -I src/mirror.py ui` (or `make ui`) | `py -3 -I src\mirror.py ui` |

Your browser opens on the page. The terminal shows the link too (it contains a one-time key, so do not share it). Press
**Ctrl+C** in the terminal to stop. Options: `--port N` (default: any free port), `--no-browser` (only print the link).

The **System / Light / Dark** switch at the top chooses the colours. System (the default) follows your operating
system; a choice is remembered by this browser only.

## Setup

1. **You.** Your work email(s), your name and personal email for the mirror commits, and the URL of your mirror repo.
   **Check** asks the repo (with your normal git setup) whether it can be reached. **More options:** the mirror folder,
   pull requests and reviews on or off, and the clone cache. **Save** writes `config/.env`:
   - on the first save it starts from `config/.env.example`, so the explanations stay in the file;
   - after that only those settings' lines are changed: tokens, comments and advanced options are kept as they are;
   - the file is readable only by you (mode 600 on Linux and macOS).

   Token values are never shown on the page, only which `WORK_*TOKEN` variables are set. A setting also set in your
   shell is flagged, because the shell wins over the file.
2. **Projects.** Your work repos, as local clone paths or git URLs of any host, the same list as `config/repos.json`
   ([configuration](configuration.md#configreposjson)). Each entry shows whether it is usable. **Find repos in a folder**
   lists the git repos under a folder so you can add them in one go. **Save projects** writes `config/repos.json`; a
   file in the older format (`remote_repos`) is converted to the single list.

## Sync

- **Preview** shows what a sync would add (found, already mirrored, new, per source) and changes nothing.
- **Sync now** asks for confirmation, then writes the new dates to your mirror and pushes them, exactly like
  `scripts/sync.sh`.
- The output of the run streams live; **Warnings and errors only** filters it. **Stop** ends a run.

Each run is a separate `mirror.py sync` process. It reads the saved settings fresh, it cannot take the page down if
something goes wrong, and it shares the run lock with the scheduled runs: if one is already going, the page says so.

## Accounts

**Check logins** shows, for each host of your projects, which login pull requests and reviews would use (the account
name and where the login came from, e.g. `gh:alice` or `git-credential`), hosts that only get commits (no API), and
local projects without a remote. It uses the same choice as a real run, including the account remembered from the last
run; **Compare all logins again** ignores that and checks every login found. Token values are never shown. The same
list on the command line: `mirror.py accounts`.

## Calendar (in Sync)

After a preview or a sync, a contribution-style calendar of the last 12 months shows your mirror: darker means more
dates that day, and a ring marks the days a preview would add to (or a sync just added to). Point at a day to see its
numbers. Days are the dates written in the timestamps themselves (each in its own time zone); GitHub may place a
date near midnight on the neighbouring day.

## Schedule

Turn the run **at login** and the run **every day at** a time on or off, and change the time (on Linux also the time
zone). **Apply** changes the system scheduler at once; **Turn all off** removes every automatic run. What the tab shows
is read from the scheduler itself, so it is also right after `mirror.py schedule ...` or `make schedule...` on the
command line. Scheduled runs read the settings and projects saved on the Setup tab each time they start. More in
[scheduling](scheduling.md).

## Logs

A bar per sync (new commits created) from `logs/mirror.log`, and the log itself, newest first, with a filter for
warnings and errors. Tokens inside URLs are shown as `***`. See [logs](logs.md).

## Security

The page can change your settings and push to your mirror, so it is locked down:

- The server listens on `127.0.0.1` only: other computers cannot reach it.
- Every request must carry a random key created when the page starts. The key is in the link's `#` part, which the
  browser never sends to any server or in a referrer; the page removes it from the address bar.
- A request must name this server (`127.0.0.1:<port>` or `localhost:<port>`) as its host, so another website cannot
  reach it through a DNS trick. Changes must come from the page itself, as JSON, so another site cannot submit a form
  to it.
- The page loads nothing from the internet (no fonts, scripts or images from elsewhere), and a content security policy
  forbids it. It cannot be shown inside another site's frame.
- Token values are never sent to the page, and any token inside a URL is shown as `***` in the output.
