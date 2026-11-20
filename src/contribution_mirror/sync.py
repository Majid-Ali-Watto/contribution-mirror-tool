"""`sync`: mirror new activity (core run) and push it. This is what you run by hand and what the schedule runs."""
import os
import sys

from . import cli
from .config import load_config
from .envfile import EnvFileError
from .logsetup import redact, warn
from .mirror_repo import ensure_repo, reconcile
from .push import PushError, ensure_remote, fetch_mirror, mirror_host_note, push_mirror
from .repos import ReposFileError
from .runlock import RunLocked, run_lock
from .settings import LOG_DIR, SIBLINGS


def resolve_folders(names):
    """Folder names are looked up as siblings of the project folder first (in a checkout), then as paths."""
    out = []
    for name in names:
        for candidate in ([os.path.join(SIBLINGS, name)] if SIBLINGS else []) + [name]:
            if os.path.isdir(candidate):
                out.append(os.path.abspath(candidate))
                break
        else:
            raise FileNotFoundError(f"folder not found: {name}")
    return out


def core_args(cfg, folders=()):
    args = ["--work-email", *cfg.work_emails, "--mirror", cfg.mirror_dir,
            "--name", cfg.personal_name, "--email", cfg.personal_email]
    if cfg.gh_user:
        args += ["--gh-user", cfg.gh_user]
    if not cfg.pr_reviews:
        args.append("--no-api")
    if cfg.log_dir:
        args += ["--log-dir", cfg.log_dir]
    if cfg.clone_cache:
        args += ["--clone-cache", cfg.clone_cache]
    if cfg.repos_file:
        args += ["--repos-file", cfg.repos_file]
    if folders:
        args += ["--src", *folders]
    return args


def catch_up_with_remote(cfg):
    """Before comparing: connect the mirror to your personal repo, fetch it, and make local `main` contain what is
    already there (another computer may have pushed). Offline is only a warning: the push will tell."""
    ensure_repo(cfg.mirror_dir)
    ensure_remote(cfg)
    err = fetch_mirror(cfg)
    if err:
        warn(f"could not fetch your personal repo, comparing with the local mirror only: {err}")
        return
    done = reconcile(cfg.mirror_dir, cfg.personal_name, cfg.personal_email)
    if done:
        print(f"mirror: {done}")


def run_sync(cfg, folders=(), dry_run=False, dates_out=""):
    """Returns (RunResult, push outcome or None). Raises PushError / RuntimeError / RunLocked on failure.
    A dry run changes nothing, not even the mirror's remote-tracking ref (so it compares with the local mirror)."""
    note = mirror_host_note(cfg.expected_remote)
    if note:
        print(note)
    extra = ["--dates-out", dates_out] if dates_out else []
    if dry_run:
        return cli.run(core_args(cfg, folders) + extra), None
    with run_lock(cfg.log_dir or LOG_DIR):
        catch_up_with_remote(cfg)
        result = cli.run(core_args(cfg, folders) + extra + ["--apply"])
        outcome = push_mirror(cfg)
    print(outcome)
    return result, outcome


def main(argv):
    """Command line of `sync`: [--dry-run] [--dates-out FILE] [folder ...]. Returns the exit code."""
    import argparse
    p = argparse.ArgumentParser(prog="mirror.py sync", description="Mirror new activity and push it.")
    p.add_argument("--dry-run", action="store_true", help="preview only: no commits, no push")
    p.add_argument("--dates-out", default="", help="also write the dates per day to this JSON file (calendar view)")
    p.add_argument("folders", nargs="*", help="only these folders (sibling folder names, or paths)")
    a = p.parse_args(argv)
    try:
        cfg = load_config()
        folders = resolve_folders(a.folders)
        run_sync(cfg, folders, a.dry_run, a.dates_out)
    except (EnvFileError, FileNotFoundError, ReposFileError, RunLocked, RuntimeError) as e:  # PushError too
        print(redact(e), file=sys.stderr)
        return 1
    return 0
