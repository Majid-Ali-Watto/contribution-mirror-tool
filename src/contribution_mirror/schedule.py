"""`schedule install|uninstall|status`: run the sync automatically at login and/or daily, with whatever scheduler the
operating system has: systemd user timer (Linux), launchd agents (macOS), Task Scheduler (Windows).

The scheduler itself is the only record of what is set up: `current()` reads it back (is the login run on, is the
daily run on, at what time), and `apply()` makes it match what you ask for, touching only what changed. So the command
line and the setup page always agree, turning a run off really removes it, and changing the time never fires an
unexpected run. Scheduled runs read the settings (.env) and the project list (repos.json) when they start, so
settings saved later (by hand or on the setup page) are used without reinstalling."""
import os
import plistlib
import posixpath
import re
import subprocess
import sys

from .settings import LOG_DIR, command_name, self_command

NAME = "contribution-mirror"
LABEL = "dev.contribution-mirror"
DEFAULT_TIME = "18:00"
TASKS = (f"{NAME}-login", f"{NAME}-daily")


class Cmd(list):
    """A command to run. `may_fail`: an error is expected and harmless (e.g. removing something that is not there)."""
    def __init__(self, args, may_fail=False):
        super().__init__(args)
        self.may_fail = may_fail


class ScheduleError(RuntimeError):
    pass


def platform_name(platform=None):
    p = platform or sys.platform
    return "linux" if p.startswith("linux") else "macos" if p == "darwin" else "windows" if p in ("win32", "cygwin") else p


def parse_time(text):
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(text).strip())
    if not m or not (0 <= int(m.group(1)) < 24 and 0 <= int(m.group(2)) < 60):
        raise ValueError("time must be HH:MM, 00:00 to 23:59")
    return int(m.group(1)), int(m.group(2))


def _argv(python, startup_delay=0):
    args = self_command(python) + ["scheduled"]
    return args + (["--startup-delay", str(startup_delay)] if startup_delay else [])


def systemd_word(arg):
    """One ExecStart argument: double-quoted, with systemd's specifier character (%) and the quote escapes doubled, so
    any folder name (spaces, %, quotes) reaches the program unchanged."""
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def render_systemd(python, time_of_day=DEFAULT_TIME, timezone="", login_delay_min=5, login=True, daily=True):
    """{filename: text} of the user service and timer (only the triggers that are on)."""
    hh, mm = parse_time(time_of_day)
    exec_start = " ".join(systemd_word(a) for a in _argv(python))
    triggers = (f"OnStartupSec={login_delay_min}min\n" if login else "") + (
        f"OnCalendar=*-*-* {hh:02d}:{mm:02d}:00{' ' + timezone if timezone else ''}\nPersistent=true\n" if daily else "")
    what = " and ".join(w for w, on in (("at login", login), ("daily", daily)) if on)
    return {
        f"{NAME}.service": f"[Unit]\nDescription=Mirror work contribution timestamps to personal repo\n\n"
                           f"[Service]\nType=oneshot\nExecStart={exec_start}\n",
        f"{NAME}.timer": f"[Unit]\nDescription=Run {NAME} {what}\n\n[Timer]\n{triggers}\n"
                         f"[Install]\nWantedBy=timers.target\n",
    }


def render_launchd(python, time_of_day=DEFAULT_TIME, log_dir=LOG_DIR):
    """{filename: plist bytes}: one agent that runs at login, one that runs daily (launchd has no 'delay')."""
    hh, mm = parse_time(time_of_day)
    out_log = os.path.join(log_dir, "launchd.log")
    common = {"StandardOutPath": out_log, "StandardErrorPath": out_log}
    login = {"Label": f"{LABEL}.login", "ProgramArguments": _argv(python, startup_delay=60), "RunAtLoad": True, **common}
    daily = {"Label": f"{LABEL}.daily", "ProgramArguments": _argv(python),
             "StartCalendarInterval": {"Hour": hh, "Minute": mm}, **common}
    return {f"{LABEL}.login.plist": plistlib.dumps(login), f"{LABEL}.daily.plist": plistlib.dumps(daily)}


def render_windows(python, time_of_day=DEFAULT_TIME, login_delay_min=5):
    """schtasks argument lists: one task at logon (delayed), one daily. pythonw avoids a console window."""
    hh, mm = parse_time(time_of_day)
    exe = python[:-len("python.exe")] + "pythonw.exe" if python.lower().endswith("python.exe") else python
    run = " ".join(f'"{a}"' for a in self_command(exe) + ["scheduled"])
    return [["schtasks", "/Create", "/F", "/TN", TASKS[0], "/SC", "ONLOGON", "/DELAY", f"{login_delay_min:04d}:00", "/TR", run],
            ["schtasks", "/Create", "/F", "/TN", TASKS[1], "/SC", "DAILY", "/ST", f"{hh:02d}:{mm:02d}", "/TR", run]]


def _systemd_dir(home):
    return posixpath.join(os.environ.get("XDG_CONFIG_HOME") or posixpath.join(home, ".config"), "systemd", "user")


