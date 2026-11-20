"""The saved state (api-state.json in the log folder): per host, the account used and when its PR/review scan last
succeeded (so the next scan is incremental), and what each self-hosted server turned out to be (so it is not probed
every run).

    {"version": 2,
     "scopes": {"github.com": {"account": "me-work", "source": "gh:me-work", "last_success": "2026-...+00:00"}},
     "hosts": {"git.corp.example": "gitlab"}}

It holds no token and no repo name. Anything unreadable, or the older single-account format, is simply ignored:
the next scan is then a full one, so nothing is ever skipped."""
import json
import os
from datetime import datetime, timedelta, timezone

from .fsutil import write_private
from .settings import API_MARGIN_DAYS, API_WINDOW_DAYS

VERSION = 2


def state_path(log_dir):
    """api-state.json, or api-state-NAME.json for profile NAME (profiles share the log folder, not their accounts)."""
    profile = os.environ.get("MIRROR_PROFILE", "").strip()
    return os.path.join(log_dir, f"api-state-{profile}.json" if profile else "api-state.json")


def load_state(log_dir):
    try:
        with open(state_path(log_dir), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {}
    return st if isinstance(st, dict) and st.get("version") == VERSION else {}


def preferred_accounts(st):
    """{scope: (token source, account)} chosen by the last successful run."""
    out = {}
    for scope, entry in (st.get("scopes") or {}).items():
        if isinstance(entry, dict) and entry.get("source") and entry.get("account"):
            out[scope] = (entry["source"], entry["account"])
    return out


def known_hosts(st):
    """{host: provider name} learned by probing in earlier runs."""
    hosts = st.get("hosts") or {}
    return {h: n for h, n in hosts.items() if isinstance(h, str) and isinstance(n, str)}


def api_since(st, scope, account, mirror_has_commits, full):
    """Start date of the API scan for one host, plus a description for the screen. Falls back to the full window
    whenever the saved state cannot be trusted, so nothing is skipped."""
    today = datetime.now(timezone.utc).date()
    full_start = today - timedelta(days=API_WINDOW_DAYS)
    if full:
        return full_start, "full (--full-api)"
    if not mirror_has_commits:
        return full_start, "full (mirror is empty)"
    entry = (st.get("scopes") or {}).get(scope)
    if not isinstance(entry, dict):
        return full_start, "full (no saved state)"
    if entry.get("account") != account:
        return full_start, "full (work account changed)"
    try:
        last = datetime.fromisoformat(entry["last_success"]).date()
    except (KeyError, TypeError, ValueError):
        return full_start, "full (no saved state)"
    return max(full_start, last - timedelta(days=API_MARGIN_DAYS)), f"incremental (since {last} minus {API_MARGIN_DAYS}d)"


def save_state(log_dir, done, started, hosts=None):
    """Record the hosts whose scan succeeded (`done` = {scope: (account, token source)}) and probe results. Other
    hosts keep what they had, so a host that failed this time is fully re-checked from its own last success."""
    st = load_state(log_dir)
    st["version"] = VERSION
    scopes = st.setdefault("scopes", {})
    for scope, (account, source) in done.items():
        scopes[scope] = {"account": account, "source": source,
                         "last_success": started.strftime("%Y-%m-%dT%H:%M:%S+00:00")}
    if hosts:
        st.setdefault("hosts", {}).update(hosts)
    write_private(state_path(log_dir), json.dumps(st, indent=1, sort_keys=True))   # names logins and hosts
