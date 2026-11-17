"""GitLab (gitlab.com and self-managed). Merge requests you opened and merge requests you approved."""
from datetime import timedelta
from urllib.parse import quote

from .base import Provider, Token, run_cli


class GitLab(Provider):
    name = "gitlab"
    label = "GitLab"
    has_activity = True
    reviews = True                  # approvals: GitLab has no "submitted review" like GitHub
    work_envs = ("WORK_GITLAB_TOKEN",)
    env_vars = ("GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN")
    api_url_env = "WORK_GITLAB_API_URL"
    git_user = "oauth2"

    def api_base(self, scope):
        return f"https://{scope}/api/v4"

    def headers(self, tok):
        return {"Authorization": f"Bearer {tok.secret}"}

    def cli_tokens(self, scope, only_user=None):
        out = run_cli(["glab", "config", "get", "token", "--host", scope])
        if out:
            yield Token("glab", out)

    def whoami(self, api):
        return api.get("/user")["username"]

    def score(self, api, login, repo):
        try:
            api.get(f"/projects/{quote(repo, safe='')}")
        except Exception:
            return (0, 0)
        return (0, 1)                                # GitLab cannot filter commits by account name: access only

    def fetch(self, api, login, repos, since):
        uid = api.get("/user")["id"]
        after = (since - timedelta(days=1)).isoformat()     # the events API's `after` is exclusive
        def events(action):
            return [e["created_at"] for e in api.pages(f"/users/{uid}/events", action=action,
                                                       target_type="merge_request", after=after)]
        return events("created"), events("approved")
