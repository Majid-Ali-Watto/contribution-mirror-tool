import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
from contribution_mirror import scheduled
from contribution_mirror.report import RunResult


class WaitingForTheNetwork(unittest.TestCase):
    def test_returns_as_soon_as_the_network_is_up(self):
        calls = iter([False, False, True])
        sleeps = []
        self.assertTrue(scheduled.wait_for_network(["h"], tries=5, delay=10, sleep=sleeps.append, check=lambda h: next(calls)))
        self.assertEqual(sleeps, [10, 10])

    def test_gives_up_after_the_last_try_without_a_pointless_final_sleep(self):
        sleeps = []
        self.assertFalse(scheduled.wait_for_network(["h"], tries=3, delay=7, sleep=sleeps.append, check=lambda h: False))
        self.assertEqual(sleeps, [7, 7])

    def test_network_up_tries_every_host_and_port(self):
        tried = []
        def connect(addr, timeout):
            tried.append(addr)
            if addr == ("b.example", 22):
                return mock.Mock()
            raise OSError
        with mock.patch.object(scheduled.socket, "create_connection", connect):
            self.assertTrue(scheduled.network_up(["a.example", "b.example"]))
        self.assertEqual(tried, [("a.example", 443), ("a.example", 22), ("b.example", 443), ("b.example", 22)])
        with mock.patch.object(scheduled.socket, "create_connection", side_effect=OSError):
            self.assertFalse(scheduled.network_up(["a.example"]))


class Messages(unittest.TestCase):
    def test_success_message(self):
        r = RunResult(5, [("app", 3), ("api", 0), ("reviews", 2)], dry_run=False)
        self.assertEqual(scheduled.success_message(r), "5 new commit(s) mirrored and pushed\napp: 3\nreviews: 2")
        self.assertEqual(scheduled.success_message(RunResult(0, [("app", 0)], dry_run=False)), "Up to date, no new commits.")


class TheScheduledRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = os.path.join(self.tmp.name, ".env")
        with open(self.env, "w") as f:
            f.write(f"WORK_EMAIL=a@w.com\nPERSONAL_NAME=Me\nPERSONAL_EMAIL=m@p.com\nEXPECTED_REMOTE=git@github.com:me/m.git\n"
                    f"MIRROR_LOG_DIR={self.tmp.name}\n")
        patches = [mock.patch.dict(os.environ, {"MIRROR_ENV_FILE": self.env}),
                   mock.patch.object(scheduled, "notify"),
                   mock.patch.object(scheduled, "real_host", lambda u: "github.com")]
        self.mocks = [p.start() for p in patches]
        self.notify = self.mocks[1]
        for p in patches:
            self.addCleanup(p.stop)
        for key in ("WORK_EMAIL", "PERSONAL_NAME", "PERSONAL_EMAIL", "EXPECTED_REMOTE", "MIRROR_LOG_DIR", "MIRROR_DIR"):
            os.environ.pop(key, None)       # unset (not empty): a variable that is already set wins over the file

    def log_text(self):
        path = os.path.join(self.tmp.name, "mirror.log")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_success_notifies(self):
        with mock.patch.object(scheduled, "wait_for_network", return_value=True), \
                mock.patch.object(scheduled.sync, "run_sync", return_value=(RunResult(2, [("app", 2)], False), "pushed")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scheduled.main([]), 0)
        title, message = self.notify.call_args[0][:2]
        self.assertEqual(title, "Contribution mirror sync OK")
        self.assertIn("2 new commit(s)", message)

    def test_offline_is_reported_as_a_failure_with_an_error_line_and_urgent_notification(self):
        with mock.patch.object(scheduled, "wait_for_network", return_value=False):
            self.assertEqual(scheduled.main([]), 1)
        self.assertIn("ERROR scheduled sync failed: network not reachable", self.log_text())
        self.assertTrue(self.notify.call_args.kwargs["urgent"])

    def test_a_failing_sync_is_reported_with_the_reason(self):
        with mock.patch.object(scheduled, "wait_for_network", return_value=True), \
                mock.patch.object(scheduled.sync, "run_sync", side_effect=RuntimeError("push failed: denied")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scheduled.main([]), 1)
        self.assertIn("push failed: denied", self.log_text())
        self.assertIn("ERROR", self.log_text())

    def test_a_broken_config_is_reported_not_ignored(self):
        os.environ["MIRROR_ENV_FILE"] = os.path.join(self.tmp.name, "missing.env")
        with mock.patch.object(scheduled, "failure") as failure:
            self.assertEqual(scheduled.main([]), 1)
        self.assertIn("config/.env", failure.call_args[0][0])


class NoTokensInReports(TheScheduledRun):
    def test_a_token_in_a_url_never_reaches_the_log_or_the_notification(self):
        with mock.patch.object(scheduled, "wait_for_network", return_value=True), \
                mock.patch.object(scheduled.sync, "run_sync",
                                  side_effect=RuntimeError("clone of https://me:ghp_SECRET@github.com/o/r failed")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(scheduled.main([]), 1)
        shown = self.log_text() + err.getvalue() + " ".join(map(str, self.notify.call_args[0]))
        self.assertNotIn("ghp_SECRET", shown)
        self.assertIn("https://***@github.com/o/r", shown)

    def test_another_run_in_progress_is_not_a_failure(self):
        from contribution_mirror.runlock import RunLocked
        with mock.patch.object(scheduled, "wait_for_network", return_value=True), \
                mock.patch.object(scheduled.sync, "run_sync", side_effect=RunLocked("another run is in progress")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scheduled.main([]), 0)
        self.notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
