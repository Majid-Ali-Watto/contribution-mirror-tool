"""Notifications and automatic-run installers for Linux, macOS and Windows. The commands and files are generated
for any platform from any platform, so this verifies all three everywhere (running them needs the real OS)."""
import os
import plistlib
import unittest

import _path  # noqa: F401
from contribution_mirror import notify, schedule

PY = {"linux": "/usr/bin/python3", "macos": "/usr/local/bin/python3", "windows": r"C:\Python312\python.exe"}
HOME = {"linux": "/home/u", "macos": "/Users/u", "windows": r"C:\Users\u"}


class Notifications(unittest.TestCase):
    def test_linux(self):
        cmd = notify.command("T", "M", urgent=True, platform="linux")
        self.assertEqual(cmd[:4], ["notify-send", "-u", "critical", "-a"])
        self.assertEqual(notify.command("T", "M", platform="linux")[2], "normal")

    def test_macos_escapes_quotes(self):
        cmd = notify.command('He said "hi"', 'a\\b "c"', platform="darwin")
        self.assertEqual(cmd[0], "osascript")
        self.assertIn('with title "He said \\"hi\\""', cmd[2])
        self.assertIn('display notification "a\\\\b \\"c\\""', cmd[2])

    def test_windows_escapes_single_quotes_and_hides_the_window(self):
        cmd = notify.command("It's", "don't", platform="win32")
        self.assertEqual(cmd[:4], ["powershell", "-NoProfile", "-WindowStyle", "Hidden"])
        self.assertIn("'It''s'", cmd[-1])
        self.assertIn("'don''t'", cmd[-1])

    def test_unknown_platform_means_no_notification_not_an_error(self):
        self.assertIsNone(notify.command("T", "M", platform="plan9"))

    def test_a_missing_tool_never_raises(self):
        with unittest.mock.patch.object(notify, "command", lambda *a, **k: ["definitely-not-installed-xyz"]):
            notify.notify("T", "M")


class Systemd(unittest.TestCase):
    def test_service_and_timer(self):
        files = schedule.render_systemd("/usr/bin/python3", "07:30", "Europe/London")
        service, timer = files["contribution-mirror.service"], files["contribution-mirror.timer"]
        self.assertIn('ExecStart="/usr/bin/python3" "-I"', service)
        self.assertIn('"scheduled"', service)
        self.assertIn("OnStartupSec=5min", timer)
        self.assertIn("OnCalendar=*-*-* 07:30:00 Europe/London", timer)
        self.assertIn("Persistent=true", timer)

    def test_no_timezone_means_local_time(self):
        self.assertIn("OnCalendar=*-*-* 18:00:00\n", schedule.render_systemd("/p")["contribution-mirror.timer"])

    def test_install_plan_for_linux(self):
        files, cmds, dels = schedule.plan("install", "linux", PY["linux"], home=HOME["linux"])
        self.assertEqual(sorted(os.path.basename(p) for p, _ in files), ["contribution-mirror.service", "contribution-mirror.timer"])
        self.assertTrue(all("/.config/systemd/user/" in p for p, _ in files))
        self.assertEqual(cmds[-1], ["systemctl", "--user", "enable", "--now", "contribution-mirror.timer"])
        self.assertEqual(dels, [])


class Launchd(unittest.TestCase):
    def test_two_agents_login_and_daily(self):
        files = schedule.render_launchd("/usr/local/bin/python3", "09:05", "/tmp/logs")
        login = plistlib.loads(files["dev.contribution-mirror.login.plist"])
        daily = plistlib.loads(files["dev.contribution-mirror.daily.plist"])
        self.assertTrue(login["RunAtLoad"])
        self.assertIn("--startup-delay", login["ProgramArguments"])
        self.assertEqual(daily["StartCalendarInterval"], {"Hour": 9, "Minute": 5})
        self.assertNotIn("RunAtLoad", daily)
        self.assertEqual(daily["ProgramArguments"][:2], ["/usr/local/bin/python3", "-I"])
        self.assertEqual(daily["ProgramArguments"][-1], "scheduled")

    def test_install_and_uninstall_plan_for_macos(self):
        files, cmds, _ = schedule.plan("install", "macos", PY["macos"], home=HOME["macos"])
        self.assertTrue(all(p.startswith("/Users/u/Library/LaunchAgents/") for p, _ in files))
        self.assertTrue(all(c[:2] == ["launchctl", "bootstrap"] and c[2].startswith("gui/") for c in cmds))
        _, cmds, dels = schedule.plan("uninstall", "macos", PY["macos"], home=HOME["macos"])
        self.assertTrue(all(c[:2] == ["launchctl", "bootout"] for c in cmds))
        self.assertEqual(len(dels), 2)


