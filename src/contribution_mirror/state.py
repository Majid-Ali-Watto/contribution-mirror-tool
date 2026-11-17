"""The saved state that makes PR/review scans incremental (and when to fall back to a full scan)."""
import json
import os
from datetime import datetime, timedelta, timezone

from .settings import API_MARGIN_DAYS, API_WINDOW_DAYS

def state_path(log_dir):
    return os.path.join(log_dir, "api-state.json")

def api_since(log_dir, who, mirror_has_commits, full):
    """Start date for the API scan, plus a description for the log. Falls back to the full window whenever
    the saved state cannot be trusted, so nothing is skipped."""
    today = datetime.now(timezone.utc).date()
    full_start = today - timedelta(days=API_WINDOW_DAYS)
    if full:
        return full_start, "full (--full-api)"
    if not mirror_has_commits:
        return full_start, "full (mirror is empty)"
    try:
        with open(state_path(log_dir)) as f:
            st = json.load(f)
        if st.get("account") != who:
            return full_start, "full (work account changed)"
        last = datetime.fromisoformat(st["last_success"]).date()
        return max(full_start, last - timedelta(days=API_MARGIN_DAYS)), f"incremental (since {last} minus {API_MARGIN_DAYS}d)"
    except (OSError, ValueError, KeyError):
        return full_start, "full (no saved state)"

def save_state(log_dir, who, started):
    tmp = state_path(log_dir) + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"account": who, "last_success": started.strftime("%Y-%m-%dT%H:%M:%S+00:00")}, f)
    os.replace(tmp, state_path(log_dir))
