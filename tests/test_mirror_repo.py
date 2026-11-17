import os
import subprocess
import tempfile
import unittest
from collections import Counter

import _path  # noqa: F401
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


if __name__ == "__main__":
    unittest.main()