class WindowsTaskScheduler(unittest.TestCase):
    def test_two_tasks_logon_delayed_and_daily(self):
        login, daily = schedule.render_windows(PY["windows"], "18:00")
        self.assertEqual(login[:4], ["schtasks", "/Create", "/F", "/TN"])
        self.assertIn("ONLOGON", login)
        self.assertEqual(login[login.index("/DELAY") + 1], "0005:00")
        self.assertEqual(daily[daily.index("/SC") + 1], "DAILY")
        self.assertEqual(daily[daily.index("/ST") + 1], "18:00")

    def test_runs_pythonw_without_a_console_and_quotes_paths_with_spaces(self):
        run = schedule.render_windows(r"C:\Program Files\Python312\python.exe")[0][-1]
        self.assertTrue(run.startswith('"C:\\Program Files\\Python312\\pythonw.exe" "-I" '))
        self.assertTrue(run.rstrip().endswith('"scheduled"'))

    def test_uninstall_and_status_commands(self):
        _, cmds, _ = schedule.plan("uninstall", "windows", PY["windows"])
        self.assertEqual(cmds[0][:3], ["schtasks", "/Delete", "/F"])
        _, cmds, _ = schedule.plan("status", "windows", PY["windows"])
        self.assertEqual(cmds[0][:2], ["schtasks", "/Query"])


