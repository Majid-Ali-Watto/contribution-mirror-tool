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


import unittest.mock  # noqa: E402  (used above through unittest.mock.patch)

if __name__ == "__main__":
    unittest.main()
