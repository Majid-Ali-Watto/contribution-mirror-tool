import os
import tempfile
import unittest
from collections import Counter
from unittest import mock

import _path
from contribution_mirror import gitutil


class SourceDates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = _path.make_repo(os.path.join(self.tmp.name, "r"), [
            ("me@work.com", "2026-01-01T10:00:00+05:00"),
            ("me@work.com", "2026-01-01T10:00:00+05:00"),      # same second twice: counted twice
            ("ME@Work.com", "2026-01-02T09:00:00+00:00"),      # email case is ignored
            ("other@work.com", "2026-01-03T09:00:00+00:00"),   # someone else: not counted
        ], merge=("me@work.com", "2026-03-01T12:00:00+00:00"))
        self.addCleanup(self.tmp.cleanup)

    def test_counts_only_my_commits_including_merges_and_branches(self):
        c = gitutil.source_dates(self.repo, {"me@work.com"}, env=_path.GIT_ENV)
        self.assertEqual(c, Counter({"2026-01-01T10:00:00+05:00": 2, "2026-01-02T09:00:00Z": 1,
                                     "2026-03-01T12:00:00Z": 1}))

    def test_unknown_email_gives_nothing(self):
        self.assertEqual(gitutil.source_dates(self.repo, {"nobody@x"}, env=_path.GIT_ENV), Counter())

    def test_git_failure_is_an_error_not_zero_commits(self):
        with self.assertRaises(RuntimeError) as cm:
            gitutil.source_dates(os.path.join(self.tmp.name, "missing"), {"me@work.com"})
        self.assertIn("git log failed", str(cm.exception))


class Environment(unittest.TestCase):
    def test_work_env_disables_prompts_and_keeps_user_setup(self):
        with mock.patch.dict(os.environ, {"GIT_SSH_COMMAND": "ssh -i mine"}):
            env = gitutil.work_env()
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_SSH_COMMAND"], "ssh -i mine")   # never overridden

    def test_token_env_adds_header_without_touching_os_environ(self):
        before = dict(os.environ)
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "a.b", "GIT_CONFIG_VALUE_0": "c"}):
            env = gitutil.token_env("Basic abc123", "github.com")
        self.assertEqual(env["GIT_CONFIG_COUNT"], "2")                      # appended after the existing entry
        self.assertEqual(env["GIT_CONFIG_KEY_1"], "http.https://github.com/.extraheader")
        self.assertEqual(env["GIT_CONFIG_VALUE_1"], "Authorization: Basic abc123")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], "c")                    # the existing entry is untouched
        self.assertEqual(dict(os.environ), before)

    def test_run_git_reports_success_and_failure(self):
        self.assertIsNone(gitutil.run_git(["--version"], _path.GIT_ENV, 30))
        err = gitutil.run_git(["-C", os.devnull, "log"], _path.GIT_ENV, 30)
        self.assertTrue(err and "fatal" in err.lower())


if __name__ == "__main__":
    unittest.main()