def _launchd_paths(home):
    d = posixpath.join(home, "Library", "LaunchAgents")
    return {"login": posixpath.join(d, f"{LABEL}.login.plist"), "daily": posixpath.join(d, f"{LABEL}.daily.plist")}


def _as_bytes(content):
    return content if isinstance(content, bytes) else content.encode()


def plan(action, platform=None, python=None, time_of_day=DEFAULT_TIME, timezone="", home=None, login=True, daily=True,
         existing=None):
    """What to do, without doing it: ([(path, content)], [Cmd, ...], [path to delete]). Paths follow the TARGET
    platform's rules, so the plan for any operating system can be produced and tested from any other.
    `existing` = {path: bytes} of the scheduler files already there: unchanged files are not reloaded (so changing the
    daily time never re-fires the login run). Installing with both runs off is the same as uninstalling."""
    plat, python, home = platform_name(platform), python or sys.executable, home or os.path.expanduser("~")
    existing = existing or {}
    if action == "install":
        parse_time(time_of_day)
        if not (login or daily):
            action = "uninstall"
    changed = lambda path, content: existing.get(path) != _as_bytes(content)
    if plat == "linux":
        d = _systemd_dir(home)
        timer = posixpath.join(d, f"{NAME}.timer")
        files = {posixpath.join(d, n): t for n, t in
                 render_systemd(python, time_of_day, timezone, login=login, daily=daily).items()}
        if action == "install":
            cmds = [Cmd(["systemctl", "--user", "daemon-reload"]),
                    Cmd(["systemctl", "--user", "enable", "--now", f"{NAME}.timer"])]
            if timer in existing and changed(timer, files[timer]):
                # a running timer keeps its old schedule until restarted
                cmds.append(Cmd(["systemctl", "--user", "restart", f"{NAME}.timer"]))
            return list(files.items()), cmds, []
        if action == "uninstall":
            return [], [Cmd(["systemctl", "--user", "disable", "--now", f"{NAME}.timer"], may_fail=True),
                        Cmd(["systemctl", "--user", "daemon-reload"], may_fail=True)], list(files)
        return [], [Cmd(["systemctl", "--user", "list-timers", f"{NAME}.timer", "--no-pager"], may_fail=True)], []
    if plat == "macos":
        uid = os.getuid() if hasattr(os, "getuid") else 501
        paths = _launchd_paths(home)
        content = render_launchd(python, time_of_day)
        want = {"login": login and action == "install", "daily": daily and action == "install"}
        if action == "status":
            return [], [Cmd(["launchctl", "print", f"gui/{uid}/{LABEL}.{k}"], may_fail=True) for k in paths], []
        files, cmds, dels = [], [], []
        for kind, path in paths.items():
            label, data = f"{LABEL}.{kind}", content[f"{LABEL}.{kind}.plist"]
            if want[kind]:
                if changed(path, data):
                    files.append((path, data))
                    if path in existing:                     # loaded with the old content: unload it first
                        cmds.append(Cmd(["launchctl", "bootout", f"gui/{uid}/{label}"], may_fail=True))
                    cmds.append(Cmd(["launchctl", "bootstrap", f"gui/{uid}", path]))
                else:                                       # unchanged: make sure it is loaded (an error = it is)
                    cmds.append(Cmd(["launchctl", "bootstrap", f"gui/{uid}", path], may_fail=True))
            elif path in existing or action == "uninstall":
                cmds.append(Cmd(["launchctl", "bootout", f"gui/{uid}/{label}"], may_fail=True))
                dels.append(path)
        return files, cmds, dels
    if plat == "windows":
        create = render_windows(python, time_of_day)
        if action == "install":
            cmds = [Cmd(c) if on else Cmd(["schtasks", "/Delete", "/F", "/TN", name], may_fail=True)
                    for c, on, name in zip(create, (login, daily), TASKS)]
            return [], cmds, []
        if action == "uninstall":
            return [], [Cmd(["schtasks", "/Delete", "/F", "/TN", n], may_fail=True) for n in TASKS], []
        return [], [Cmd(["schtasks", "/Query", "/TN", n], may_fail=True) for n in TASKS], []
    raise SystemExit(f"automatic runs are not supported on '{plat}'; run `{command_name()} sync` from your own "
                     "scheduler")


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def existing_files(platform=None, home=None):
    """{path: bytes} of this tool's scheduler files that exist now (Linux, macOS; Windows keeps tasks, not files)."""
    plat, home = platform_name(platform), home or os.path.expanduser("~")
    if plat == "linux":
        d = _systemd_dir(home)
        paths = [posixpath.join(d, f"{NAME}.service"), posixpath.join(d, f"{NAME}.timer")]
    elif plat == "macos":
        paths = list(_launchd_paths(home).values())
    else:
        return {}
    return {p: data for p in paths for data in [_read(p)] if data is not None}


