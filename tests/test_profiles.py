"""Profiles: several setups (e.g. one per employer), each with its own settings and project list, side by side."""
import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
from contribution_mirror import cli, config, scheduled, state
from contribution_mirror.report import RunResult


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.join(self.tmp.name, "cfg")
        self.default_env = os.path.join(self.base, ".env")
        patches = [mock.patch.dict(os.environ), mock.patch.object(config, "DEFAULT_ENV_FILE", self.default_env),
                   mock.patch.object(config, "REPOS_FILE", os.path.join(self.base, "repos.json"))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for k in ("MIRROR_PROFILE", "MIRROR_ENV_FILE", "MIRROR_REPOS_FILE", "WORK_EMAIL", "PERSONAL_NAME",
                  "PERSONAL_EMAIL", "EXPECTED_REMOTE", "MIRROR_LOG_DIR", "MIRROR_DIR"):
            os.environ.pop(k, None)

    def write_env(self, name, email):
        path = config.profile_paths(name)[0]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"WORK_EMAIL={email}\nPERSONAL_NAME=Me\nPERSONAL_EMAIL=m@p.com\n"
                    f"EXPECTED_REMOTE=git@github.com:me/m.git\nMIRROR_LOG_DIR={self.tmp.name}\n")
        return path


class Paths(Tmp):
    def test_each_profile_has_its_own_files_next_to_the_default_ones(self):
        self.assertEqual(config.profile_paths(""), (self.default_env, os.path.join(self.base, "repos.json")))
        self.assertEqual(config.profile_paths("acme"), (os.path.join(self.base, "profiles", "acme", ".env"),
                                                         os.path.join(self.base, "profiles", "acme", "repos.json")))
        os.environ["MIRROR_PROFILE"] = "acme"
        self.assertEqual((config.env_path(), config.repos_path()), config.profile_paths("acme"))
        os.environ["MIRROR_ENV_FILE"] = "/elsewhere/.env"                   # an explicit file still wins
        self.assertEqual(config.env_path(), "/elsewhere/.env")

    def test_names(self):
        for good in ("acme", "Old-Job_2", "a"):
            self.assertEqual(config.check_profile_name(good), good)
        for bad in ("../x", "a b", "-x", "_new", "x" * 41, "a/b"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                config.check_profile_name(bad)

    def test_listing_only_counts_profiles_with_settings(self):
        self.assertEqual(config.list_profiles(), [])
        self.write_env("", "a@w.com")
        self.write_env("zeta", "z@w.com")
        self.write_env("acme", "b@w.com")
        os.makedirs(os.path.join(self.base, "profiles", "empty"))
        self.assertEqual(config.list_profiles(), ["", "acme", "zeta"])

    def test_each_profile_keeps_its_own_login_state(self):
        self.assertTrue(state.state_path("/l").endswith("api-state.json"))
        os.environ["MIRROR_PROFILE"] = "acme"
        self.assertTrue(state.state_path("/l").endswith("api-state-acme.json"))

    def test_the_command_line_chooses_a_profile_first(self):
        self.write_env("acme", "b@w.com")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--profile", "acme", "setup"]), 0)
        self.assertIn(config.profile_paths("acme")[0], out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["--profile=../etc", "setup"]), 2)
        self.assertIn("cannot name a profile", err.getvalue())

    def test_the_environment_is_restored_after_each_profile(self):
        os.environ["KEEP"] = "1"
        with config.profile_environment("acme"):
            os.environ["WORK_EMAIL"] = "loaded@w.com"
            self.assertEqual(os.environ["MIRROR_PROFILE"], "acme")
        self.assertNotIn("WORK_EMAIL", os.environ)
        self.assertNotIn("MIRROR_PROFILE", os.environ)
        self.assertEqual(os.environ["KEEP"], "1")


class ScheduledRuns(Tmp):
    def setUp(self):
        super().setUp()
        self.write_env("", "default@w.com")
        self.write_env("acme", "acme@w.com")
        self.synced = []
        def run_sync(cfg):
            self.synced.append((os.environ.get("MIRROR_PROFILE", ""), cfg.work_emails))
            if cfg.work_emails == ["broken@w.com"]:
                raise RuntimeError("push failed")
            return RunResult(1, [("app", 1)], False), "pushed"
        for p in (mock.patch.object(scheduled.sync, "run_sync", run_sync), mock.patch.object(scheduled, "notify"),
                  mock.patch.object(scheduled, "real_host", lambda u: "github.com")):
            p.start()
            self.addCleanup(p.stop)
        self.notify = scheduled.notify                                       # the mock

    def run_scheduled(self, network=True):
        with mock.patch.object(scheduled, "wait_for_network", return_value=network) as wait, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = scheduled.main([])
        return code, wait

    def test_every_profile_is_synced_with_its_own_settings(self):
        code, wait = self.run_scheduled()
        self.assertEqual(code, 0)
        self.assertEqual(self.synced, [("", ["default@w.com"]), ("acme", ["acme@w.com"])])
        self.assertEqual(wait.call_count, 1)                                 # the network is checked once
        self.assertEqual([c[0][0] for c in self.notify.call_args_list],
                         ["Contribution mirror sync OK", "Contribution mirror sync OK (profile acme)"])

    def test_one_failing_profile_does_not_stop_the_others(self):
        self.write_env("", "broken@w.com")
        os.remove(self.write_env("zz", "z@w.com"))                            # a folder without settings: not a profile
        code, _ = self.run_scheduled()
        self.assertEqual(code, 1)
        self.assertEqual([p for p, _ in self.synced], ["", "acme"])

    def test_offline_stops_after_the_first_profile(self):
        code, _ = self.run_scheduled(network=False)
        self.assertEqual((code, self.synced), (1, []))

    def test_a_chosen_profile_runs_alone(self):
        os.environ["MIRROR_PROFILE"] = "acme"
        self.run_scheduled()
        self.assertEqual(self.synced, [("acme", ["acme@w.com"])])


if __name__ == "__main__":
    unittest.main()
