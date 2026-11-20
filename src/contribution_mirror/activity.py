"""PRs opened, reviews submitted and issues opened: pick the account(s) per host, choose the scan window, query, and
label."""
from collections import Counter
from datetime import datetime, timezone

from .logsetup import say, warn
from .report import API_PREFIX
from .state import api_since, projects_key
from .timeutil import to_utc_z


def _reason(e):
    detail = (getattr(e, "stderr", "") or str(e)).strip().splitlines()[-1:] or [repr(e)]
    return detail[0]


def fetch_activity(creds, repos_by_scope, st, mirror_has_commits, full, explicit, paused=None):
    """Returns (items, done, started). `items` is [(label, Counter)] for pull requests and reviews; `done` is
    {scope: (account, token source, projects key)} for every host whose scan fully succeeded (saved in the state only
    after the commits are written); `started` is when the scan began. `paused` = {scope: ['owner/repo', ...]} of
    paused projects, whose activity is left out; a host whose listed or paused projects changed is fully re-scanned
    (a resumed or newly added project then gets its older pull requests too). Each host is independent: one that
    fails is a warning and is fully re-checked next time, the others still count. `explicit` = the user forced an account, so failing
    to use it is a warning rather than a quiet skip. Never raises: commits are mirrored even if an API is down."""
    started = datetime.now(timezone.utc)
    prs, reviews, issues, done, picked = [], [], [], {}, False
    for scope in repos_by_scope:
        try:
            pk = creds.pick(scope)
        except Exception as e:  # noqa: BLE001 - see docstring
            warn(f"PR/review login lookup for {scope} failed, skipped: {_reason(e)}")
            continue
        if not pk:
            continue
        picked = True
        skip = (paused or {}).get(scope, [])
        key = projects_key(repos_by_scope[scope], skip)
        since, how = api_since(st, scope, pk.login, mirror_has_commits, full, key)
        skip = frozenset(p.lower() for p in skip)
        try:
            api = pk.api()
            pr_s, rv_s = pk.provider.fetch(api, pk.login, repos_by_scope[scope], since, skip)
            is_s = pk.provider.fetch_issues(api, pk.login, repos_by_scope[scope], since, skip)
            pr_s, rv_s, is_s = ([to_utc_z(t) for t in ts] for ts in (pr_s, rv_s, is_s))
        except Exception as e:  # noqa: BLE001 - see docstring
            warn(f"PR/review fetch for {scope} failed, skipped: {_reason(e)}")
            continue
        prs += pr_s
        reviews += rv_s
        issues += is_s
        done[scope] = (pk.login, pk.source, key)
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
    return [(f"{API_PREFIX}pull-requests", Counter(prs)), (f"{API_PREFIX}reviews", Counter(reviews)),
            (f"{API_PREFIX}issues", Counter(issues))], done, started
