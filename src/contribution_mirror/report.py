"""What a run prints and logs: per-source attribution, totals, and the one-line SUMMARY."""
import os
from dataclasses import dataclass, field

from .logsetup import log, say


@dataclass
class RunResult:
    """What a run did, for the commands built on top of it (sync, scheduled)."""
    created: int = 0                                           # commits created (or that would be, in a dry run)
    by_source: list = field(default_factory=list)              # [(source name, new commits)]
    dry_run: bool = True


def attribute(per_repo, existing):
    """Each source first claims timestamps already in the mirror; the rest are new, so totals equal the number of
    commits to create exactly. Prints one line per source and returns [(name, new), ...]."""
    left, by_repo = existing.copy(), []
    for r, c in per_repo:
        covered = c & left
        left -= covered
        new = sum((c - covered).values())
        by_repo.append((os.path.basename(r).replace('github-api:', '').replace('remote:', ''), new))
        say(f"repo {r}: matching={sum(c.values())} already_mirrored={sum(covered.values())} new={new}")
    return by_repo


def print_totals(n_sources, wanted, existing, todo):
    say(f"source repos scanned : {n_sources}")
    say(f"matching contributions: {sum(wanted.values())}")
    say(f"already mirrored     : {sum((wanted & existing).values())}")
    say(f"to create            : {sum(todo.values())}")


def summary(apply, n_sources, wanted, existing, todo, by_repo, verb):
    """Print and log the SUMMARY line. `verb` is would_create (dry run) or created."""
    line = (f"SUMMARY mode={'apply' if apply else 'dry-run'} repos={n_sources} "
            f"matching={sum(wanted.values())} already_mirrored={sum((wanted & existing).values())} "
            f"{verb}={sum(todo.values())} | new per repo: {' '.join(f'{n}={c}' for n, c in by_repo) or '-'}")
    print(line)
    log.info(line)
