"""Git host providers. `provider_for(scope)` picks one from the host (known hosts, GIT_HOST_PROVIDERS, then a
quick probe of the server's API); an unknown host gets the generic provider, which still mirrors commits."""
import http.client
import os
import ssl

from .azure import AzureDevOps
from .base import Provider, Token
from .bitbucket import Bitbucket
from .gitea import Gitea
from .github import GitHub
from .gitlab import GitLab

__all__ = ["GENERIC", "PROVIDERS", "Provider", "Token", "host_of", "probed", "provider_for", "remember", "trusted"]

PROVIDERS = {p.name: p for p in (GitHub(), GitLab(), Bitbucket(), AzureDevOps(), Gitea())}
GENERIC = Provider()

KNOWN_HOSTS = {"github.com": "github", "gitlab.com": "gitlab", "bitbucket.org": "bitbucket",
               "dev.azure.com": "azure", "codeberg.org": "gitea", "gitea.com": "gitea", "try.gitea.io": "gitea"}

_cache = {}       # scope -> provider
_learned = {}     # host -> provider name or 'generic', from probes (seeded from, and saved to, the state file)
_probed = {}      # what this run learned by probing (to be saved)


def host_of(scope):
    """The host part of a scope ('dev.azure.com/org' -> 'dev.azure.com')."""
    return scope.split("/", 1)[0].lower()


def overrides():
    """GIT_HOST_PROVIDERS='git.corp.com=gitlab,code.corp.com=gitea' -> {host: provider name}."""
    out = {}
    for item in os.environ.get("GIT_HOST_PROVIDERS", "").split(","):
        if "=" in item:
            host, name = (x.strip().lower() for x in item.split("=", 1))
            if host and name:
                out[host] = name
    return out


def probe(host):
    """Ask an unknown server what it is, from public endpoints. A provider name, 'generic' if it answered and is
    none of them, or None if it cannot be told now (offline, firewall...): only a definite answer is remembered."""
    for path, name, ok in (("/api/v4/version", "gitlab", (200, 401)), ("/api/v1/version", "gitea", (200,)),
                           ("/api/v3/meta", "github", (200, 401))):
        conn = None
        try:
            conn = http.client.HTTPSConnection(host, timeout=5, context=ssl.create_default_context())
            conn.request("GET", path, headers={"User-Agent": "contribution-mirror"})
            r = conn.getresponse()
            body = r.read(2000)
        except (OSError, http.client.HTTPException, ssl.SSLError):
            return None
        finally:
            if conn is not None:
                conn.close()
        if r.status in ok and body.lstrip().startswith((b"{", b"[")):
            return name
    return "generic"


def remember(hosts):
    """Seed what earlier runs learned by probing ({host: provider name})."""
    _learned.update({h.lower(): n for h, n in hosts.items()})


def probed():
    """{host: provider name} learned by probing during this run (for the state file)."""
    return dict(_probed)


def provider_for(scope, use_probe=True):
    host = host_of(scope)
    if host.endswith(".visualstudio.com"):
        host = "dev.azure.com"
    if scope not in _cache:
        name = overrides().get(host) or KNOWN_HOSTS.get(host) or _learned.get(host)
        if name is None and use_probe:
            name = probe(host)
            if name is not None:
                _learned[host] = _probed[host] = name
        _cache[scope] = PROVIDERS.get(name, GENERIC)
    return _cache[scope]


def trusted(scope, provider=None):
    """True if the host's kind was named by a person (a well-known host, or GIT_HOST_PROVIDERS) or its API address
    was set by the user (WORK_*_API_URL). Only such hosts get tokens that are not tied to a host (WORK_TOKEN,
    WORK_<HOST>_TOKEN, GITLAB_TOKEN, ...): a server identified only by what it says about itself never does."""
    provider = provider or provider_for(scope)
    if provider.api_url_env and os.environ.get(provider.api_url_env, "").strip():
        return True
    host = host_of(scope)
    if host.endswith(".visualstudio.com"):
        host = "dev.azure.com"
    return host in KNOWN_HOSTS or host in overrides()
