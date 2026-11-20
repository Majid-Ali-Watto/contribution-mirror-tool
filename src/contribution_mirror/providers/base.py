"""What every git host provider offers. Reading commits needs none of this (that is plain git, so every host
works); a provider adds: the right way to send a token, finding your account, and PR / review timestamps."""
import base64
import os
import subprocess
from typing import NamedTuple

from ..gitutil import child_env


class Token(NamedTuple):
    """A credential found on this machine."""
    source: str                 # where it came from, e.g. WORK_GITHUB_TOKEN, gh:alice, git-credential
    secret: str                 # the token (Bitbucket app passwords are 'user:password')
    explicit: bool = False      # the user chose it on purpose: used as is, without comparing accounts
    kind: str = "pat"           # 'pat' = static token, 'bearer' = short-lived OAuth / Azure AD token


def basic(user_and_secret):
    return "Basic " + base64.b64encode(user_and_secret.encode()).decode()


def run_cli(cmd, timeout=30, any_status=False):
    """Output of a vendor CLI (gh, glab, az), or None if it is missing or fails. With any_status=True the combined
    stdout+stderr is returned whatever the exit status (`gh auth status` lists accounts even when one is invalid)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           env=child_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    if any_status:
        return r.stdout + r.stderr
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def listed_owners(repos):
    """Lowercase owners (user, organisation, workspace, group) of the listed 'owner/repo' paths."""
    return {r.split("/", 1)[0].lower() for r in repos if "/" in r}


def owned_by(full_name, owners):
    """True if a pull request in repo `full_name` belongs to the listed work: its owner is one of `owners`. With no
    listed repos, or when the host did not say which repo it is, everything is kept (nothing is silently dropped)."""
    return not owners or not full_name or full_name.split("/", 1)[0].lower() in owners


class Provider:
    """A host that is not recognised: commits only. Subclasses override what their API supports."""
    name = "generic"
    label = "generic git host (commits only)"
    has_activity = False            # can it list PRs / reviews?
    reviews = False                 # ... and reviews, not just authored PRs?
    issues = False                  # ... and issues you opened?
    work_envs = ()                  # env vars that explicitly choose your account
    env_vars = ()                   # conventional env vars that may hold a token
    api_url_env = ""                # env var that overrides the API URL (self-hosted)
    git_user = "token"              # HTTP basic username when a token is handed to git

    def api_base(self, scope):
        """Base URL of the REST API for a scope (a host, or for Azure DevOps host/organisation)."""
        return None

    def api_url(self, scope):
        return (os.environ.get(self.api_url_env, "").strip() if self.api_url_env else "") or self.api_base(scope)

    def headers(self, tok):
        """HTTP headers that authenticate API calls with `tok`."""
        return {}

    def git_header(self, tok):
        """Value of the Authorization header that lets git talk https to this host with `tok`."""
        return basic(f"{self.git_user}:{tok.secret}")

    def env_token_vars(self, scope):
        """Conventional variables that may hold a token for `scope`."""
        return self.env_vars

    def cli_tokens(self, scope, only_user=None):
        """Tokens of this vendor's own CLI, if installed and logged in."""
        return iter(())

    def whoami(self, api):
        """Login of the account `api` is authenticated as. Raises if the token is not valid."""
        raise NotImplementedError

    def score(self, api, login, repo):
        """(commits by this account, can see the repo) for one listed repo: used to pick among several logins."""
        return (0, 0)

    def fetch(self, api, login, repos, since):
        """(timestamps of PRs you opened, timestamps of reviews you made) since a date, as ISO strings. Only activity
        in the listed repos' owners (or, where the host cannot say that, the listed projects) is returned."""
        raise NotImplementedError

    def fetch_issues(self, api, login, repos, since):
        """Timestamps of the issues you opened since a date (ISO strings), limited like fetch(). Hosts whose graph
        counts opened issues (GitHub) or that list them per user implement it."""
        return []
