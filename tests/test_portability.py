"""Things that only break on some operating systems (Windows read-only files, consoles, odd paths)."""
import os
import stat
import tempfile
import unittest
from collections import Counter
from unittest import mock

import _path
from contribution_mirror import cli, gitutil, remotes, scan


class Windows(unittest.TestCase):
    def test_rmtree_removes_read_only_files_like_gits_pack_files(self):
        with tempfile.TemporaryDirectory() as d:
            tree = os.path.join(d, "clone.git", "objects", "pack")
            os.makedirs(tree)
            pack = os.path.join(tree, "pack-1.pack")
            with open(pack, "w") as f:
                f.write("x")
            os.chmod(pack, stat.S_IREAD)                      # on Windows this makes a plain rmtree fail
            gitutil.rmtree(os.path.join(d, "clone.git"))
            self.assertFalse(os.path.exists(os.path.join(d, "clone.git")))

    def test_a_temporary_clone_never_survives_a_scan(self):
        def fake_clone(args, env, timeout):
            target = args[-1]
            os.makedirs(os.path.join(target, "objects"))
            ro = os.path.join(target, "objects", "p.pack")
            with open(ro, "w") as f:
                f.write("x")
            os.chmod(ro, stat.S_IREAD)
            return None
        before = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("mirror-scan-")}
        with mock.patch.object(scan, "run_git", fake_clone), mock.patch.object(scan, "source_commits", return_value={"h": "d"}):
            commits, err = scan.remote_dates("git@github.com:o/r.git", {"me"}, mock.Mock())
        after = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("mirror-scan-")}
        self.assertEqual((commits, err), ({"h": "d"}, None))
        self.assertEqual(after - before, set())

    def test_windows_paths_are_paths_not_urls(self):
        for p in (r"C:\Users\me\work\repo", "C:/Users/me/work/repo", r"\\server\share\repo", "D:repo"):
            self.assertFalse(remotes.is_url(p), p)

    def test_console_that_cannot_print_a_character_does_not_crash(self):
        class Narrow:
            errors = "strict"
            def reconfigure(self, errors):
                self.errors = errors
        out, err = Narrow(), Narrow()
        with mock.patch.object(cli.sys, "stdout", out), mock.patch.object(cli.sys, "stderr", err):
            cli._console_safe()
        self.assertEqual((out.errors, err.errors), ("replace", "replace"))


class Everywhere(unittest.TestCase):
    def test_folders_with_spaces_and_unicode_work(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _path.make_repo(os.path.join(d, "My Projects", "répo 1"), [("me@w.com", "2026-01-01T00:00:00Z")])
            self.assertEqual(gitutil.source_dates(repo, {"me@w.com"}, env=_path.GIT_ENV), Counter({"2026-01-01T00:00:00Z": 1}))

    def test_author_names_with_non_ascii_characters_do_not_break_the_scan(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _path.make_repo(os.path.join(d, "r"), [("müller@w.com", "2026-01-01T00:00:00Z")])
            self.assertEqual(gitutil.source_dates(repo, {"müller@w.com"}, env=_path.GIT_ENV), Counter({"2026-01-01T00:00:00Z": 1}))


if __name__ == "__main__":
    unittest.main()
