"""Finding a login automatically for any supported host (env token, vendor CLI, git credential helper) and
choosing the right account when several are present."""
import os
import re
import subprocess
import threading
from typing import NamedTuple

from .gitutil import child_env
from .httpapi import Api
from .providers import Token, host_of, provider_for, trusted
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
    """Password from the developer's git credential helper for https://host, if one is stored (usually a token).
    A credential helper is tied to the host it is asked about, so this is safe for any host."""
    try:
        r = subprocess.run(["git", "credential", "fill"], input=f"protocol=https\nhost={host}\n\n", text=True,
                           capture_output=True, timeout=15, encoding="utf-8", errors="replace",
                           env={**child_env(), "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"^password=(.+)$", r.stdout, re.M) if r.returncode == 0 else None
    return Token("git-credential", m.group(1).strip()) if m else None


def explicit_choice(gh_user=None):
    """True if the user chose an account on purpose (any WORK_*TOKEN variable, or a gh account name)."""
    names = ["WORK_TOKEN", "WORK_GITHUB_TOKEN", "WORK_GITLAB_TOKEN", "WORK_BITBUCKET_TOKEN",
             "WORK_AZURE_DEVOPS_TOKEN", "WORK_GITEA_TOKEN"]
    return bool(gh_user) or any(os.environ.get(n, "").strip() for n in names)


def env_tokens(provider, scope):
    """Tokens from conventional variables (GH_TOKEN, GITLAB_TOKEN...) that may be sent to `scope`. They are not tied
    to one host, so they only go to a host whose kind a person named (see providers.trusted)."""
    if not trusted(scope, provider):
        return []
    return [Token(v, os.environ[v].strip()) for v in provider.env_token_vars(scope) if os.environ.get(v, "").strip()]


def candidates(provider, scope, gh_user):
    """Tokens to try for `scope`, best-known first. An explicit choice (WORK_*TOKEN, or a gh account name) is
    the only candidate and is used as is; otherwise everything found is tried and the best account is picked.
    WORK_*TOKEN and the conventional variables are only offered to trusted hosts; the vendor CLI and the git
    credential helper are asked about this very host, so they are always tried."""
    if trusted(scope, provider):
        for var in ("WORK_TOKEN", *provider.work_envs):
            value = os.environ.get(var, "").strip()
            if value:
                yield Token(var, value, explicit=True)
                return
    if gh_user and provider.name == "github":
        yield from provider.cli_tokens(scope, gh_user)
        return
    yield from env_tokens(provider, scope)
    yield from provider.cli_tokens(scope, None)
    h = helper_token(host_of(scope))
    if h:
        yield h


class Creds:
    """Finds, once per scope, the account to use: the one that actually has your work commits in the projects
    you listed. This matters when several accounts are logged in (e.g. personal and work): the personal one is
    never picked just because it is the active one.

    `preferred` = {scope: (token source, account)} from the last successful run: if that same login is still
    available it is used straight away, without comparing every account again (a few API calls per token)."""
    def __init__(self, repos_by_scope, gh_user, preferred=None):
        self.repos, self.gh_user = repos_by_scope, gh_user
        self.preferred = dict(preferred or {})
        self.cache, self.lock = {}, threading.Lock()

    def pick(self, scope):
        """A Pick, or None (no usable login, or a host whose API we do not speak: commits are still mirrored)."""
        with self.lock:
            if scope not in self.cache:
                self.cache[scope] = self._choose(scope)
            return self.cache[scope]

    @staticmethod
    def _login(provider, scope, tok):
        """(Api, login) for a token, or None if the host does not accept it."""
        try:
            api = Api(provider.api_url(scope), provider.headers(tok))
            return api, provider.whoami(api)
        except Exception:  # noqa: BLE001 - invalid, expired or not a token for this host
            return None

    def _choose(self, scope):
        provider = provider_for(scope)
        if not provider.has_activity:
            return None
        pref = self.preferred.get(scope)
        seen, pending = set(), []
        for tok in candidates(provider, scope, self.gh_user):
            if tok.secret in seen:
                continue
            seen.add(tok.secret)
            if tok.explicit or (pref and tok.source == pref[0]):
                found = self._login(provider, scope, tok)
                if found and (tok.explicit or found[1] == pref[1]):
                    return Pick(tok, found[1], provider, scope)
                if found:
                    pending.append((tok, found))          # same source, another account now: compare it normally
                continue
            pending.append((tok, None))
        best = None
        for tok, found in pending:
            found = found or self._login(provider, scope, tok)
            if not found:
                continue
            api, login = found
            hits = access = 0
            for repo in self.repos.get(scope, [])[:3]:
                h, a = provider.score(api, login, repo)
                hits, access = hits + h, access + a
            if best is None or (hits, access) > best[0]:
                best = ((hits, access), Pick(tok, login, provider, scope))
        if best and (best[0] > (0, 0) or not self.repos.get(scope)):
            return best[1]
        return None
