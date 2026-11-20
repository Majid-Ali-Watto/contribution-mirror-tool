# Logs

Every run (manual, `sync` or the scheduler) appends **one `SUMMARY` line** to `mirror.log` in your log folder, in addition to printing the full output on screen:

```
2026-11-17 03:10:56,255 INFO SUMMARY mode=apply repos=2 matching=320 already_mirrored=317 created=3 | new per repo: app-fe=3 app-api=0
```

- `mode` is `dry-run` or `apply`. `matching` = commits by your work email (plus PRs and reviews), `already_mirrored` = their timestamps already in the mirror, `created` (`would_create` in a dry run) = new commits, and the per-repo list shows where the new ones came from. `pull-requests`, `reviews` and `issues` are the API sources (the `repo` lines above it name them `api:pull-requests`, `api:reviews` and `api:issues`); a URL entry is named `remote:<name>` there.
- Skipped repos and failed lookups get a `WARNING` line. A failed scheduled run gets an `ERROR` line with the reason and the last lines of the error output, e.g. `ERROR scheduled sync failed: sync failed (commits may not be pushed) -- refusing to push: origin is ..., expected ...`.
- Handy, inside your log folder: `grep SUMMARY mirror.log`, or `grep "mode=apply.*created=[1-9]" mirror.log` for runs that added commits, or `grep -E "WARNING|ERROR" mirror.log` for problems.
- **Rotation:** at 5 MB the file rolls over to `mirror.log.1`, `.2`, … and a new `mirror.log` starts. The 10 most recent rolled files are kept (about 55 MB at most). One line per run is far below that.
- Repo folder names appear **only** in this local log, never in the mirror repo. Tokens are never logged: credentials in URLs and `Authorization:` headers are shown as `***` ([privacy](privacy.md#credentials-in-output)).
- **On the setup page:** the Logs tab shows the runs as a chart and the log newest first ([GUI](gui.md#logs)).
- **Location:** your log folder (`mirror.py setup` prints it; see [where the files are](configuration.md#where-the-files-are)), or `logs/` in a checkout that already has one. Change it with `MIRROR_LOG_DIR` in your settings file, or `--log-dir`. A log folder the tool creates is private to you (mode 700 on Linux and macOS), and so are `mirror.log` (with its rotated copies) and `api-state.json`.
- `api-state.json` (`api-state-NAME.json` for profile NAME, see [profiles](configuration.md#profiles-several-setups)) holds, per host, the incremental-scan state (account name, where its login came from, and the time of the last successful scan), plus what each self-hosted server turned out to be. Deleting it is safe: the next run does a full scan.
- `mirror.lock` makes sure only one run works at a time. It is an operating-system lock, released even if a run crashes; the file itself can stay.
- `tmp/` holds the temporary clones of URL entries while a scan runs. It is normally empty; see [how it works](how-it-works.md).