class Common(unittest.TestCase):
    def test_time_validation(self):
        self.assertEqual(schedule.parse_time("07:05"), (7, 5))
        for bad in ("25:00", "12:60", "noon", "7"):
            with self.assertRaises(ValueError):
                schedule.parse_time(bad)

    def test_unsupported_platform_says_so(self):
        with self.assertRaises(SystemExit):
            schedule.plan("install", "plan9")

    def test_platform_names(self):
        for raw, name in {"linux": "linux", "linux2": "linux", "darwin": "macos", "win32": "windows", "cygwin": "windows"}.items():
            self.assertEqual(schedule.platform_name(raw), name)

    def test_dry_run_changes_nothing(self):
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = schedule.main(["install", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("would", out.getvalue())


class Recorder:
    """Stands in for subprocess.run: records commands, answers like the scheduler would."""
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        for prefix, (code, out) in self.answers.items():
            if tuple(cmd[:len(prefix)]) == prefix:
                return unittest.mock.Mock(returncode=code, stdout=out, stderr="")
        return unittest.mock.Mock(returncode=0, stdout="", stderr="")

    def ran(self, *words):
        return [c for c in self.calls if all(w in c for w in words)]


class TurningRunsOnAndOff(unittest.TestCase):
    """What the setup page and `schedule install/uninstall` do: the scheduler always ends up exactly as asked."""
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        env = unittest.mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(self.home, ".config")})
        env.start()                                          # never the real ~/.config, whatever the machine
        self.addCleanup(env.stop)

    def apply(self, platform, login, daily, time="18:00", tz="", run=None):
        run = run or Recorder()
        state = schedule.apply(login, daily, time, tz, platform=platform, python="/usr/bin/python3", home=self.home,
                               run=run, say=lambda *_: None)
        return state, run

    def test_linux_on_change_time_login_only_off(self):
        state, run = self.apply("linux", True, True)
        self.assertEqual((state["login"], state["daily"], state["time"]), (True, True, "18:00"))
        self.assertTrue(run.ran("enable", "--now"))
        self.assertFalse(run.ran("restart"))                  # a fresh install has nothing to restart

        state, run = self.apply("linux", True, True, "14:00", "Asia/Karachi")
        self.assertEqual((state["time"], state["timezone"]), ("14:00", "Asia/Karachi"))
        self.assertTrue(run.ran("restart"))                   # a running timer keeps its old time until restarted

        state, run = self.apply("linux", True, True, "14:00", "Asia/Karachi")
        self.assertFalse(run.ran("restart"))                  # nothing changed: nothing restarted

        state, _ = self.apply("linux", True, False)
        self.assertEqual((state["login"], state["daily"]), (True, False))
        state, _ = self.apply("linux", False, True, "09:30")
        self.assertEqual((state["login"], state["daily"], state["time"]), (False, True, "09:30"))

        state, run = self.apply("linux", False, False)
        self.assertEqual((state["login"], state["daily"]), (False, False))
        self.assertTrue(run.ran("disable", "--now"))
        self.assertEqual(schedule.existing_files("linux", self.home), {})   # the unit files are gone

    def test_linux_reads_a_hand_written_timer(self):
        d = os.path.join(self.home, ".config", "systemd", "user")
        os.makedirs(d)
        with open(os.path.join(d, "contribution-mirror.timer"), "w") as f:
            f.write("[Timer]\n# at login\nOnStartupSec=5min\n# daily\nOnCalendar=*-*-* 18:00:00 Asia/Karachi\n"
                    "Persistent=true\n")
        self.assertEqual(schedule.describe(schedule.current("linux", self.home)),
                         "automatic runs: at login and every day at 18:00 Asia/Karachi")

    def test_macos_changing_the_time_never_reloads_the_login_agent(self):
        state, run = self.apply("macos", True, True)
        self.assertEqual(len(run.ran("bootstrap")), 2)
        state, run = self.apply("macos", True, True, "14:00")
        self.assertEqual(state["time"], "14:00")
        # only the daily agent is reloaded: reloading the login agent would run a sync right now (RunAtLoad)
        self.assertTrue(run.ran("bootout") and all(c[-1].endswith(".daily") for c in run.ran("bootout")))
        state, run = self.apply("macos", False, True, "14:00")
        self.assertEqual((state["login"], state["daily"]), (False, True))
        self.assertTrue(any(c[-1].endswith(".login") for c in run.ran("bootout")))
        state, _ = self.apply("macos", False, False)
        self.assertEqual(schedule.existing_files("macos", self.home), {})

    def test_macos_a_failing_load_is_an_error(self):
        with self.assertRaises(schedule.ScheduleError):
            self.apply("macos", True, True, run=Recorder({("launchctl", "bootstrap"): (5, "Bootstrap failed: 5")}))

    def test_windows_tasks_follow_the_choice(self):
        _, run = self.apply("windows", False, True, "14:00")
        self.assertTrue(run.ran("/Create", "contribution-mirror-daily", "14:00"))
        self.assertTrue(run.ran("/Delete", "contribution-mirror-login"))
        self.assertFalse(run.ran("/Create", "contribution-mirror-login"))
        xml = "<Task><Triggers><CalendarTrigger><StartBoundary>2026-01-01T14:00:00</StartBoundary></CalendarTrigger></Triggers></Task>"
        answers = Recorder({("schtasks", "/Query", "/TN", "contribution-mirror-login"): (1, "not found"),
                            ("schtasks", "/Query", "/TN", "contribution-mirror-daily"): (0, xml)})
        self.assertEqual(schedule.describe(schedule.current("windows", run=answers)), "automatic runs: every day at 14:00")
        _, run = self.apply("windows", False, False)
        self.assertEqual(len(run.ran("/Delete")), 2)

    def test_command_line_options(self):
        import posixpath
        timer_path = posixpath.join(schedule._systemd_dir(self.home), "contribution-mirror.timer")
        files, _, _ = schedule.plan("install", "linux", "/p", home=self.home, login=False)
        timer = dict(files)[timer_path]
        self.assertNotIn("OnStartupSec", timer)
        self.assertIn("OnCalendar", timer)
        files, _, _ = schedule.plan("install", "linux", "/p", home=self.home, daily=False)
        timer = dict(files)[timer_path]
        self.assertNotIn("OnCalendar", timer)
        _, cmds, dels = schedule.plan("install", "linux", "/p", home=self.home, login=False, daily=False)
        self.assertTrue(dels and any("disable" in c for c in cmds))   # both off = uninstall


import unittest.mock  # noqa: E402  (used above through unittest.mock.patch)

if __name__ == "__main__":
    unittest.main()
