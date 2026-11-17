"""One run at a time: a manual sync, the login run and the daily run can otherwise overlap, and two writers on one
mirror make one of them fail. The lock is an operating-system file lock on <log_dir>/mirror.lock, so it is released
automatically if a run crashes or is killed (no stale lock to clean up). Re-entrant within one process (`sync`
holds it while it runs the core, which takes it too)."""
import os
import threading
from contextlib import contextmanager

_held = {}                     # lock path -> (open file, depth)
_guard = threading.Lock()


class RunLocked(RuntimeError):
    pass


def _lock(f):
    if os.name == "nt":
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(f):
    if os.name == "nt":
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@contextmanager
def run_lock(log_dir):
    path = os.path.abspath(os.path.join(log_dir, "mirror.lock"))
    with _guard:
        if path in _held:
            f, depth = _held[path]
            _held[path] = (f, depth + 1)
        else:
            os.makedirs(log_dir, exist_ok=True)
            f = open(path, "a+")
            try:
                _lock(f)
            except OSError:
                f.close()
                raise RunLocked("another run is in progress (a manual sync or the scheduled one); try again when it "
                                "has finished") from None
            _held[path] = (f, 1)
    try:
        yield
    finally:
        with _guard:
            f, depth = _held[path]
            if depth > 1:
                _held[path] = (f, depth - 1)
            else:
                del _held[path]
                try:
                    _unlock(f)
                finally:
                    f.close()
