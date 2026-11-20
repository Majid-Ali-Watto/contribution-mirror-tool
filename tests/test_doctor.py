"""`mirror.py doctor`: every check finds the problem it is for, says how to fix it, and changes nothing."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import _path
from _server import FakeHost
from contribution_mirror import doctor, fsutil
from contribution_mirror.config import Config
from contribution_mirror.providers import PROVIDERS, Token

ME = "me@work.com"


class Setup(unittest.TestCase):
    """A complete, local setup: one work clone, a bare repo as the personal mirror, PRs/reviews off."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        env = mock.patch.dict(os.environ, _path.GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        for k in ("WORK_EMAIL", "PERSONAL_NAME", "PERSONAL_EMAIL", "EXPECTED_REMOTE", "MIRROR_DIR", "MIRROR_LOG_DIR",
                  "MIRROR_PR_REVIEWS", "MIRROR_CLONE_CACHE", "WORK_GH_USER"):
            os.environ.pop(k, None)
        self.work = _path.make_repo(os.path.join(t, "work"), [(ME, "2026-01-01T10:00:00Z"),
                                                              ("x@o.com", "2026-01-02T10:00:00Z")])
        self.remote = os.path.join(t, "personal.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.remote], check=True)
        self.mirror, self.logs = os.path.join(t, "mirror"), os.path.join(t, "logs")
        self.env_file, self.repos_file = os.path.join(t, "cfg", ".env"), os.path.join(t, "cfg", "repos.json")
        os.environ["MIRROR_ENV_FILE"], os.environ["MIRROR_REPOS_FILE"] = self.env_file, self.repos_file
        self.write_settings()
        self.write_repos([self.work, os.path.join(t, "gone")])

    def write_settings(self, **over):
        values = {"WORK_EMAIL": ME, "PERSONAL_NAME": "Me", "PERSONAL_EMAIL": "me@personal.com",
                  "EXPECTED_REMOTE": self.remote, "MIRROR_DIR": self.mirror, "MIRROR_LOG_DIR": self.logs,
                  "MIRROR_PR_REVIEWS": "off", **over}
        fsutil.write_private(self.env_file, "".join(f"{k}={v}\n" for k, v in values.items()))

    def write_repos(self, entries):
        fsutil.write_private(self.repos_file, json.dumps({"repos": entries}))

    def run_checks(self):
        # a run loads the settings into the environment (as its own process would): start fresh every time
        with mock.patch.dict(os.environ), contextlib.redirect_stderr(io.StringIO()):
            return doctor.run_checks(include_schedule=False)

    def main(self, argv):
        out = io.StringIO()
        with mock.patch.dict(os.environ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            return doctor.main(argv), out.getvalue()

    def by_name(self, checks):
        out = {}
        for c in checks:
            out.setdefault(c.name, []).append(c)
        return out


class WholeSetup(Setup):
    def test_a_working_setup_with_one_bad_project(self):
        checks = self.by_name(self.run_checks())
        status = lambda name: [c.status for c in checks[name]]
        self.assertEqual(status("Settings file"), ["ok"])
        self.assertEqual(status("git"), ["ok"])
        self.assertEqual(status("Project list"), ["ok"])
        self.assertEqual(status("Project"), ["warn"])                        # the missing folder, with a fix
        self.assertIn("folder not found", checks["Project"][0].detail)
        self.assertIn("1 commit(s)", checks["Work email"][0].detail)
        self.assertEqual(status("Mirror repo"), ["ok"])
        self.assertIn("created by the first sync", checks["Mirror folder"][0].detail)
        self.assertEqual(status("Personal account"), ["skip"])               # a local folder, not on a host
        self.assertEqual(status("Logins"), ["skip"])                         # PRs/reviews are off
        self.assertFalse(os.path.exists(self.mirror))                         # nothing was created
        self.assertFalse(os.path.exists(self.logs))

    def test_missing_settings_stop_early_with_the_way_to_fix_them(self):
        os.remove(self.env_file)
        checks = self.run_checks()
        self.assertEqual([(c.name, c.status) for c in checks], [("Settings file", "fail"), ("git", "ok")])
        self.assertIn("mirror.py setup", checks[0].fix)

    def test_a_typo_in_the_work_email_is_a_failure(self):
        self.write_settings(WORK_EMAIL="me@wrok.com")
        email = [c for c in self.run_checks() if c.name == "Work email"]
        self.assertEqual([c.status for c in email], ["fail", "warn"])
        self.assertIn("git -C", email[1].fix)                                # how to see the emails really used

    def test_an_empty_project_list_is_a_failure(self):
        self.write_repos([])
        self.assertEqual([c.status for c in self.run_checks() if c.name == "Project list"], ["fail"])

    def test_a_mirror_folder_with_real_work_or_another_origin_is_a_failure(self):
        _path.make_repo(self.mirror, [(ME, "2026-01-01T10:00:00Z")])
        folder = [c for c in self.run_checks() if c.name == "Mirror folder"][0]
        self.assertEqual(folder.status, "fail")
        self.assertIn("contribution-mirror.mirror", folder.fix)
        subprocess.run(["git", "-C", self.mirror, "remote", "add", "origin", "git@github.com:someone/else.git"],
                       check=True)
        folder = [c for c in self.run_checks() if c.name == "Mirror folder"][0]
        self.assertEqual(folder.status, "fail")
        self.assertIn("refuses to push", folder.detail)

    def test_an_unreachable_mirror_is_a_warning(self):
        self.write_settings(EXPECTED_REMOTE=self.remote + "-missing")
        self.assertEqual([c.status for c in self.run_checks() if c.name == "Mirror repo"], ["fail"])  # not a URL either
        self.write_settings(EXPECTED_REMOTE="file://" + self.remote.replace(os.sep, "/") + "-missing")
        self.assertEqual([c.status for c in self.run_checks() if c.name == "Mirror repo"], ["warn"])

    @unittest.skipIf(os.name == "nt", "file modes are a Linux/macOS matter")
    def test_settings_readable_by_others_are_a_warning(self):
        os.chmod(self.env_file, 0o644)
        first = self.run_checks()[0]
        self.assertEqual(first.status, "warn")
        self.assertIn("chmod 600", first.fix)


class CommandLine(Setup):
    def test_text_output_and_exit_code(self):
        code, text = self.main(["--skip-schedule"])
        self.assertEqual(code, 0)                                            # warnings are not failures
        self.assertIn("[warn] Project", text)
        self.assertIn("fix: remove it from the list", text)
        self.assertIn("0 problem(s) to fix, 1 warning(s).", text)
        self.write_settings(WORK_EMAIL="nobody@x.com")
        self.assertEqual(self.main(["--skip-schedule"])[0], 1)

    def test_json_output(self):
        checks = json.loads(self.main(["--json", "--skip-schedule"])[1])["checks"]
        self.assertEqual(set(checks[0]), {"status", "name", "detail", "fix"})


class GitHubAccount(unittest.TestCase):
    """The email and repo checks, against a fake GitHub API."""
    CFG = Config([ME], "Me", "me@personal.com", "git@github.com:me/mirror.git", "/m")

    def run_with(self, emails=(200, [{"email": "me@personal.com", "verified": True}]),
                 repo=(200, {"private": True, "default_branch": "main"}), personal_email=None):
        cfg = self.CFG if personal_email is None else Config([ME], "Me", personal_email, self.CFG.expected_remote, "/m")
        found = lambda cfg: (PROVIDERS["github"], "github.com", "me", Token("gh:me", "t"))
        with FakeHost() as h, mock.patch.dict(os.environ, {"WORK_GITHUB_API_URL": h.url}):
            h.routes["/user/emails"], h.routes["/repos/me/mirror"] = emails, repo
            checks = doctor.check_github(cfg, find_token=found)
            self.assertTrue(all(r["auth"] == "Bearer t" for r in h.requests))
        return {c.name: c for c in checks}

    def test_verified_email_and_a_good_repo(self):
        c = self.run_with()
        self.assertEqual((c["Personal email"].status, c["Mirror repo settings"].status), ("ok", "ok"))
        self.assertIn("Include private contributions", c["Mirror repo settings"].detail)

    def test_unverified_or_unknown_email_is_a_failure(self):
        c = self.run_with(emails=(200, [{"email": "me@personal.com", "verified": False}]))
        self.assertEqual(c["Personal email"].status, "fail")
        self.assertIn("NOT verified", c["Personal email"].detail)
        c = self.run_with(emails=(200, [{"email": "other@personal.com", "verified": True}]))
        self.assertEqual(c["Personal email"].status, "fail")

    def test_githubs_private_address_counts(self):
        c = self.run_with(emails=(200, []), personal_email="12345+me@users.noreply.github.com")
        self.assertEqual(c["Personal email"].status, "ok")

    def test_a_login_that_cannot_read_emails_is_not_a_failure(self):
        c = self.run_with(emails=(403, {"message": "Resource not accessible"}))
        self.assertEqual(c["Personal email"].status, "skip")
        self.assertIn("user:email", c["Personal email"].fix)

    def test_a_default_branch_other_than_main_is_a_failure(self):
        c = self.run_with(repo=(200, {"private": False, "default_branch": "master"}))
        self.assertEqual(c["Mirror repo settings"].status, "fail")
        c = self.run_with(repo=(404, {"message": "Not Found"}))
        self.assertEqual(c["Mirror repo settings"].status, "fail")

    def test_without_a_personal_login_it_says_so(self):
        c = doctor.check_github(self.CFG, find_token=lambda cfg: None)
        self.assertEqual(c[0].status, "skip")
        self.assertIn("gh auth login", c[0].fix)


class AutomaticRuns(unittest.TestCase):
    NOW = datetime(2026, 11, 20, 12, 0, 0)
    ON = {"supported": True, "platform": "linux", "login": True, "daily": True, "time": "18:00"}

    def check(self, state, runs=(), entries=()):
        return doctor.check_schedule(state, {"runs": list(runs), "entries": list(entries)}, now=self.NOW)

    def test_off_or_unsupported(self):
        self.assertEqual(self.check({**self.ON, "login": False, "daily": False}).status, "ok")
        self.assertEqual(self.check({"supported": False, "platform": "plan9"}).status, "skip")

    def test_working(self):
        c = self.check(self.ON, runs=[{"time": "2026-11-19 18:00:00", "mode": "apply"}])
        self.assertEqual((c.status, c.detail), ("ok", "last sync 2026-11-19 18:00:00"))

    def test_a_failure_after_the_last_success(self):
        failed = {"time": "2026-11-20 09:00:00", "level": "ERROR", "message": "scheduled sync failed: push failed"}
        c = self.check(self.ON, runs=[{"time": "2026-11-19 18:00:00", "mode": "apply"}], entries=[failed])
        self.assertEqual(c.status, "fail")
        self.assertIn("push failed", c.detail)
        c = self.check(self.ON, runs=[{"time": "2026-11-20 10:00:00", "mode": "apply"}], entries=[failed])
        self.assertEqual(c.status, "ok")                                     # fixed since then

    def test_never_ran_or_stopped_running(self):
        self.assertEqual(self.check(self.ON, runs=[{"time": "2026-11-19 18:00:00", "mode": "dry-run"}]).status, "warn")
        c = self.check(self.ON, runs=[{"time": "2026-11-01 18:00:00", "mode": "apply"}])
        self.assertEqual(c.status, "warn")
        self.assertIn("18:00", c.fix)


if __name__ == "__main__":
    unittest.main()
