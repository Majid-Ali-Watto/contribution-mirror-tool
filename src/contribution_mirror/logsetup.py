"""Logging and screen output: the shared logger, `say` (screen) and `warn` (screen + log)."""
import logging
import logging.handlers
import os
import re
import sys

from .fsutil import make_private, private_dir
from .settings import LOG_BACKUPS, LOG_MAX_BYTES

log = logging.getLogger("mirror")

# user[:password-or-token]@ in an http(s) URL (user:token@ or token@ before the host, also inside git+https URLs): all
# of it, since a token is often the user part alone
_HTTP_CREDENTIALS = re.compile(r"(https?://)[^/@\s]+@", re.I)
# user:password@ in any other URL (ssh, ...): only the password, the user name (usually `git`) is useful
_URL_PASSWORD = re.compile(r"([a-z][a-z0-9+.-]*://[^/@\s:]+):[^/@\s]+@", re.I)
# an HTTP Authorization header value, as a git trace or an error message may print it
_AUTH_HEADER = re.compile(r"(Authorization:\s*(?:Basic|Bearer|token)\s+)\S+", re.I)


def redact(text):
    """`text` with every credential in a URL or Authorization header replaced by ***: safe for the screen, the log
    and a notification."""
    text = _HTTP_CREDENTIALS.sub(r"\1***@", str(text))
    return _AUTH_HEADER.sub(r"\1***", _URL_PASSWORD.sub(r"\1:***@", text))


class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """The log names work repos and hosts: every log file (also each new one after a rotation) is owner-only."""
    def _open(self):
        os.close(os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
        make_private(self.baseFilename)
        return super()._open()


def close_log():
    """Release the log file. Windows cannot delete or move a file that is still open, so a run closes it when it ends."""
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()


def setup_log(log_dir):
    """Log to <log_dir>/mirror.log. Safe to call more than once (the scheduled run and the run it starts both do)."""
    private_dir(log_dir)
    close_log()
    for name in os.listdir(log_dir):                     # older files, written before they were made private
        if name.startswith(("mirror.log", "api-state")):
            make_private(os.path.join(log_dir, name))
    h = _PrivateRotatingFileHandler(os.path.join(log_dir, "mirror.log"),
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
    msg = redact(msg)
    print(f"warning: {msg}", file=sys.stderr)
    log.warning("%s", msg)
