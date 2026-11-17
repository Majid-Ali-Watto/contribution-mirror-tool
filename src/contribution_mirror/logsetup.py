"""Logging and screen output: the shared logger, `say` (screen) and `warn` (screen + log)."""
import logging
import logging.handlers
import os
import sys

from .settings import LOG_BACKUPS, LOG_MAX_BYTES

log = logging.getLogger("mirror")

def close_log():
    """Release the log file. Windows cannot delete or move a file that is still open, so a run closes it when it ends."""
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()


def setup_log(log_dir):
    """Log to <log_dir>/mirror.log. Safe to call more than once (the scheduled run and the run it starts both do)."""
    os.makedirs(log_dir, exist_ok=True)
    close_log()
    h = logging.handlers.RotatingFileHandler(os.path.join(log_dir, "mirror.log"),
                                             maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
                                             encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False


def say(msg):
    """Screen only. The log file gets one SUMMARY line per run (plus warnings/errors)."""
    print(msg)

def warn(msg):
    """A problem that does not stop the run: on stderr and as a WARNING line in the log."""
    print(f"warning: {msg}", file=sys.stderr)
    log.warning("%s", msg)
