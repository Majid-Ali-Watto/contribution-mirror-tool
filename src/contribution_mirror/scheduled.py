"""`scheduled`: what the OS scheduler runs. Wait for the network, sync every profile (or the one chosen with
--profile / MIRROR_ENV_FILE), and report each outcome with a notification and, on failure, an ERROR line in the log."""
import argparse
import contextlib
import os
import socket
import sys
import time

from . import sync
from .config import list_profiles, load_config, profile_environment
from .envfile import EnvFileError
from .logsetup import close_log, log, redact, setup_log
from .notify import notify
from .remotes import real_host
from .runlock import RunLocked
from .settings import LOG_DIR


def network_up(hosts, ports=(443, 22), timeout=5):
    """True if a TCP connection to any host succeeds on any port (443 = https, 22 = ssh), so it works for both."""
    for host in hosts:
        for port in ports:
            try:
                socket.create_connection((host, port), timeout=timeout).close()
                return True
            except OSError:
                continue
    return False


def wait_for_network(hosts, tries=30, delay=10, sleep=time.sleep, check=network_up):
    """Wait up to tries * delay seconds (about 5 minutes) for the network, e.g. right after boot."""
    for i in range(tries):
        if check(hosts):
            return True
        if i < tries - 1:
            sleep(delay)
    return False


def failure(reason, detail="", log_dir=LOG_DIR):
    """Report a failed run: stderr, an ERROR line in the log, and a critical notification."""
    reason, detail = redact(reason), redact(detail)
    text = f"{reason}{' (' + detail + ')' if detail else ''}"
    print(f"FAILED: {text}", file=sys.stderr)
    try:
        setup_log(log_dir)
        log.error("scheduled sync failed: %s%s", reason, f" -- {detail}" if detail else "")
    except OSError:
        pass
    finally:
        close_log()
    notify("Contribution mirror sync failed", f"{reason}\n{detail}\nLog: {os.path.join(log_dir, 'mirror.log')}".strip(),
           urgent=True)


def success_message(result):
    """'N new commit(s) mirrored and pushed' plus the sources that got any, or 'Up to date'."""
    if result.created <= 0:
        return "Up to date, no new commits."
    lines = [f"{name}: {n}" for name, n in result.by_source if n]
    return "\n".join([f"{result.created} new commit(s) mirrored and pushed", *lines])


def run_profile(name, network_checked):
    """Sync one profile ('' = the default). Returns (exit code, network): network is True once it was reached,
    False if it could not be reached (the other profiles would fail the same way), None if not checked yet."""
    which = f" (profile {name})" if name else ""
    try:
        cfg = load_config()
    except EnvFileError as e:
        failure(f"cannot read your settings (.env){which}", str(e))
        return 1, network_checked or None
    log_dir = cfg.log_dir or LOG_DIR
    if not network_checked:
        hosts = list(dict.fromkeys([real_host(cfg.expected_remote), "github.com"]))
        if not wait_for_network(hosts):
            failure("network not reachable after 5 minutes (will retry at next run)", log_dir=log_dir)
            return 1, False
    print(f"scheduled sync starting{which}")
    try:
        result, _ = sync.run_sync(cfg)
    except RunLocked as e:                      # another run is doing the work right now: not a failure
        print(e)
        return 0, True
    except Exception as e:  # noqa: BLE001 - every failure must be reported, never swallowed
        failure(f"sync failed{which} (commits may not be pushed)", str(e), log_dir)
        return 1, True
    notify(f"Contribution mirror sync OK{which}", success_message(result))
    return 0, True


def main(argv):
    p = argparse.ArgumentParser(prog="mirror.py scheduled")
    p.add_argument("--startup-delay", type=int, default=0, help="seconds to wait first (for login triggers)")
    a = p.parse_args(argv)
    if a.startup_delay:
        time.sleep(a.startup_delay)
    # a settings file or profile chosen for this run: only that one; otherwise every profile, one after the other
    chosen = os.environ.get("MIRROR_ENV_FILE", "").strip() or os.environ.get("MIRROR_PROFILE", "").strip()
    profiles = [os.environ.get("MIRROR_PROFILE", "").strip()] if chosen else (list_profiles() or [""])
    code, network = 0, None
    for name in profiles:
        with contextlib.nullcontext() if chosen else profile_environment(name):
            result, network = run_profile(name, bool(network))
        code = max(code, result)
        if network is False:
            break                               # offline: the other profiles would fail the same way
    return code
