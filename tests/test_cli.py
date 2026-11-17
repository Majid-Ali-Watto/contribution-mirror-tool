"""End to end, fully offline: real git repos, the real CLI, no network, no gh, no GitHub login."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import _path
from contribution_mirror import cli, scan


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.repo_a = _path.make_repo(os.path.join(t, "a"), [("me@work.com", "2026-01-01T10:00:00+05:00"),
                                                              ("me@work.com", "2026-01-02T10:00:00+00:00"),
                                                              ("other@work.com", "2026-01-03T10:00:00+00:00")])
        self.repo_b = _path.make_repo(os.path.join(t, "b"), [("me@work.com", "2026-02-01T10:00:00+00:00")],
                                      merge=("me@work.com", "2026-03-01T10:00:00+00:00"))
        self.repos_file = os.path.join(t, "repos.json")
        with open(self.repos_file, "w") as f:
            json.dump({"repos": [self.repo_a, self.repo_b]}, f)
        self.mirror = os.path.join(t, "mirror")
        self.argv = ["--work-email", "me@work.com", "--mirror", self.mirror, "--name", "Me", "--email", "me@personal.com",
                     "--repos-file", self.repos_file, "--log-dir", os.path.join(t, "logs"), "--no-api", "--no-fetch"]
        env = mock.patch.dict(os.environ, _path.GIT_ENV)
        env.start()
        self.addCleanup(env.stop)

    def run_cli(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(self.argv + list(extra))
        return out.getvalue()

    def count(self):
        r = subprocess.run(["git", "-C", self.mirror, "rev-list", "--count", "HEAD"], capture_output=True, text=True)
        return int(r.stdout) if r.returncode == 0 else 0

    def test_dry_run_writes_nothing(self):
        out = self.run_cli()
        self.assertIn("to create            : 4", out)        # 2 + (1 + merge) from b
        self.assertIn("would_create=4", out)
        self.assertFalse(os.path.exists(self.mirror))

    def test_apply_creates_exactly_my_commits_and_a_rerun_creates_none(self):
        self.assertIn("created=4", self.run_cli("--apply"))
        self.assertEqual(self.count(), 4)
        again = self.run_cli("--apply")
        self.assertIn("to create            : 0", again)
        self.assertEqual(self.count(), 4)

    def test_new_commit_later_is_picked_up_alone(self):
        self.run_cli("--apply")
        env = {**_path.GIT_ENV, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "me@work.com", "GIT_AUTHOR_DATE": "2026-04-01T10:00:00+00:00",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "me@work.com", "GIT_COMMITTER_DATE": "2026-04-01T10:00:00+00:00"}
        subprocess.run(["git", "-C", self.repo_a, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "n"],
                       check=True, env=env)
        self.assertIn("created=1", self.run_cli("--apply"))
        self.assertEqual(self.count(), 5)

    def test_scan_failure_of_one_repo_does_not_stop_the_others(self):
        calls = []
        real = scan.source_dates
        def flaky(repo, emails, env=None):
            calls.append(repo)
            if repo == self.repo_a:
                raise RuntimeError("git log failed: boom")
            return real(repo, emails, env)
        err = io.StringIO()
        with mock.patch.object(scan, "source_dates", flaky), contextlib.redirect_stderr(err):
            out = self.run_cli()
        self.assertIn("could not read commits", err.getvalue())
        self.assertIn("to create            : 2", out)        # only repo b's 2 commits


if __name__ == "__main__":
    unittest.main()
