import os
import subprocess
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


class WhatCountsAsACommit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _path.make_repo(os.path.join(self.tmp.name, "r"), [("me@work.com", "2026-01-01T10:00:00+00:00"),
                                                                       ("a.me@work.com", "2026-01-02T10:00:00+00:00")])

    def test_a_lookalike_email_is_not_mine(self):
        self.assertEqual(gitutil.source_dates(self.repo, {"me@work.com"}, env=_path.GIT_ENV),
                         Counter({"2026-01-01T10:00:00Z": 1}))

    def test_a_stash_is_not_a_contribution(self):
        path = os.path.join(self.repo, "f.txt")
        with open(path, "w") as f:
            f.write("x")
        env = {**_path.GIT_ENV, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "me@work.com", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "me@work.com", "GIT_AUTHOR_DATE": "2026-05-05T10:00:00+00:00",
               "GIT_COMMITTER_DATE": "2026-05-05T10:00:00+00:00"}
        run = lambda *a: subprocess.run(["git", "-C", self.repo, *a], check=True, capture_output=True, env=env)
        run("add", "f.txt")
        run("stash")
        self.assertEqual(sum(gitutil.source_dates(self.repo, {"me@work.com"}, env=_path.GIT_ENV).values()), 1)

    def commit(self, author, message, date="2026-03-01T10:00:00+00:00"):
        env = {**_path.GIT_ENV, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": author, "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": author, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        subprocess.run(["git", "-C", self.repo, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty",
                        "-m", message], check=True, capture_output=True, env=env)

    def test_a_commit_that_names_me_as_co_author_counts_once(self):
        """GitHub counts a commit for each co-author (pair programming, a squash merge someone else made)."""
        self.commit("pair@work.com", "pair work\n\nCo-authored-by: Me <ME@work.com>", "2026-03-01T10:00:00+00:00")
        self.commit("me@work.com", "mine\n\nCo-authored-by: Me <me@work.com>", "2026-03-02T10:00:00+00:00")
        self.commit("x@work.com", "thanks to <me@work.com> for the idea", "2026-03-03T10:00:00+00:00")   # not a trailer
        self.commit("x@work.com", "x\n\nCo-authored-by: A <a.me@work.com>", "2026-03-04T10:00:00+00:00")  # lookalike
        dates = gitutil.source_dates(self.repo, {"me@work.com"}, env=_path.GIT_ENV)
        self.assertEqual(dates, Counter({"2026-01-01T10:00:00Z": 1, "2026-03-01T10:00:00Z": 1, "2026-03-02T10:00:00Z": 1}))

    def test_commits_are_returned_by_hash(self):
        commits = gitutil.source_commits(self.repo, {"me@work.com"}, env=_path.GIT_ENV)
        self.assertEqual(list(commits.values()), ["2026-01-01T10:00:00Z"])
        self.assertRegex(next(iter(commits)), r"^[0-9a-f]{40,64}$")

    def test_utc_has_one_spelling_whatever_the_git_version(self):
        # git >= 2.45 prints UTC as Z, older versions as +00:00: both must be the same date
        self.assertEqual(gitutil.canonical_date("2026-01-01T10:00:00+00:00"), "2026-01-01T10:00:00Z")
        self.assertEqual(gitutil.canonical_date("2026-01-01T10:00:00-00:00"), "2026-01-01T10:00:00Z")
        self.assertEqual(gitutil.canonical_date("2026-01-01T10:00:00Z"), "2026-01-01T10:00:00Z")
        self.assertEqual(gitutil.canonical_date("2026-01-01T10:00:00+05:30"), "2026-01-01T10:00:00+05:30")


class ChildEnvironment(unittest.TestCase):
    def test_this_tools_tokens_are_not_passed_to_other_programs(self):
        env = gitutil.child_env({"WORK_TOKEN": "a", "WORK_GITHUB_TOKEN": "b", "WORK_AZURE_DEVOPS_TOKEN": "c",
                                 "GH_TOKEN": "user's own", "PATH": "/bin", "WORK_EMAIL": "me@w"})
        self.assertEqual(env, {"GH_TOKEN": "user's own", "PATH": "/bin", "WORK_EMAIL": "me@w"})

    def test_no_credential_manager_window_and_no_ssh_prompt_in_parallel_fetches(self):
        with mock.patch.dict(os.environ, {**_path.GIT_ENV, "WORK_TOKEN": "s"}):
            os.environ.pop("GIT_SSH_COMMAND", None)
            os.environ.pop("GIT_SSH", None)
            env = gitutil.work_env(batch_ssh=True)
            plain = gitutil.work_env()
        self.assertEqual((env["GCM_INTERACTIVE"], env["GIT_SSH_COMMAND"]), ("never", "ssh -o BatchMode=yes"))
        self.assertNotIn("WORK_TOKEN", env)
        self.assertNotIn("GIT_SSH_COMMAND", plain)

    def test_a_users_core_ssh_command_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _path.make_repo(os.path.join(d, "r"), [])
            subprocess.run(["git", "-C", repo, "config", "core.sshCommand", "ssh -i ~/.ssh/work"], check=True,
                           env=_path.GIT_ENV)
            with mock.patch.dict(os.environ, _path.GIT_ENV):
                os.environ.pop("GIT_SSH_COMMAND", None)
                self.assertNotIn("GIT_SSH_COMMAND", gitutil.work_env(repo, batch_ssh=True))


if __name__ == "__main__":
    unittest.main()
