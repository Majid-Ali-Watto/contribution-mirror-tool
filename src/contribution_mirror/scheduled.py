"""`scheduled`: what the OS scheduler runs. Wait for the network, sync, and report the outcome with a
notification and, on failure, an ERROR line in the log."""
import argparse
import os
import socket
import sys
import time

from . import sync
from .config import load_config
from .envfile import EnvFileError
from .logsetup import log, setup_log
from .notify import notify
from .remotes import real_host
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
    text = f"{reason}{' (' + detail + ')' if detail else ''}"
    print(f"FAILED: {text}", file=sys.stderr)
    try:
        setup_log(log_dir)
        log.error("scheduled sync failed: %s%s", reason, f" -- {detail}" if detail else "")
    except OSError:
        pass
    notify("Contribution mirror sync failed", f"{reason}\n{detail}\nLog: {os.path.join(log_dir, 'mirror.log')}".strip(),
           urgent=True)


def success_message(result):
    """'N new commit(s) mirrored and pushed' plus the sources that got any, or 'Up to date'."""
    if result.created <= 0:
        return "Up to date, no new commits."
    lines = [f"{name}: {n}" for name, n in result.by_source if n]
    return "\n".join([f"{result.created} new commit(s) mirrored and pushed", *lines])


def main(argv):
    p = argparse.ArgumentParser(prog="mirror.py scheduled")
    p.add_argument("--startup-delay", type=int, default=0, help="seconds to wait first (for login triggers)")
    a = p.parse_args(argv)
    if a.startup_delay:
        time.sleep(a.startup_delay)
    try:
        cfg = load_config()
    except EnvFileError as e:
        failure("cannot read config/.env", str(e))
        return 1
    log_dir = cfg.log_dir or LOG_DIR
    hosts = list(dict.fromkeys([real_host(cfg.expected_remote), "github.com"]))
    if not wait_for_network(hosts):
        failure("network not reachable after 5 minutes (will retry at next run)", log_dir=log_dir)
        return 1
    print("scheduled sync starting")
    try:
        result, _ = sync.run_sync(cfg)
    except Exception as e:  # noqa: BLE001 - every failure must be reported, never swallowed
        failure("sync failed (commits may not be pushed)", str(e), log_dir)
        return 1
    notify("Contribution mirror sync OK", success_message(result))
    return 0
