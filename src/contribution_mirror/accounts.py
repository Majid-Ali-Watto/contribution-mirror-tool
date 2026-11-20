"""`accounts [--json] [--recheck]`: which login would be used for each host of your listed projects, without running a
sync. Uses exactly the logic of a real run (credentials.Creds), including the account remembered from the last run
(`--recheck` compares all logins again, like `--full-api`). Never prints a token, only where a login came from."""
import argparse
import json
import os
import sys

from . import providers
from .config import load_config
from .credentials import Creds
from .envfile import EnvFileError
from .logsetup import redact
from .repos import ReposFileError, group_by_scope, origin_urls, select_sources
from .settings import LOG_DIR, REPOS_FILE, command_name
from .state import known_hosts, load_state, preferred_accounts


def report(cfg, recheck=False, sources=None):
    """[{scope, provider, api, login, source, reviews, projects, error}] for every host of the listed projects.
    `sources` = (local repo paths, URLs) if the caller already has them, else they are read from the project list."""
    log_dir = cfg.log_dir or LOG_DIR
    st = load_state(log_dir)
    providers.remember(known_hosts(st))
    repos, remotes = sources or select_sources(None, cfg.repos_file or REPOS_FILE, os.path.abspath(cfg.mirror_dir))
    origins = origin_urls(repos)
    by_scope = group_by_scope(repos, remotes, origins)
    creds = Creds(by_scope, cfg.gh_user, None if recheck else preferred_accounts(st))
    out = []
    for scope in sorted(by_scope):
        provider = providers.provider_for(scope)
        row = {"scope": scope, "provider": provider.label, "api": provider.has_activity, "login": None,
               "source": None, "reviews": provider.reviews, "projects": len(by_scope[scope]), "error": None}
        if provider.has_activity and cfg.pr_reviews:
            try:
                pick = creds.pick(scope)
            except Exception as e:  # noqa: BLE001 - shown on the page, never a crash
                row["error"] = redact(str(e).strip().splitlines()[-1] if str(e).strip() else repr(e))
            else:
                if pick:
                    row.update(login=pick.login, source=pick.source)
        out.append(row)
    return {"hosts": out, "pr_reviews": cfg.pr_reviews, "local_without_remote": sum(1 for u in origins.values() if not u)}


def main(argv):
    p = argparse.ArgumentParser(prog=f"{command_name()} accounts")
    p.add_argument("--json", action="store_true", help="machine-readable output (used by the setup page)")
    p.add_argument("--recheck", action="store_true", help="compare all logins again instead of reusing the last one")
    a = p.parse_args(argv)
    try:
        result = report(load_config(), a.recheck)
    except (EnvFileError, ReposFileError) as e:
        print(json.dumps({"error": str(e)}) if a.json else e, file=sys.stdout if a.json else sys.stderr)
        return 1
    if a.json:
        print(json.dumps(result))
        return 0
    if not result["pr_reviews"]:
        print("PRs/reviews are off (MIRROR_PR_REVIEWS=off): only commits are mirrored, no login is needed.")
    for h in result["hosts"]:
        who = (f"{h['login']} via {h['source']}" if h["login"] else
               "commits only (no API for this host)" if not h["api"] else
               f"no usable login ({h['error']})" if h["error"] else "no login found that can see your projects")
        print(f"{h['scope']:<28} {h['provider']:<34} {who}")
    return 0
