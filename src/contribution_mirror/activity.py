"""PRs opened and reviews submitted: pick the account(s) per host, choose the scan window, query, and label."""
from collections import Counter
from datetime import datetime, timezone

from .logsetup import say, warn
from .state import api_since
from .timeutil import to_utc_z


def fetch_activity(creds, repos_by_scope, log_dir, mirror_has_commits, full, explicit):
    """Returns (items, who, started). `items` is [(label, Counter)] for pull requests and reviews; `who` identifies
    the account(s) used and is None when the step did not run (it is saved in the state only after the commits
    are written); `started` is when the scan began. `explicit` = the user forced an account, so failing to use it
    is a warning rather than a quiet skip. Never raises: commits are mirrored even if an API is unreachable."""
    started = datetime.now(timezone.utc)
    try:
        picks = {s: pk for s in repos_by_scope for pk in [creds.pick(s)] if pk}
        if picks:
            # account(s) recorded with the saved state, so switching account forces a full re-scan
            who = ",".join(sorted(f"{s}/{pk.login}" for s, pk in picks.items()))
            since, how = api_since(log_dir, who, mirror_has_commits, full)
            prs, reviews = [], []
            for scope, pk in picks.items():
                pr_s, rv_s = pk.provider.fetch(pk.api(), pk.login, repos_by_scope[scope], since)
                prs += [to_utc_z(t) for t in pr_s]
                reviews += [to_utc_z(t) for t in rv_s]
            say(f"PRs/reviews          : {how}; " + ", ".join(
                f"{s} ({pk.provider.label}) as {pk.login} via {pk.source}"
                + ("" if pk.provider.reviews else " [pull requests only]") for s, pk in picks.items()))
            return [("github-api:pull-requests", Counter(prs)), ("github-api:reviews", Counter(reviews))], who, started
        if explicit:
            warn("PR/review fetch skipped: the account you configured (WORK_*_TOKEN / gh user) could not be used")
        else:
            say("PRs/reviews          : skipped (no login found that can see your projects, or the host has no "
                "supported API; commits are still mirrored. Log in with `gh auth login` / `glab auth login`, "
                "use an https credential helper, or set WORK_TOKEN in config/.env)")
    except Exception as e:  # noqa: BLE001 - see docstring
        detail = (getattr(e, "stderr", "") or str(e)).strip().splitlines()[-1:] or [repr(e)]
        warn(f"PR/review fetch failed, skipped: {detail[0]}")
    return [], None, started