def _run(cmd, run):
    try:
        r = run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def current(platform=None, home=None, run=subprocess.run):
    """What is set up now, read back from the scheduler: {supported, platform, login, daily, time, timezone}."""
    plat, home = platform_name(platform), home or os.path.expanduser("~")
    out = {"supported": plat in ("linux", "macos", "windows"), "platform": plat, "login": False, "daily": False,
           "time": None, "timezone": ""}
    if plat == "linux":
        text = (_read(posixpath.join(_systemd_dir(home), f"{NAME}.timer")) or b"").decode("utf-8", "replace")
        out["login"] = bool(re.search(r"^OnStartupSec=", text, re.M))
        m = re.search(r"^OnCalendar=\S+ (\d{2}):(\d{2}):\d{2}(?: (\S+))?\s*$", text, re.M)
        if m:
            out.update(daily=True, time=f"{m.group(1)}:{m.group(2)}", timezone=m.group(3) or "")
    elif plat == "macos":
        paths = _launchd_paths(home)
        out["login"] = _read(paths["login"]) is not None
        data = _read(paths["daily"])
        if data is not None:
            try:
                sci = plistlib.loads(data).get("StartCalendarInterval") or {}
                out.update(daily=True, time=f"{int(sci.get('Hour', 0)):02d}:{int(sci.get('Minute', 0)):02d}")
            except (ValueError, TypeError, plistlib.InvalidFileException):
                out["daily"] = True
    elif plat == "windows":
        code, _ = _run(["schtasks", "/Query", "/TN", TASKS[0]], run)
        out["login"] = code == 0
        code, xml = _run(["schtasks", "/Query", "/TN", TASKS[1], "/XML"], run)
        if code == 0:
            m = re.search(r"<StartBoundary>[^<]*T(\d{2}):(\d{2})", xml)
            out.update(daily=True, time=f"{m.group(1)}:{m.group(2)}" if m else None)
    return out


def execute(files, commands, deletes, run=subprocess.run, say=print):
    """Write, run, delete. Raises ScheduleError at the first command that must succeed and fails."""
    for path, content in files:
        say(f"writing {path}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(_as_bytes(content))
    for path in deletes:
        if os.path.exists(path):
            say(f"deleting {path}")
            os.remove(path)
    for cmd in commands:
        say("running: " + " ".join(cmd))
        code, output = _run(list(cmd), run)
        if code != 0 and not getattr(cmd, "may_fail", False):
            raise ScheduleError(f"`{' '.join(cmd[:4])}` failed: {output.strip()[-300:] or f'exit {code}'}")


def apply(login, daily, time_of_day=DEFAULT_TIME, timezone="", platform=None, python=None, home=None,
          run=subprocess.run, say=print):
    """Make the scheduler match: login run on/off, daily run on/off at `time_of_day`. Both off removes everything.
    Returns current() afterwards."""
    existing = existing_files(platform, home)
    action = "install" if (login or daily) else "uninstall"
    files, cmds, dels = plan(action, platform, python, time_of_day, timezone, home, login, daily, existing)
    if action == "uninstall":       # the commands (stop the timer / unload agents) first, then remove the files
        execute([], cmds, [], run, say)
        execute([], [], dels, run, say)
    else:
        execute(files, [], dels, run, say)
        execute([], cmds, [], run, say)
    return current(platform, home, run)


def describe(state):
    if not state["supported"]:
        return f"automatic runs are not supported on '{state['platform']}'"
    parts = (["at login"] if state["login"] else []) + (
        [f"every day at {state['time'] or '?'}{' ' + state['timezone'] if state['timezone'] else ''}"]
        if state["daily"] else [])
    return "automatic runs: " + (" and ".join(parts) if parts else "off")


def main(argv):
    import argparse
    p = argparse.ArgumentParser(prog=f"{command_name()} schedule")
    p.add_argument("action", choices=["install", "uninstall", "status"])
    p.add_argument("--time", default=DEFAULT_TIME, help="daily run time, HH:MM in local time (default 18:00)")
    p.add_argument("--timezone", default="", help="Linux only: timezone name for the daily run, e.g. Europe/London")
    p.add_argument("--no-login", action="store_true", help="do not run at login (only daily)")
    p.add_argument("--no-daily", action="store_true", help="do not run daily (only at login)")
    p.add_argument("--dry-run", action="store_true", help="show what would be written and run, change nothing")
    a = p.parse_args(argv)
    login, daily = not a.no_login, not a.no_daily
    try:
        if a.action == "status":
            print(describe(current()), flush=True)
            _, commands, _ = plan("status")
            for cmd in commands:
                subprocess.run(list(cmd))
            return 0
        if a.dry_run:
            files, commands, deletes = plan(a.action, time_of_day=a.time, timezone=a.timezone, login=login,
                                            daily=daily, existing=existing_files())
            for path, content in files:
                print(f"would write {path}")
                print((content if isinstance(content, str) else content.decode()).rstrip() + "\n")
            for path in deletes:
                print(f"would delete {path}")
            for cmd in commands:
                print("would run: " + " ".join(cmd))
            return 0
        if a.action == "uninstall":
            login = daily = False
        print(describe(apply(login, daily, a.time, a.timezone)))
    except (ValueError, ScheduleError) as e:
        print(e, file=sys.stderr)
        return 1
    return 0
