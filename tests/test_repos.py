import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest

import _path
from contribution_mirror import remotes, repos


class RepoList(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.local = _path.make_repo(os.path.join(self.tmp.name, "clone"), [("me@w.com", "2026-01-01T00:00:00Z")])
        remotes._hosts.clear()
        remotes._hosts.update({"github.com": "github.com", "github-work": "github.com"})
        self.addCleanup(remotes._hosts.clear)

    def write(self, cfg):
        path = os.path.join(self.tmp.name, "repos.json")
        with open(path, "w") as f:
            json.dump(cfg, f)
        return path

    def test_paths_and_urls_can_be_mixed_in_one_list(self):
        path = self.write({"repos": [self.local, "git@github-work:Org/a.git", "https://github.com/Org/b",
                                     self.local, "git@github-work:Org/a.git"]})   # duplicates ignored
        local, urls = repos.load_repos_file(path)
        self.assertEqual((local, urls), ([self.local], ["git@github-work:Org/a.git", "https://github.com/Org/b"]))

    def test_legacy_remote_repos_key_still_works_with_a_note(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            local, urls = repos.load_repos_file(self.write({"repos": [], "remote_repos": ["git@github.com:Org/c.git"]}))
        self.assertEqual((local, urls), ([], ["git@github.com:Org/c.git"]))
        self.assertIn('older "remote_repos" key', out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            repos.load_repos_file(self.write({"repos": ["git@github.com:Org/c.git"]}))
        self.assertEqual(out.getvalue(), "")                             # the current form: no note

    def test_a_path_that_is_not_a_git_repo_is_skipped_with_a_warning(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            local, _ = repos.load_repos_file(self.write({"repos": [os.path.join(self.tmp.name, "nope"), self.local]}))
        self.assertEqual(local, [self.local])
        self.assertIn("not a git repo", err.getvalue())

    def test_url_that_is_already_cloned_locally_is_not_counted_twice(self):
        subprocess.run(["git", "-C", self.local, "remote", "add", "origin", "git@github.com:Org/Clone.git"],
                       check=True, env=_path.GIT_ENV)
        path = self.write({"repos": [self.local, "https://github.com/org/clone.git", "git@github.com:Org/Other.git"]})
        local, urls = repos.select_sources(None, path, os.path.join(self.tmp.name, "mirror"))
        self.assertEqual((local, urls), ([self.local], ["git@github.com:Org/Other.git"]))

    def test_src_folders_take_precedence_over_the_repos_file(self):
        path = self.write({"repos": ["git@github.com:Org/ignored.git"]})
        local, urls = repos.select_sources([self.tmp.name], path, os.path.join(self.tmp.name, "mirror"))
        self.assertEqual((local, urls), ([self.local], []))      # found by scanning the folder

    def test_mirror_inside_a_source_repo_is_refused(self):
        with self.assertRaises(SystemExit):
            repos.select_sources([self.local], self.write({"repos": []}), os.path.join(self.local, "mirror"))

    def test_group_by_scope_uses_the_real_host_behind_aliases(self):
        subprocess.run(["git", "-C", self.local, "remote", "add", "origin", "git@github-work:Org/Clone.git"],
                       check=True, env=_path.GIT_ENV)
        got = repos.group_by_scope([self.local], ["https://github.com/Org/b", "git@github.com:org/CLONE.git"])
        self.assertEqual(got, {"github.com": ["Org/Clone", "Org/b", "org/CLONE"]})


class ReposFileProblems(RepoList):
    def raw(self, text, encoding="utf-8"):
        path = os.path.join(self.tmp.name, "repos.json")
        with open(path, "w", encoding=encoding) as f:
            f.write(text)
        return path

    def test_non_ascii_paths_and_a_windows_bom_are_read_as_utf8(self):
        repo = _path.make_repo(os.path.join(self.tmp.name, "Projets Été"), [("me@w.com", "2026-01-01T00:00:00Z")])
        local, _ = repos.load_repos_file(self.raw(json.dumps({"repos": [repo]}, ensure_ascii=False), "utf-8-sig"))
        self.assertEqual(local, [repo])

    def test_broken_files_are_clear_errors(self):
        with self.assertRaisesRegex(repos.ReposFileError, "not valid JSON"):
            repos.load_repos_file(self.raw('{"repos": [}'))
        for bad in ('["/a"]', '{"repo": []}', '{"repos": "/a"}', '{"repos": [1]}'):
            with self.subTest(bad=bad), self.assertRaisesRegex(repos.ReposFileError, "must look like"):
                repos.load_repos_file(self.raw(bad))


if __name__ == "__main__":
    unittest.main()
