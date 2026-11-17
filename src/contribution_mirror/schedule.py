"""`schedule install|uninstall|status`: run the sync automatically at login and daily, with whatever scheduler the
operating system has: systemd user timer (Linux), launchd agents (macOS), Task Scheduler (Windows)."""
import os
import plistlib
import posixpath
import subprocess
import sys

from .settings import LOG_DIR, PROJECT_ROOT

NAME = "contribution-mirror"
LABEL = "dev.contribution-mirror"
ENTRY = os.path.join(PROJECT_ROOT, "src", "mirror.py")


def platform_name(platform=None):
    p = platform or sys.platform
    return "linux" if p.startswith("linux") else "macos" if p == "darwin" else "windows" if p in ("win32", "cygwin") else p


def parse_time(text):
    hh, mm = text.split(":")
    if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
        raise ValueError("time must be HH:MM, 00:00 to 23:59")
    return int(hh), int(mm)


def _argv(python, startup_delay=0):
    args = [python, "-I", ENTRY, "scheduled"]
    return args + (["--startup-delay", str(startup_delay)] if startup_delay else [])


def render_systemd(python, time_of_day="18:00", timezone="", login_delay_min=5):
    """{filename: text} of the user service and timer."""
    hh, mm = parse_time(time_of_day)
    exec_start = " ".join(f'"{a}"' for a in _argv(python))
    return {
        f"{NAME}.service": f"[Unit]\nDescription=Mirror work contribution timestamps to personal repo\n\n"
                           f"[Service]\nType=oneshot\nExecStart={exec_start}\n",
        f"{NAME}.timer": f"[Unit]\nDescription=Run {NAME} at login and daily\n\n[Timer]\n"
                         f"OnStartupSec={login_delay_min}min\n"
                         f"OnCalendar=*-*-* {hh:02d}:{mm:02d}:00{' ' + timezone if timezone else ''}\n"
                         f"Persistent=true\n\n[Install]\nWantedBy=timers.target\n",
    }


def render_launchd(python, time_of_day="18:00", log_dir=LOG_DIR):
    """{filename: plist bytes}: one agent that runs at login, one that runs daily (launchd has no 'delay')."""
    hh, mm = parse_time(time_of_day)
    out_log = os.path.join(log_dir, "launchd.log")
    common = {"StandardOutPath": out_log, "StandardErrorPath": out_log}
    login = {"Label": f"{LABEL}.login", "ProgramArguments": _argv(python, startup_delay=60), "RunAtLoad": True, **common}
    daily = {"Label": f"{LABEL}.daily", "ProgramArguments": _argv(python),
             "StartCalendarInterval": {"Hour": hh, "Minute": mm}, **common}
    return {f"{LABEL}.login.plist": plistlib.dumps(login), f"{LABEL}.daily.plist": plistlib.dumps(daily)}


def render_windows(python, time_of_day="18:00", login_delay_min=5):
    """schtasks argument lists: one task at logon (delayed), one daily. pythonw avoids a console window."""
    parse_time(time_of_day)
    exe = python[:-len("python.exe")] + "pythonw.exe" if python.lower().endswith("python.exe") else python
    run = " ".join(f'"{a}"' for a in [exe, "-I", ENTRY, "scheduled"])
    return [["schtasks", "/Create", "/F", "/TN", f"{NAME}-login", "/SC", "ONLOGON", "/DELAY", f"{login_delay_min:04d}:00", "/TR", run],
            ["schtasks", "/Create", "/F", "/TN", f"{NAME}-daily", "/SC", "DAILY", "/ST", time_of_day, "/TR", run]]


def plan(action, platform=None, python=None, time_of_day="18:00", timezone="", home=None):
    """What to do, without doing it: ([(path, content)], [command, ...], [path to delete]). Paths follow the TARGET
    platform's rules, so the plan for any operating system can be produced and tested from any other."""
    plat, python, home = platform_name(platform), python or sys.executable, home or os.path.expanduser("~")
    if plat == "linux":
        d = posixpath.join(os.environ.get("XDG_CONFIG_HOME") or posixpath.join(home, ".config"), "systemd", "user")
        files = {posixpath.join(d, n): t for n, t in render_systemd(python, time_of_day, timezone).items()}
        if action == "install":
            return list(files.items()), [["systemctl", "--user", "daemon-reload"],
                                         ["systemctl", "--user", "enable", "--now", f"{NAME}.timer"]], []
        if action == "uninstall":
            return [], [["systemctl", "--user", "disable", "--now", f"{NAME}.timer"]], list(files)
        return [], [["systemctl", "--user", "list-timers", f"{NAME}.timer", "--no-pager"]], []
    if plat == "macos":
        d = posixpath.join(home, "Library", "LaunchAgents")
        files = {posixpath.join(d, n): t for n, t in render_launchd(python, time_of_day).items()}
        uid = os.getuid() if hasattr(os, "getuid") else 501
        labels = [f"{LABEL}.login", f"{LABEL}.daily"]
        if action == "install":
            return list(files.items()), [["launchctl", "bootstrap", f"gui/{uid}", p] for p in files], []
        if action == "uninstall":
            return [], [["launchctl", "bootout", f"gui/{uid}/{l}"] for l in labels], list(files)
        return [], [["launchctl", "print", f"gui/{uid}/{l}"] for l in labels], []
    if plat == "windows":
        if action == "install":
            return [], render_windows(python, time_of_day), []
        names = (f"{NAME}-login", f"{NAME}-daily")
        if action == "uninstall":
            return [], [["schtasks", "/Delete", "/F", "/TN", n] for n in names], []
        return [], [["schtasks", "/Query", "/TN", n] for n in names], []
    raise SystemExit(f"automatic runs are not supported on '{plat}'; run `mirror.py sync` from your own scheduler")


def main(argv):
    import argparse
    p = argparse.ArgumentParser(prog="mirror.py schedule")
    p.add_argument("action", choices=["install", "uninstall", "status"])
    p.add_argument("--time", default="18:00", help="daily run time, HH:MM in local time (default 18:00)")
    p.add_argument("--timezone", default="", help="Linux only: timezone name for the daily run, e.g. Europe/London")
    p.add_argument("--dry-run", action="store_true", help="show what would be written and run, change nothing")
    a = p.parse_args(argv)
    files, commands, deletes = plan(a.action, time_of_day=a.time, timezone=a.timezone)
    for path, content in files:
        print(f"{'would write' if a.dry_run else 'writing'} {path}")
        if a.dry_run:
            print((content if isinstance(content, str) else content.decode()).rstrip() + "\n")
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(content if isinstance(content, bytes) else content.encode())
    for cmd in commands:
        print(("would run: " if a.dry_run else "running: ") + " ".join(cmd))
        if not a.dry_run:
            r = subprocess.run(cmd)
            if r.returncode != 0 and a.action == "install":
                return r.returncode
    for path in deletes:
        print(f"{'would delete' if a.dry_run else 'deleting'} {path}")
        if not a.dry_run and os.path.exists(path):
            os.remove(path)
    return 0
