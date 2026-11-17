# Performance

A normal run takes a few seconds. Nothing is skipped to get there:

- **Incremental PR/review scan** (`state.py`). After a successful `--apply`, the start time is saved in `logs/api-state.json`. The next run only asks the host about the period since then, **minus a 3-day safety margin** (a review always updates its PR's `updated` time, and overlap is harmless because duplicates are skipped by timestamp). A **full 365-day scan** is used automatically on the first run, when the mirror is empty, when the work account changes, when the state file is missing or unreadable, or with `--full-api` (about 15 s). The state is saved only after the commits are written, and never after a failed API call.
- **Parallel work.** Review lookups use 8 threads over keep-alive HTTPS (no extra process per call). Fetches and temporary clones of all projects run in parallel. Rate limits and 5xx errors are retried, honouring `Retry-After`.
- **No silent search cap.** GitHub search returns at most 1000 results per query. A date range that would exceed it is split in half until each part fits.
- **Streaming and low memory** (`gitutil.py`). `git log` output is read line by line into per-timestamp counters. No hash set is kept, because `git log --all` already lists each commit once. Peak memory is about 30-40 MB.
- **One `git fast-import`** (`mirror_repo.py`) writes all new commits at once, instead of one `git commit` process each (identical commit metadata, empty tree).
- **No push when nothing changed.** `sync` skips `git push` if `main` equals `origin/main` (it prints `nothing to push`). Unpushed commits from a failed earlier push are still pushed.

In incremental runs the `matching` / `already_mirrored` numbers in the summary only cover the recent PR/review window (commits are always counted in full). `created` is exact.
