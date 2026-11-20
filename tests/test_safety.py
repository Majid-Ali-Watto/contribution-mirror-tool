"""Safety nets around the user's data: private files, temporary clones that never outlive a run (even a cancelled
one), a mirror folder that is really a mirror, and credentials kept out of every message."""
import contextlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone
from unittest import mock

import _path
from contribution_mirror import config, envfile, fsutil, logsetup, mirror_repo, scan, state

POSIX = os.name != "nt"


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name


@unittest.skipUnless(POSIX, "file modes are a Linux/macOS matter")
class PrivateFiles(Tmp):
    def test_logs_and_state_are_owner_only_even_if_they_were_not_before(self):
        log_dir = os.path.join(self.dir, "logs")
        os.makedirs(log_dir)
        old = os.path.join(log_dir, "mirror.log.1")
        open(old, "w").close()
        os.chmod(old, 0o644)                                # written by an older version
        logsetup.setup_log(log_dir)
        try:
            logsetup.log.warning("names a work repo")
        finally:
            logsetup.close_log()
        state.save_state(log_dir, {"github.com": ("me", "gh:me")}, datetime.now(timezone.utc))
        for name in ("mirror.log", "mirror.log.1", "api-state.json"):
            self.assertEqual(mode(os.path.join(log_dir, name)), 0o600, name)

    def test_a_new_log_folder_is_owner_only(self):
        log_dir = os.path.join(self.dir, "new", "logs")
        logsetup.setup_log(log_dir)
        logsetup.close_log()
        self.assertEqual(mode(log_dir), 0o700)

    def test_write_private_replaces_a_planted_symlink_instead_of_writing_through_it(self):
        target, path = os.path.join(self.dir, "elsewhere"), os.path.join(self.dir, "f.json")
        open(target, "w").close()
        os.symlink(target, path + ".tmp")
        fsutil.write_private(path, "x")
        self.assertEqual(read(target), "")
        self.assertEqual((mode(path), read(path)), (0o600, "x"))


