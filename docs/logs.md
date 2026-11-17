# Logs

Every run (manual, `sync` or the scheduler) appends **one `SUMMARY` line** to `logs/mirror.log`, in addition to printing the full output on screen:

```
2026-11-17 03:10:56,255 INFO SUMMARY mode=apply repos=2 matching=320 already_mirrored=317 created=3 | new per repo: app-fe=3 app-api=0
```

- `mode` is `dry-run` or `apply`. `matching` = commits by your work email (plus PRs and reviews), `already_mirrored` = their timestamps already in the mirror, `created` (`would_create` in a dry run) = new commits, and the per-repo list shows where the new ones came from (`pull-requests` and `reviews` are the API sources).
- Skipped repos and failed lookups get a `WARNING` line. A failed scheduled run gets an `ERROR` line with the reason and the last lines of the error output, e.g. `ERROR scheduled sync failed: sync failed (commits may not be pushed) -- refusing to push: origin is ..., expected ...`.
- Handy: `grep SUMMARY logs/mirror.log`, or `grep "mode=apply.*created=[1-9]" logs/mirror.log` for runs that added commits, or `grep -E "WARNING|ERROR" logs/mirror.log` for problems.
- **Rotation:** at 5 MB the file rolls over to `mirror.log.1`, `.2`, … and a new `mirror.log` starts. The 10 most recent rolled files are kept (about 55 MB at most). One line per run is far below that.
- Repo folder names appear **only** in this local log, never in the mirror repo. Tokens are never logged.
- **On the setup page:** the Logs tab shows the runs as a chart and the log newest first ([GUI](gui.md#logs)).
- **Location:** `logs/` in the project folder (git-ignored). Change it with `MIRROR_LOG_DIR` in `config/.env` or `--log-dir`.
- `logs/api-state.json` holds, per host, the incremental-scan state (account name, where its login came from, and the time of the last successful scan), plus what each self-hosted server turned out to be. Deleting it is safe: the next run does a full scan.
- `logs/mirror.lock` makes sure only one run works at a time. It is an operating-system lock, released even if a run crashes; the file itself can stay.
