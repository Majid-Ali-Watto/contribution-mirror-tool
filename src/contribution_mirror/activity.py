"""PRs opened and reviews submitted: pick the account(s) per host, choose the scan window, query, and label."""
from collections import Counter
from datetime import datetime, timezone

from .logsetup import say, warn
from .report import API_PREFIX
from .state import api_since
from .timeutil import to_utc_z


def _reason(e):
    detail = (getattr(e, "stderr", "") or str(e)).strip().splitlines()[-1:] or [repr(e)]
    return detail[0]


def fetch_activity(creds, repos_by_scope, st, mirror_has_commits, full, explicit):
    """Returns (items, done, started). `items` is [(label, Counter)] for pull requests and reviews; `done` is
    {scope: (account, token source)} for every host whose scan fully succeeded (saved in the state only after the
    commits are written); `started` is when the scan began. Each host is independent: one that fails is a warning
    and is fully re-checked next time, the others still count. `explicit` = the user forced an account, so failing
    to use it is a warning rather than a quiet skip. Never raises: commits are mirrored even if an API is down."""
    started = datetime.now(timezone.utc)
    prs, reviews, done, picked = [], [], {}, False
    for scope in repos_by_scope:
        try:
            pk = creds.pick(scope)
        except Exception as e:  # noqa: BLE001 - see docstring
            warn(f"PR/review login lookup for {scope} failed, skipped: {_reason(e)}")
            continue
        if not pk:
            continue
        picked = True
        since, how = api_since(st, scope, pk.login, mirror_has_commits, full)
        try:
            pr_s, rv_s = pk.provider.fetch(pk.api(), pk.login, repos_by_scope[scope], since)
            pr_s, rv_s = [to_utc_z(t) for t in pr_s], [to_utc_z(t) for t in rv_s]
        except Exception as e:  # noqa: BLE001 - see docstring
            warn(f"PR/review fetch for {scope} failed, skipped: {_reason(e)}")
            continue
        prs += pr_s
        reviews += rv_s
        done[scope] = (pk.login, pk.source)
        say(f"PRs/reviews          : {scope} ({pk.provider.label}) as {pk.login} via {pk.source}, {how}"
            + ("" if pk.provider.reviews else " [pull requests only]"))
    if not picked:
        if explicit:
            warn("PR/review fetch skipped: the account you configured (WORK_*_TOKEN / gh user) could not be used")
        else:
            say("PRs/reviews          : skipped (no login found that can see your projects, or the host has no "
                "supported API; commits are still mirrored. Log in with `gh auth login` / `glab auth login`, "
                "use an https credential helper, or set WORK_TOKEN in your .env settings)")
    if not done:
        return [], {}, started
    return [(f"{API_PREFIX}pull-requests", Counter(prs)), (f"{API_PREFIX}reviews", Counter(reviews))], done, started
