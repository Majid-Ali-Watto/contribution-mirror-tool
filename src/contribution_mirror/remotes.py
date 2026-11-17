"""Git remote URLs: telling URLs from paths, owner/repo, the real host behind an ssh alias, and the scope
(host, or host/organisation on Azure DevOps) used to pick credentials."""
import re
import subprocess
from urllib.parse import urlsplit

from .gitutil import child_env

URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://|^[^/\s@]+@[^/\s:]+:")
# ssh-only front doors of the big hosts (port 443 variants and Azure's ssh hosts) -> the host the web/API lives on
SSH_PREFIXES = ("ssh.", "altssh.")
AZURE_SSH_HOSTS = {"ssh.dev.azure.com": "dev.azure.com", "vs-ssh.visualstudio.com": "dev.azure.com"}


def is_url(x):
    """True for a git URL (https://, ssh://, git@host:o/r), False for a local path."""
    return bool(URL_RE.match(x.strip()))


def url_host(url):
    u = url.strip()
    if "://" in u:
        return (urlsplit(u).hostname or "").lower()
    return u.split("@", 1)[-1].split(":", 1)[0].lower()


def is_azure_host(host):
    return host in ("dev.azure.com", *AZURE_SSH_HOSTS) or host.endswith(".visualstudio.com")


def repo_path(url):
    """'owner/repo' of a git remote URL, whatever the spelling: git@host:o/r.git, ssh://git@host:22/o/r,
    https://[user[:token]@]host/o/r(.git), or an ssh host alias. GitLab subgroups keep all their levels
    ('group/sub/repo'); Azure DevOps https and ssh forms both become 'organization/project/repo'."""
    u = url.strip()
    if "://" in u:
        u = u.split("://", 1)[1].split("@", 1)[-1]
        u = u.split("/", 1)[1] if "/" in u else ""
    else:
        u = u.split("@", 1)[-1].split(":", 1)[-1]
    u = u.strip("/")
    u = u[:-4] if u.endswith(".git") else u
    host = url_host(url)
    if is_azure_host(host):
        parts = u.split("/")
        if parts and parts[0] == "v3":                       # ssh: v3/<org>/<project>/<repo>
            parts = parts[1:]
        parts = [p for p in parts if p != "_git"]            # https: <org>/<project>/_git/<repo>
        if host.endswith(".visualstudio.com") and host != "vs-ssh.visualstudio.com":
            parts = [host.split(".")[0]] + parts             # https://<org>.visualstudio.com/<project>/_git/<repo>
        u = "/".join(parts)
    return u


def repo_key(url):
    """repo_path, lowercase: used to recognise the same repo spelled two ways."""
    return repo_path(url).lower()


_hosts = {}


def is_ssh_url(url):
    """True for the ssh forms (ssh://... and git@host:path), the only ones where an ssh host alias can be used."""
    u = url.strip()
    return u.lower().startswith(("ssh://", "git+ssh://", "ssh+git://")) or ("://" not in u and is_url(u))


def real_host(url):
    """The host a remote really points to. An ssh host alias is resolved through `ssh -G` (so `github-work`
    becomes github.com), and the ssh-only front doors (ssh.github.com, altssh.gitlab.com, ...) map to the main host.
    https URLs need no ssh lookup."""
    h = url_host(url)
    if h not in _hosts:
        real = h
        if h and is_ssh_url(url):
            try:
                out = subprocess.run(["ssh", "-G", "--", h], capture_output=True, text=True, timeout=10,
                                     stdin=subprocess.DEVNULL, env=child_env()).stdout
                m = re.search(r"^hostname (\S+)", out, re.M)
                if m:
                    real = m.group(1).lower()
            except (OSError, subprocess.TimeoutExpired):
                pass
        real = AZURE_SSH_HOSTS.get(real, real)
        _hosts[h] = next((real[len(p):] for p in SSH_PREFIXES if real.startswith(p)), real)
    return _hosts[h]


def scope_of(url):
    """Where credentials apply: the real host, except Azure DevOps where tokens belong to an organisation
    ('dev.azure.com/<org>')."""
    host = real_host(url)
    if host == "dev.azure.com" or host.endswith(".visualstudio.com"):
        return "dev.azure.com/" + repo_path(url).split("/", 1)[0]
    return host


def https_url(url):
    """The https clone URL of the same repository."""
    host = real_host(url)
    path = repo_path(url)
    if host == "dev.azure.com" or host.endswith(".visualstudio.com"):
        org, project, repo = path.split("/", 2)
        return f"https://dev.azure.com/{org}/{project}/_git/{repo}"
    return f"https://{host}/{path}.git"
