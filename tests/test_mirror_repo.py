import os
import subprocess
import tempfile
import unittest
from collections import Counter
from unittest import mock

import _path
from contribution_mirror import mirror_repo

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class RawDate(unittest.TestCase):
    def test_keeps_the_utc_offset(self):
        self.assertEqual(mirror_repo.raw_date("2026-11-16T15:44:09Z"), (1794843849, "+0000"))
        self.assertEqual(mirror_repo.raw_date("2026-04-28T00:23:45+05:00")[1], "+0500")
        self.assertEqual(mirror_repo.raw_date("2026-04-28T00:23:45-03:30")[1], "-0330")


class WriteCommits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mirror = os.path.join(self.tmp.name, "m")

    def git(self, *a):
        return subprocess.run(["git", "-C", self.mirror, *a], capture_output=True, text=True, check=True,
                              env=_path.GIT_ENV).stdout.strip()

    def test_missing_or_unborn_repo_is_empty(self):
        self.assertEqual(mirror_repo.existing_dates(self.mirror), Counter())
        mirror_repo.ensure_repo(self.mirror)
        self.assertEqual(mirror_repo.existing_dates(self.mirror), Counter())

    def test_commits_roundtrip_with_exact_dates_identity_and_empty_tree(self):
        dates = ["2026-01-01T10:00:00+05:00", "2026-01-01T10:00:00+05:00", "2026-02-02T08:00:00Z"]
        mirror_repo.ensure_repo(self.mirror)
        mirror_repo.write_commits(self.mirror, dates, "Me", "me@personal.com")
        self.assertEqual(mirror_repo.existing_dates(self.mirror), Counter(dates))
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae> | %cn <%ce> | %s"),
                         "Me <me@personal.com> | Me <me@personal.com> | mirror")
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), EMPTY_TREE)
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "3")
        self.git("fsck", "--strict")

    def test_second_batch_continues_the_branch(self):
        mirror_repo.ensure_repo(self.mirror)
        mirror_repo.write_commits(self.mirror, ["2026-01-01T00:00:00Z"], "Me", "me@p.com")
        mirror_repo.write_commits(self.mirror, ["2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"], "Me", "me@p.com")
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "3")      # appended, not replaced


class OldGitDates(unittest.TestCase):
    def test_a_mirror_read_by_git_before_2_45_matches_utc_dates(self):
        old_git = lambda repo, *a, env=None: iter(["2026-01-01T10:00:00+00:00", "2026-01-02T10:00:00+05:00"])
        with mock.patch.object(mirror_repo, "git_lines", old_git):
            got = mirror_repo.dates_in("m", "main")
        self.assertEqual(got, Counter({"2026-01-01T10:00:00Z": 1, "2026-01-02T10:00:00+05:00": 1}))


class Reconcile(unittest.TestCase):
    """A second computer (or a fresh mirror folder) must build on what the personal repo already has."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.remote = os.path.join(self.tmp.name, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.remote], check=True, env=_path.GIT_ENV)
        env = mock.patch.dict(os.environ, _path.GIT_ENV)
        env.start()
        self.addCleanup(env.stop)

    def machine(self, name, dates):
        m = os.path.join(self.tmp.name, name)
        mirror_repo.ensure_repo(m)
        if dates:
            mirror_repo.write_commits(m, dates, "Me", "me@p.com")
        subprocess.run(["git", "-C", m, "remote", "add", "origin", self.remote], check=True)
        return m

    def push(self, m):
        subprocess.run(["git", "-C", m, "push", "-q", "origin", "main"], check=True, capture_output=True)

    def fetch(self, m):
        subprocess.run(["git", "-C", m, "fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main"],
                       check=True, capture_output=True)

    def test_new_machine_starts_from_the_remote(self):
        a = self.machine("a", ["2026-01-01T00:00:00Z"])
        self.push(a)
        b = self.machine("b", [])
        self.fetch(b)
        self.assertIn("remote", mirror_repo.reconcile(b, "Me", "me@p.com"))
        self.assertEqual(mirror_repo.existing_dates(b), Counter({"2026-01-01T00:00:00Z": 1}))

    def test_diverged_history_keeps_every_date_once(self):
        a = self.machine("a", ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"])
        self.push(a)
        b = self.machine("b", ["2026-01-01T00:00:00Z", "2026-03-03T00:00:00Z"])   # one date in common, one new
        self.fetch(b)
        self.assertIn("1 kept", mirror_repo.reconcile(b, "Me", "me@p.com"))
        self.assertEqual(mirror_repo.existing_dates(b), Counter(["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z",
                                                                 "2026-03-03T00:00:00Z"]))
        self.push(b)                                                           # a fast-forward now

    def test_ahead_or_equal_is_left_alone(self):
        a = self.machine("a", ["2026-01-01T00:00:00Z"])
        self.push(a)
        self.fetch(a)
        self.assertIsNone(mirror_repo.reconcile(a, "Me", "me@p.com"))
        mirror_repo.write_commits(a, ["2026-02-01T00:00:00Z"], "Me", "me@p.com")
        self.assertIsNone(mirror_repo.reconcile(a, "Me", "me@p.com"))
        self.assertEqual(sum(mirror_repo.existing_dates(a).values()), 2)


if __name__ == "__main__":
    unittest.main()
