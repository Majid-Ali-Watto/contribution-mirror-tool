"""GitHub (github.com and GitHub Enterprise Server). PRs opened and every review submitted."""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import math

from ..logsetup import warn
from ..settings import API_WORKERS, SEARCH_CAP
from .base import Provider, Token, listed_owners, owned_by, run_cli


def search_items(api, base_q, field, a, b):
    """All PRs matching `base_q` with `field` (created/updated) in the inclusive date range [a, b].
    GitHub search stops at 1000 results, so a range that would overflow is split in two (nothing is dropped)."""
    q = f"{base_q} {field}:{a.isoformat()}..{b.isoformat()}"
    first = api.get("/search/issues", q=q, per_page=100, page=1)
    total = first["total_count"]
    if total > SEARCH_CAP and a < b:
        mid = a + (b - a) // 2
        return search_items(api, base_q, field, a, mid) + search_items(api, base_q, field, mid + timedelta(days=1), b)
    if total > SEARCH_CAP:
        warn(f"more than {SEARCH_CAP} results on {a}: some may be missing")
    items = first["items"]
    for page in range(2, math.ceil(min(total, SEARCH_CAP) / 100) + 1):
        items += api.get("/search/issues", q=q, per_page=100, page=page)["items"]
    return items


def pr_reviews(api, repo, number, login, since):
    """Submission times of `login`'s reviews on one PR (all pages)."""
    out, page = [], 1
    while True:
        batch = api.get(f"/repos/{repo}/pulls/{number}/reviews", per_page=100, page=page)
        out += [r["submitted_at"] for r in batch
                if (r.get("user") or {}).get("login") == login and r.get("submitted_at")
                and r["submitted_at"][:10] >= since.isoformat()]
        if len(batch) < 100:
            return out
        page += 1


class GitHub(Provider):
    name = "github"
    label = "GitHub"
    has_activity = True
    reviews = True
    work_envs = ("WORK_GITHUB_TOKEN",)
    env_vars = ("GH_TOKEN", "GITHUB_TOKEN")
    enterprise_env_vars = ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
    api_url_env = "WORK_GITHUB_API_URL"
    git_user = "x-access-token"

    def api_base(self, scope):
        return "https://api.github.com" if scope == "github.com" else f"https://{scope}/api/v3"

    def headers(self, tok):
        return {"Authorization": f"Bearer {tok.secret}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}

    def env_token_vars(self, scope):
        """GH_TOKEN / GITHUB_TOKEN are github.com tokens (as for the gh CLI); a GitHub Enterprise server has its own."""
        return self.env_vars if scope == "github.com" else self.enterprise_env_vars

    def cli_tokens(self, scope, only_user=None):
        """Every account logged in to the gh CLI for this host (or just `only_user`)."""
        status = run_cli(["gh", "auth", "status", "--hostname", scope], any_status=True)
        if status is None:
            return                                  # gh is not installed
        found = list(dict.fromkeys(re.findall(r"Logged in to \S+ (?:account|as) (\S+)", status)))
        for acc in ([only_user] if only_user else found or [None]):
            out = run_cli(["gh", "auth", "token", "--hostname", scope] + (["--user", acc] if acc else []))
            if out:
                yield Token(f"gh:{acc}" if acc else "gh", out, explicit=bool(only_user))

    def whoami(self, api):
        return api.get("/user")["login"]

    def score(self, api, login, repo):
        try:
            commits = api.get(f"/repos/{repo}/commits", author=login, per_page=1)
        except Exception:
            return (0, 0)                          # this account cannot see that repo
        return (1 if commits else 0, 1)

    def fetch(self, api, login, repos, since):
        end = datetime.now(timezone.utc).date() + timedelta(days=1)   # +1: tolerate clock/timezone skew
        with ThreadPoolExecutor(2) as ex:
            f_prs = ex.submit(search_items, api, f"author:{login} type:pr", "created", since, end)
            f_rev = ex.submit(search_items, api, f"reviewed-by:{login} type:pr -author:{login}", "updated", since, end)
            prs, reviewed = f_prs.result(), f_rev.result()
        owners = listed_owners(repos)
        repo_of = lambda i: (i.get("repository_url") or "").split("/repos/", 1)[-1] if i.get("repository_url") else ""
        pr_times = [i["created_at"] for i in prs if owned_by(repo_of(i), owners)]
        jobs = [(repo_of(i), i["number"]) for i in reviewed if owned_by(repo_of(i), owners)]
        del prs, reviewed
        with ThreadPoolExecutor(API_WORKERS) as ex:
            review_times = [t for ts in ex.map(lambda j: pr_reviews(api, j[0], j[1], login, since), jobs) for t in ts]
        return pr_times, review_times
