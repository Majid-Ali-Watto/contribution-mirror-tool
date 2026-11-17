"""Finding a login automatically for any supported host (env token, vendor CLI, git credential helper) and
choosing the right account when several are present."""
import os
import re
import subprocess
import threading
from typing import NamedTuple

from .httpapi import Api
from .providers import Token, host_of, provider_for
from .providers.base import Provider


class Pick(NamedTuple):
    """The account chosen for a scope."""
    token: Token
    login: str
    provider: Provider
    scope: str

    @property
    def source(self):
        return self.token.source

    def api(self):
        return Api(self.provider.api_url(self.scope), self.provider.headers(self.token))


def helper_token(host):
    """Password from the developer's git credential helper for https://host, if one is stored (usually a token)."""
    try:
        r = subprocess.run(["git", "credential", "fill"], input=f"protocol=https\nhost={host}\n\n", text=True,
                           capture_output=True, timeout=15, encoding="utf-8", errors="replace",
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"^password=(.+)$", r.stdout, re.M) if r.returncode == 0 else None
    return Token("git-credential", m.group(1).strip()) if m else None


def explicit_choice(gh_user=None):
    """True if the user chose an account on purpose (any WORK_*TOKEN variable, or a gh account name)."""
    names = ["WORK_TOKEN", "WORK_GITHUB_TOKEN", "WORK_GITLAB_TOKEN", "WORK_BITBUCKET_TOKEN",
             "WORK_AZURE_DEVOPS_TOKEN", "WORK_GITEA_TOKEN"]
    return bool(gh_user) or any(os.environ.get(n, "").strip() for n in names)


def candidates(provider, scope, gh_user):
    """Tokens to try for `scope`, best-known first. An explicit choice (WORK_*TOKEN, or a gh account name) is
    the only candidate and is used as is; otherwise everything found is tried and the best account is picked."""
    for var in ("WORK_TOKEN", *provider.work_envs):
        value = os.environ.get(var, "").strip()
        if value:
            yield Token(var, value, explicit=True)
            return
    if gh_user and provider.name == "github":
        yield from provider.cli_tokens(scope, gh_user)
        return
    for var in provider.env_vars:
        value = os.environ.get(var, "").strip()
        if value:
            yield Token(var, value)
    yield from provider.cli_tokens(scope, None)
    h = helper_token(host_of(scope))
    if h:
        yield h


class Creds:
    """Finds, once per scope, the account to use: the one that actually has your work commits in the projects
    you listed. This matters when several accounts are logged in (e.g. personal and work): the personal one is
    never picked just because it is the active one."""
    def __init__(self, repos_by_scope, gh_user):
        self.repos, self.gh_user = repos_by_scope, gh_user
        self.cache, self.lock = {}, threading.Lock()

    def pick(self, scope):
        """A Pick, or None (no usable login, or a host whose API we do not speak: commits are still mirrored)."""
        with self.lock:
            if scope not in self.cache:
                self.cache[scope] = self._choose(scope)
            return self.cache[scope]

    def _choose(self, scope):
        provider = provider_for(scope)
        if not provider.has_activity:
            return None
        seen, best = set(), None
        for tok in candidates(provider, scope, self.gh_user):
            if tok.secret in seen:
                continue
            seen.add(tok.secret)
            try:
                api = Api(provider.api_url(scope), provider.headers(tok))
                login = provider.whoami(api)
            except Exception:
                continue                           # invalid, expired or not a token for this host
            pick = Pick(tok, login, provider, scope)
            if tok.explicit:
                return pick
            hits = access = 0
            for repo in self.repos.get(scope, [])[:3]:
                h, a = provider.score(api, login, repo)
                hits, access = hits + h, access + a
            if best is None or (hits, access) > best[0]:
                best = ((hits, access), pick)
        if best and (best[0] > (0, 0) or not self.repos.get(scope)):
            return best[1]
        return None
