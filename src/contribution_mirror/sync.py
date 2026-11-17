"""`sync`: mirror new activity (core run) and push it. This is what you run by hand and what the schedule runs."""
import os
import sys

from . import cli
from .config import load_config
from .envfile import EnvFileError
from .push import PushError, mirror_host_note, push_mirror
from .settings import SIBLINGS


def resolve_folders(names):
    """Folder names are looked up as siblings of the project folder first, then as paths."""
    out = []
    for name in names:
        for candidate in (os.path.join(SIBLINGS, name), name):
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
    if folders:
        args += ["--src", *folders]
    return args


def run_sync(cfg, folders=(), dry_run=False):
    """Returns (RunResult, push outcome or None). Raises PushError / RuntimeError on failure."""
    note = mirror_host_note(cfg.expected_remote)
    if note:
        print(note)
    if dry_run:
        return cli.run(core_args(cfg, folders)), None
    result = cli.run(core_args(cfg, folders) + ["--apply"])
    outcome = push_mirror(cfg)
    print(outcome)
    return result, outcome


def main(argv):
    """Command line of `sync`: [--dry-run] [folder ...]. Returns the exit code."""
    dry = "--dry-run" in argv
    names = [a for a in argv if a != "--dry-run"]
    try:
        cfg = load_config()
        folders = resolve_folders(names)
        run_sync(cfg, folders, dry)
    except (EnvFileError, FileNotFoundError, PushError) as e:
        print(e, file=sys.stderr)
        return 1
    return 0
