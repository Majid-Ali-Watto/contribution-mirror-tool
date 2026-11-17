"""Azure DevOps (dev.azure.com). Pull requests you opened, per listed repo. No review timestamps are exposed."""
from urllib.parse import quote, unquote

from ..timeutil import parse_iso
from .base import Provider, Token, basic, run_cli

AZURE_DEVOPS_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"   # the Azure AD application id of Azure DevOps
VERSION = {"api-version": "7.1"}


class AzureDevOps(Provider):
    name = "azure"
    label = "Azure DevOps"
    has_activity = True
    reviews = False
    work_envs = ("WORK_AZURE_DEVOPS_TOKEN",)
    env_vars = ("AZURE_DEVOPS_EXT_PAT",)
    api_url_env = "WORK_AZURE_DEVOPS_API_URL"

    def api_base(self, scope):                       # scope is 'dev.azure.com/<organization>'
        return f"https://{scope}"

    def headers(self, tok):
        return {"Authorization": f"Bearer {tok.secret}" if tok.kind == "bearer" else basic(f":{tok.secret}")}

    def git_header(self, tok):
        return self.headers(tok)["Authorization"]

    def cli_tokens(self, scope, only_user=None):
        out = run_cli(["az", "account", "get-access-token", "--resource", AZURE_DEVOPS_RESOURCE,
                       "--query", "accessToken", "-o", "tsv"])
        if out:
            yield Token("az", out, kind="bearer")

    def _me(self, api):
        return api.get("/_apis/connectionData", **{"api-version": "7.1-preview.1"})["authenticatedUser"]

    def whoami(self, api):
        me = self._me(api)
        return me.get("providerDisplayName") or me["id"]

    @staticmethod
    def _project_repo(repo):                         # 'org/project/repo' -> ('project', 'repo')
        _, project, name = repo.split("/", 2)
        return quote(unquote(project), safe=""), quote(unquote(name), safe="")

    def score(self, api, login, repo):
        project, name = self._project_repo(repo)
        try:
            api.get(f"/{project}/_apis/git/repositories/{name}", **VERSION)
        except Exception:
            return (0, 0)
        return (0, 1)

    def fetch(self, api, login, repos, since):
        me, out = self._me(api)["id"], []
        for repo in repos:
            project, name = self._project_repo(repo)
            skip = 0
            while True:
                # minTime + queryTimeRangeType=created: the server returns only the window (the date is still checked
                # below, so a server that ignores them is merely slower)
                data = api.get(f"/{project}/_apis/git/repositories/{name}/pullrequests", **VERSION, **{
                    "searchCriteria.status": "all", "searchCriteria.creatorId": me,
                    "searchCriteria.minTime": f"{since.isoformat()}T00:00:00Z",
                    "searchCriteria.queryTimeRangeType": "created", "$top": 100, "$skip": skip})
                batch = data.get("value", [])
                out += [pr["creationDate"] for pr in batch if parse_iso(pr["creationDate"]).date() >= since]
                if len(batch) < 100:
                    break
                skip += 100
        return out, []
