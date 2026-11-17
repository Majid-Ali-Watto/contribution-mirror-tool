"""Bitbucket Cloud (bitbucket.org). Pull requests you opened. Bitbucket exposes no review timestamps, so no reviews."""
from urllib.parse import quote

from ..timeutil import parse_iso
from .base import Provider, basic


class Bitbucket(Provider):
    name = "bitbucket"
    label = "Bitbucket Cloud"
    has_activity = True
    reviews = False
    work_envs = ("WORK_BITBUCKET_TOKEN",)
    env_vars = ("BITBUCKET_TOKEN",)
    api_url_env = "WORK_BITBUCKET_API_URL"

    def api_base(self, scope):
        return "https://api.bitbucket.org"

    def headers(self, tok):
        # app password / API token: "username:secret" with basic auth; repository/workspace access token: bearer
        return {"Authorization": basic(tok.secret) if ":" in tok.secret else f"Bearer {tok.secret}"}

    def git_header(self, tok):
        return basic(tok.secret if ":" in tok.secret else f"x-token-auth:{tok.secret}")

    def whoami(self, api):
        u = api.get("/2.0/user")
        return u.get("nickname") or u.get("username") or u["account_id"]

    def score(self, api, login, repo):
        try:
            api.get(f"/2.0/repositories/{repo}")
        except Exception:
            return (0, 0)
        return (0, 1)

    def fetch(self, api, login, repos, since):
        uuid = api.get("/2.0/user")["uuid"]
        path, params, out = f"/2.0/pullrequests/{quote(uuid, safe='')}", {
            "state": ["OPEN", "MERGED", "DECLINED", "SUPERSEDED"], "sort": "-created_on", "pagelen": 50}, []
        while path:
            data = api.get(path, **params)
            params = {}                                       # the `next` link already carries them
            for pr in data.get("values", []):
                if parse_iso(pr["created_on"]).date() < since:
                    return out, []                            # newest first: everything after is older
                out.append(pr["created_on"])
            path = data.get("next")
        return out, []
