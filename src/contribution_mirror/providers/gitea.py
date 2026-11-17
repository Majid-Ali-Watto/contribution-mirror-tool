"""Gitea, Forgejo and Codeberg. Pull requests you opened and the reviews you submitted."""
from ..timeutil import parse_iso
from .base import Provider


class Gitea(Provider):
    name = "gitea"
    label = "Gitea / Forgejo"
    has_activity = True
    reviews = True
    work_envs = ("WORK_GITEA_TOKEN",)
    env_vars = ("GITEA_TOKEN", "FORGEJO_TOKEN")
    api_url_env = "WORK_GITEA_API_URL"

    def api_base(self, scope):
        return f"https://{scope}/api/v1"

    def headers(self, tok):
        return {"Authorization": f"token {tok.secret}"}

    def whoami(self, api):
        return api.get("/user")["login"]

    def score(self, api, login, repo):
        try:
            api.get(f"/repos/{repo}")
        except Exception:
            return (0, 0)
        return (0, 1)

    def fetch(self, api, login, repos, since):
        after = f"{since.isoformat()}T00:00:00Z"
        search = lambda **extra: api.pages("/repos/issues/search", size_param="limit", size=50,
                                           type="pulls", state="all", created_after=after, **extra)
        prs = [i["created_at"] for i in search(created_by=login)]
        reviews = []
        for pr in search(reviewed="true"):               # pull requests reviewed by the authenticated user
            if (pr.get("user") or {}).get("login") == login:
                continue
            repo = pr["repository"]["full_name"]
            for r in api.get(f"/repos/{repo}/pulls/{pr['number']}/reviews") or []:
                when = r.get("submitted_at")
                if (r.get("user") or {}).get("login") == login and when and r.get("state") != "PENDING" \
                        and parse_iso(when).date() >= since:
                    reviews.append(when)
        return prs, reviews
