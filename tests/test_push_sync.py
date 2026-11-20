"""push + sync end to end, offline: real git repos, a local bare repo as the personal remote."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _path
from contribution_mirror import push, sync
from contribution_mirror.config import Config
from contribution_mirror.push import PushError


def git(*args, cwd=None):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True, cwd=cwd, env=_path.GIT_ENV).stdout.strip()


class SyncSetup(unittest.TestCase):
    """A work repo, a local bare repo as the personal remote, and a config/.env pointing at them."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.remote = os.path.join(t, "personal-mirror.git")
        git("init", "-q", "--bare", "-b", "main", self.remote)
        self.work = _path.make_repo(os.path.join(t, "work"), [("me@work.com", "2026-01-01T10:00:00+05:00"),
                                                               ("me@work.com", "2026-01-02T10:00:00+00:00")])
        self.mirror = os.path.join(t, "mirror")
        self.env_file = os.path.join(t, ".env")
        with open(self.env_file, "w") as f:
            f.write(f"WORK_EMAIL=me@work.com\nPERSONAL_NAME=Me\nPERSONAL_EMAIL=me@personal.com\n"
                    f"EXPECTED_REMOTE={self.remote}\nMIRROR_DIR={self.mirror}\nMIRROR_PR_REVIEWS=off\n"
                    f"MIRROR_LOG_DIR={os.path.join(t, 'logs')}\n")
        env = mock.patch.dict(os.environ, {**_path.GIT_ENV, "MIRROR_ENV_FILE": self.env_file})
        env.start()
        self.addCleanup(env.stop)
        for k in ("WORK_EMAIL", "PERSONAL_NAME", "PERSONAL_EMAIL", "EXPECTED_REMOTE", "MIRROR_DIR", "MIRROR_PR_REVIEWS",
                  "MIRROR_LOG_DIR", "PERSONAL_SSH_KEY", "WORK_GH_USER"):
            os.environ.pop(k, None)

    def run_sync(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sync.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def remote_count(self):
        return int(git("rev-list", "--count", "main", cwd=self.remote))


class SyncEndToEnd(SyncSetup):
    def test_dry_run_changes_nothing(self):
        code, out, _ = self.run_sync("--dry-run", self.work)
        self.assertEqual(code, 0)
        self.assertIn("would_create=2", out)
        self.assertFalse(os.path.exists(self.mirror))

    def test_first_sync_connects_the_remote_and_pushes_then_it_is_idempotent(self):
        code, out, _ = self.run_sync(self.work)
        self.assertEqual(code, 0)
        self.assertEqual(self.remote_count(), 2)
        self.assertIn("pushed", out)
        self.assertEqual(git("remote", "get-url", "origin", cwd=self.mirror), self.remote)   # added automatically
        code, out, _ = self.run_sync(self.work)
        self.assertEqual(code, 0)
        self.assertIn("nothing to push", out)
        self.assertEqual(self.remote_count(), 2)

    def test_a_new_commit_is_pushed_alone(self):
        self.run_sync(self.work)
        env = {**_path.GIT_ENV, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "me@work.com", "GIT_AUTHOR_DATE": "2026-05-05T10:00:00+00:00",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "me@work.com", "GIT_COMMITTER_DATE": "2026-05-05T10:00:00+00:00"}
        subprocess.run(["git", "-C", self.work, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "n"], check=True, env=env)
        self.run_sync(self.work)
        self.assertEqual(self.remote_count(), 3)

    def test_refuses_to_push_to_any_other_repo(self):
        self.run_sync(self.work)
        other = os.path.join(self.tmp.name, "someone-elses.git")
        git("init", "-q", "--bare", other)
        git("remote", "set-url", "origin", other, cwd=self.mirror)
        git("update-ref", "-d", "refs/remotes/origin/main", cwd=self.mirror)        # force a push attempt
        code, _, err = self.run_sync(self.work)
        self.assertEqual(code, 1)
        self.assertIn("refusing to push", err)
        self.assertEqual(git("rev-list", "--all", "--count", cwd=other), "0")           # nothing reached it

    def test_missing_folder_and_missing_config_are_clear_errors(self):
        code, _, err = self.run_sync("no-such-folder-xyz")
        self.assertEqual((code, "folder not found" in err), (1, True))
        os.environ["MIRROR_ENV_FILE"] = os.path.join(self.tmp.name, "nope.env")
        code, _, err = self.run_sync(self.work)
        self.assertEqual((code, "mirror.py setup" in err), (1, True))


class PushFallbacks(unittest.TestCase):
    CFG = Config([], "Me", "m@p.com", "git@github.com:me/mirror.git", "/m")

    def test_ssh_key_applies_to_the_push_only_and_survives_windows_paths(self):
        self.assertNotIn("GIT_SSH_COMMAND", push._push_env("") if "GIT_SSH_COMMAND" not in os.environ else {})
        env = push._push_env("C:\\Users\\me\\.ssh\\id key")
        self.assertEqual(env["GIT_SSH_COMMAND"], "ssh -i 'C:/Users/me/.ssh/id key' -o IdentitiesOnly=yes -o BatchMode=yes")
        env = push._push_env("/k/it's $(touch x) `y` \"z\".key")             # nothing in the path is run by sh
        self.assertEqual(env["GIT_SSH_COMMAND"].split(" -o ")[0],
                         "ssh -i '/k/it'\\''s $(touch x) `y` \"z\".key'")

    def test_normal_push_failure_falls_back_to_a_token(self):
        calls = []
        def fake_git(mirror, *args, env=None):
            calls.append(args[:2])
            if args[:1] == ("push",):
                return 1, "", "permission denied"
            if args[0] == "rev-parse":                   # local main differs from the remote-tracking ref: there is something to push
                return (0, "local-sha" if args[-1] == "main" else "remote-sha", "")
            return {"remote": (0, "git@github.com:me/mirror.git", "")}.get(args[0], (0, "", ""))
        with mock.patch.object(push, "_git", fake_git), mock.patch.object(push, "_push_with_token", return_value=None):
            self.assertEqual(push.push_mirror(self.CFG), "pushed")
        with mock.patch.object(push, "_git", fake_git), mock.patch.object(push, "_push_with_token", return_value="no token"):
            with self.assertRaisesRegex(PushError, "permission denied.*no token"):
                push.push_mirror(self.CFG)

    def test_the_token_push_goes_over_https_with_the_right_header(self):
        seen = {}
        def fake_run_git(args, env, timeout):
            seen.update(args=args, env=env)
            return None
        from contribution_mirror.providers import PROVIDERS, Token
        remote = {"git@github.com:me/mirror.git": "x"}
        with mock.patch.object(push, "run_git", fake_run_git), mock.patch.object(push, "_git", return_value=(0, "", "")), \
                mock.patch.object(push, "real_host", lambda u: "github.com"), \
                mock.patch.object(type(PROVIDERS["github"]), "cli_tokens", lambda self, scope, only_user=None: iter([Token("gh:me", "SECRET")])), \
                mock.patch.object(push, "helper_token", lambda h: None):
            self.assertIsNone(push._push_with_token(self.CFG))
        self.assertIn("https://github.com/me/mirror.git", seen["args"])
        self.assertEqual(seen["args"][-1], "main:main")
        self.assertTrue(seen["env"]["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic "))
        self.assertNotIn("SECRET", " ".join(seen["args"]))                      # never on the command line

    def test_no_token_anywhere_gives_a_helpful_message(self):
        from contribution_mirror.providers import PROVIDERS
        with mock.patch.object(push, "real_host", lambda u: "github.com"), mock.patch.dict(os.environ, {"GH_TOKEN": "", "GITHUB_TOKEN": ""}), \
                mock.patch.object(type(PROVIDERS["github"]), "cli_tokens", lambda self, scope, only_user=None: iter(())), \
                mock.patch.object(push, "helper_token", lambda h: None):
            self.assertIn("no token found", push._push_with_token(self.CFG))


class TheFallbackTokenMustOwnTheMirror(unittest.TestCase):
    def owns(self, login, source="GH_TOKEN"):
        from contribution_mirror.providers import PROVIDERS, Token
        github = PROVIDERS["github"]
        with mock.patch.object(type(github), "whoami", lambda self, api: login):
            return push._token_owns(github, "github.com", Token(source, "t"), "me")

    def test_a_work_account_token_is_not_used_to_push(self):
        self.assertTrue(self.owns("Me"))
        self.assertFalse(self.owns("me-at-work"))
        self.assertTrue(self.owns("anyone", source="gh:me"))          # gh CLI token asked for by the owner's name

    def test_where_a_tokens_account_cannot_be_checked_only_the_hosts_own_git_login_is_used(self):
        """Bitbucket, Azure DevOps, unknown hosts: a repo's owner is not a login, so a token from a variable or a CLI
        (maybe the work account's) is never tried; only the credential helper's entry for that host, as `git push`."""
        from contribution_mirror.providers import PROVIDERS, Token
        bitbucket = PROVIDERS["bitbucket"]
        cfg = Config([], "Me", "m@p.com", "git@bitbucket.org:me/mirror.git", "/m")
        with mock.patch.dict(os.environ, {"BITBUCKET_TOKEN": "work-token"}), \
                mock.patch.object(type(bitbucket), "cli_tokens", lambda s, scope, only_user=None: iter([Token("cli", "c")])), \
                mock.patch.object(push, "helper_token", lambda h: Token("git-credential", "mine")):
            _, _, _, tokens = push._mirror_tokens(cfg)
        self.assertEqual([t.source for t in tokens], ["git-credential"])

    def test_errors_never_show_a_token_from_a_url(self):
        cfg = Config([], "Me", "m@p.com", "https://me:SECRET@github.com/me/mirror.git", "/m")
        fake = lambda mirror, *a, env=None: (0, "https://x:SECRET@github.com/evil/repo.git", "")
        with mock.patch.object(push, "_git", fake), self.assertRaises(PushError) as cm:
            push.ensure_remote(cfg)
        self.assertNotIn("SECRET", str(cm.exception))
        self.assertIn("https://***@github.com/evil/repo.git", str(cm.exception))


class TwoComputers(SyncSetup):
    """The same personal repo synced from two computers (two mirror folders): nothing is ever doubled."""
    def second_computer(self):
        other = os.path.join(self.tmp.name, "mirror-on-laptop")
        with open(self.env_file, "a") as f:
            f.write(f"MIRROR_DIR={other}\n")
        with open(self.env_file) as f:
            text = f.read().replace(f"MIRROR_DIR={self.mirror}\n", "")
        with open(self.env_file, "w") as f:
            f.write(text)
        os.environ.pop("MIRROR_DIR", None)          # the first run loaded it; a variable that is set wins over the file
        return other

    def test_a_second_computer_adds_only_what_is_new(self):
        self.assertEqual(self.run_sync(self.work)[0], 0)
        laptop = self.second_computer()
        code, out, err = self.run_sync(self.work)
        self.assertEqual(code, 0, err)
        self.assertIn("started from the remote's history", out)
        self.assertEqual(int(git("rev-list", "--count", "main", cwd=laptop)), 2)
        self.assertEqual(self.remote_count(), 2)                       # not 4
        env = {**_path.GIT_ENV, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "me@work.com", "GIT_AUTHOR_DATE": "2026-06-06T10:00:00+00:00",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "me@work.com", "GIT_COMMITTER_DATE": "2026-06-06T10:00:00+00:00"}
        subprocess.run(["git", "-C", self.work, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "n"], check=True, env=env)
        self.assertEqual(self.run_sync(self.work)[0], 0)
        self.assertEqual(self.remote_count(), 3)

    def test_a_dry_run_does_not_touch_the_remote_tracking_ref(self):
        self.run_sync(self.work)
        before = git("rev-parse", "refs/remotes/origin/main", cwd=self.mirror)
        git("update-ref", "-d", "refs/remotes/origin/main", cwd=self.mirror)
        self.run_sync("--dry-run", self.work)
        r = subprocess.run(["git", "-C", self.mirror, "rev-parse", "-q", "--verify", "refs/remotes/origin/main"],
                           capture_output=True, text=True, env=_path.GIT_ENV)
        self.assertEqual((r.returncode, bool(before)), (1, True))

    def test_a_run_while_another_is_in_progress_is_refused_cleanly(self):
        from contribution_mirror.runlock import run_lock
        logs = os.path.join(self.tmp.name, "logs")
        script = ("import sys,time; sys.path.insert(0, sys.argv[1]); from contribution_mirror.runlock import run_lock\n"
                  "with run_lock(sys.argv[2]):\n    print('locked', flush=True); time.sleep(30)")
        holder = subprocess.Popen([sys.executable, "-c", script, os.path.join(_path.ROOT, "src"), logs],
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "locked")
        code, _, err = self.run_sync(self.work)
        self.assertEqual(code, 1)
        self.assertIn("another run is in progress", err)
        holder.kill()
        holder.wait()
        holder.stdout.close()
        self.assertEqual(self.run_sync(self.work)[0], 0)                # released by the OS when the holder died
        with run_lock(logs), run_lock(logs):                            # re-entrant in one process
            pass


if __name__ == "__main__":
    unittest.main()