class Setup(Tmp):
    def test_setup_creates_both_files_from_the_templates_once_and_privately(self):
        env, repos = os.path.join(self.dir, "cfg", ".env"), os.path.join(self.dir, "cfg", "repos.json")
        with mock.patch.dict(os.environ, {"MIRROR_ENV_FILE": env, "MIRROR_REPOS_FILE": repos,
                                          "MIRROR_LOG_DIR": os.path.join(self.dir, "logs")}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(config.setup_main([]), 0)
            self.assertIn("created: " + env, out.getvalue())
            self.assertIn("WORK_EMAIL=", read(env))
            self.assertIn('"repos"', read(repos))
            with open(env, "w") as f:
                f.write("WORK_EMAIL=mine@w.com\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                config.setup_main([])
            self.assertIn("exists:  " + env, out.getvalue())
            self.assertEqual(read(env), "WORK_EMAIL=mine@w.com\n")       # never overwritten
        if POSIX:
            self.assertEqual((mode(env), mode(repos)), (0o600, 0o600))


class TemporaryClones(Tmp):
    def test_clones_go_into_the_runs_own_folder_and_are_deleted(self):
        root = os.path.join(self.dir, "tmp")
        os.makedirs(root)
        seen = []
        def fake_clone(args, env, timeout):
            seen.append(args[-1])
            os.makedirs(args[-1])
        with mock.patch.object(scan, "run_git", fake_clone), mock.patch.object(scan, "source_commits", return_value={}):
            scan.remote_dates("git@github.com:o/r.git", {"me"}, mock.Mock(), tmp_root=root)
        self.assertTrue(seen[0].startswith(root + os.sep))
        self.assertEqual(os.listdir(root), [])

    def test_the_next_run_deletes_what_a_killed_run_left(self):
        root = os.path.join(self.dir, "tmp")
        os.makedirs(os.path.join(root, "mirror-scan-old", "r", "objects"))
        scan.clear_tmp(root)
        self.assertEqual(os.listdir(root), [])
        if POSIX:
            self.assertEqual(mode(root), 0o700)

    @unittest.skipUnless(POSIX, "SIGTERM is how Linux/macOS stop a run (Windows: the next run cleans up)")
    def test_sigterm_runs_the_cleanup(self):
        """The page's Cancel sends SIGTERM: the run must leave through its `finally` blocks, like on Ctrl+C."""
        leftover = os.path.join(self.dir, "clone")
        script = textwrap.dedent(f"""
            import os, sys, time
            sys.path.insert(0, {os.path.join(_path.ROOT, 'src')!r})
            from contribution_mirror import cli
            cli._exit_on_sigterm()
            os.makedirs({leftover!r})
            try:
                print("ready", flush=True)
                time.sleep(60)
            finally:
                os.rmdir({leftover!r})
        """)
        p = subprocess.Popen([sys.executable, "-I", "-c", script], stdout=subprocess.PIPE, text=True)
        with p:
            self.assertEqual(p.stdout.readline().strip(), "ready")
            p.terminate()
            self.assertEqual(p.wait(timeout=30), 128 + 15)
        self.assertFalse(os.path.exists(leftover))


class MirrorFolder(Tmp):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, _path.GIT_ENV)
        env.start()
        self.addCleanup(env.stop)

    def marked(self, path):
        return subprocess.run(["git", "-C", path, "config", "--get", mirror_repo.MARKER], capture_output=True,
                              text=True).stdout.strip() == "true"

    def test_a_new_mirror_is_marked(self):
        path = os.path.join(self.dir, "m")
        mirror_repo.ensure_repo(path)
        self.assertTrue(self.marked(path))
        mirror_repo.ensure_repo(path)                                      # and accepted from then on

    def test_an_existing_mirror_from_before_the_marker_is_adopted(self):
        path = os.path.join(self.dir, "m")
        mirror_repo.ensure_repo(path)
        subprocess.run(["git", "-C", path, "config", "--unset", mirror_repo.MARKER], check=True)
        mirror_repo.write_commits(path, ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00+05:00"], "Me", "m@p.com")
        mirror_repo.ensure_repo(path)
        self.assertTrue(self.marked(path))

    def test_a_folder_with_real_work_is_refused_and_left_alone(self):
        path = _path.make_repo(os.path.join(self.dir, "work"), [("me@w.com", "2026-01-01T00:00:00Z")])
        head = subprocess.run(["git", "-C", path, "rev-parse", "main"], capture_output=True, text=True).stdout
        with self.assertRaisesRegex(mirror_repo.NotAMirror, "not your contribution mirror"):
            mirror_repo.ensure_repo(path)
        self.assertFalse(self.marked(path))
        self.assertEqual(subprocess.run(["git", "-C", path, "rev-parse", "main"], capture_output=True,
                                        text=True).stdout, head)


class UntouchedTemplate(Tmp):
    """Settings and projects nobody filled in are not run: they would clone the example repos and push to the
    example mirror (`your-user/work-contributions`)."""
    def setUp(self):
        super().setUp()
        self.env_file, self.repos_file = os.path.join(self.dir, "cfg", ".env"), os.path.join(self.dir, "cfg", "repos.json")
        env = mock.patch.dict(os.environ, {"MIRROR_ENV_FILE": self.env_file, "MIRROR_REPOS_FILE": self.repos_file,
                                           "HOME": self.dir, "USERPROFILE": self.dir})
        env.start()
        self.addCleanup(env.stop)
        for k in config.REQUIRED + ("MIRROR_DIR", "MIRROR_LOG_DIR"):
            os.environ.pop(k, None)
        with contextlib.redirect_stdout(io.StringIO()):
            config.setup_main([])                              # exactly what a new user gets

    def test_a_sync_right_after_setup_stops_before_doing_anything(self):
        from contribution_mirror import sync
        with mock.patch.object(subprocess, "run") as run, mock.patch.object(subprocess, "Popen") as popen, \
                contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sync.main([]), 1)
        self.assertIn("template's example value for WORK_EMAIL, PERSONAL_NAME, PERSONAL_EMAIL, EXPECTED_REMOTE",
                      err.getvalue())
        run.assert_not_called()                                 # no git, no ssh, no network
        popen.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "work-contributions")))

    def test_one_example_value_is_enough_to_stop(self):
        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write("WORK_EMAIL=me@work.com you@company.com\nPERSONAL_NAME=Me\nPERSONAL_EMAIL=me@personal.com\n"
                    "EXPECTED_REMOTE=git@github.com:me/mirror.git\n")
        with self.assertRaisesRegex(envfile.EnvFileError, "example value for WORK_EMAIL:"):
            config.load_config()

    def test_example_projects_are_never_scanned(self):
        from contribution_mirror import repos
        with self.assertRaisesRegex(repos.ReposFileError, "only the template's examples"):
            repos.load_repos_file(self.repos_file)
        mine = _path.make_repo(os.path.join(self.dir, "mine"), [])
        entries = repos.read_entries(self.repos_file).entries
        repos.save_entries(self.repos_file, entries + [mine])
        with contextlib.redirect_stderr(io.StringIO()) as err:
            local, urls, _ = repos.load_repos_file(self.repos_file)
        self.assertEqual((local, urls), ([mine], []))
        self.assertIn(f"skipping {len(entries)} example entries", err.getvalue())
        self.assertEqual(repos.check_entry(entries[1])["ok"], False)

    def test_doctor_says_what_to_fill_in(self):
        from contribution_mirror import doctor
        with mock.patch.dict(os.environ), contextlib.redirect_stderr(io.StringIO()):
            first = doctor.run_checks(include_schedule=False)[0]
        self.assertEqual(first.status, "fail")
        self.assertIn("template's example value", first.detail)


class Redaction(unittest.TestCase):
    def test_every_form_of_credential_is_hidden(self):
        cases = {
            "fatal: https://user:tok123@github.com/o/r": "fatal: https://***@github.com/o/r",
            "clone https://tok123@host/o/r failed": "clone https://***@host/o/r failed",
            "git+https://u:tok123@host/o/r": "git+https://***@host/o/r",
            "ssh://git:tok123@host/o/r": "ssh://git:***@host/o/r",
            "Authorization: Basic dG9rMTIz": "Authorization: Basic ***",
            "authorization: bearer tok123 sent": "authorization: bearer *** sent",
        }
        for text, want in cases.items():
            self.assertEqual(logsetup.redact(text), want)

    def test_ordinary_urls_and_paths_are_unchanged(self):
        for text in ("git@github.com:o/r.git", "ssh://git@host:22/o/r", "https://github.com/o/r", "/a/b:c@d"):
            self.assertEqual(logsetup.redact(text), text)


if __name__ == "__main__":
    unittest.main()
