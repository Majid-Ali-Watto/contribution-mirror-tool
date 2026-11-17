"""Reading commits from many repos: each commit counted once, and the opt-in clone cache."""
import os
import subprocess
import tempfile
import unittest
from collections import Counter
from unittest import mock

import _path
from contribution_mirror import scan

NO_LOGIN = mock.Mock(pick=lambda scope: None)


def commit(repo, email, date):
    env = {**_path.GIT_ENV, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date,
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", "-C", repo, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "n"],
                   check=True, env=env)


class CountedOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, _path.GIT_ENV)
        env.start()
        self.addCleanup(env.stop)

    def test_the_same_project_cloned_twice_or_forked_is_not_counted_twice(self):
        a = _path.make_repo(os.path.join(self.tmp.name, "a"), [("me@w.com", "2026-01-01T00:00:00Z"),
                                                               ("me@w.com", "2026-01-02T00:00:00Z")])
        fork = os.path.join(self.tmp.name, "fork")
        subprocess.run(["git", "clone", "-q", a, fork], check=True)
        commit(fork, "me@w.com", "2026-02-02T00:00:00Z")                       # one commit only in the fork
        got = scan.scan_all([a, fork], [], {"me@w.com"}, NO_LOGIN, no_fetch=True)
        self.assertEqual([sum(c.values()) for _, c in got], [2, 1])

    def test_two_different_commits_at_the_same_second_both_count(self):
        got = scan.count_once([("a", {"h1": "2026-01-01T00:00:00Z"}), ("b", {"h2": "2026-01-01T00:00:00Z"})])
        self.assertEqual(sum((got[0][1] + got[1][1]).values()), 2)


class CloneCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, _path.GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        self.work = _path.make_repo(os.path.join(self.tmp.name, "work"), [("me@w.com", "2026-01-01T00:00:00Z")])
        self.url = "file://" + self.work.replace(os.sep, "/") if os.name != "nt" else "file:///" + self.work.replace(os.sep, "/")
        self.cache = os.path.join(self.tmp.name, "cache")

    def scan(self):
        return scan.remote_dates(self.url, {"me@w.com"}, NO_LOGIN, self.cache)

    def test_first_run_clones_later_runs_only_fetch_new_commits(self):
        commits, err = self.scan()
        self.assertEqual((len(commits), err), (1, None))
        cached = scan.cache_path(self.cache, self.url)
        self.assertTrue(os.path.isdir(cached))
        self.assertNotIn("work", os.path.basename(cached))                      # no repo name in the folder name
        commit(self.work, "me@w.com", "2026-03-03T00:00:00Z")
        commits, err = self.scan()
        self.assertEqual((sorted(commits.values()), err), (["2026-01-01T00:00:00Z", "2026-03-03T00:00:00Z"], None))

    def test_an_unreachable_remote_still_scans_the_cached_copy(self):
        self.scan()
        subprocess.run(["git", "-C", scan.cache_path(self.cache, self.url), "remote", "set-url", "origin",
                        self.url + "-gone"], check=True)
        commits, err = self.scan()
        self.assertEqual(len(commits), 1)
        self.assertIn("scanned the cached copy", err)

    def test_without_a_cache_nothing_is_kept(self):
        commits, err = scan.remote_dates(self.url, {"me@w.com"}, NO_LOGIN)
        self.assertEqual((len(commits), err), (1, None))
        self.assertFalse(os.path.exists(self.cache))


if __name__ == "__main__":
    unittest.main()
