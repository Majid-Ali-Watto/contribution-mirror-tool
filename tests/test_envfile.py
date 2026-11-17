import os
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
from contribution_mirror import config, envfile

SAMPLE = """# comment

  A=plain
B="double quoted value"
C='single quoted'
export D=~/x
E=$HOME/y
F=has=equals
EMPTY=
G=$(touch PWNED_BY_ENV)
"""


class Parse(unittest.TestCase):
    def test_values(self):
        v = envfile.parse(SAMPLE)
        home = os.path.expanduser("~")
        self.assertEqual((v["A"], v["B"], v["C"], v["F"], v["EMPTY"]), ("plain", "double quoted value", "single quoted", "has=equals", ""))
        self.assertEqual((v["D"], v["E"]), (home + "/x", home + "/y"))

    def test_nothing_is_executed(self):
        self.assertEqual(envfile.parse(SAMPLE)["G"], "$(touch PWNED_BY_ENV)")

    def test_bad_lines_are_reported_with_their_number(self):
        with self.assertRaisesRegex(envfile.EnvFileError, "bad line 2"):
            envfile.parse("A=1\noops\n")
        with self.assertRaisesRegex(envfile.EnvFileError, "bad variable name"):
            envfile.parse("1BAD=x\n")

    def test_windows_style_home(self):
        self.assertEqual(envfile.expand("~\\work"), os.path.expanduser("~") + "\\work")


class Load(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, ".env")

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_existing_environment_wins_over_the_file(self):
        self.write("A=from-file\nB=from-file\n")
        env = {"A": "from-shell"}
        envfile.load(self.path, env)
        self.assertEqual(env, {"A": "from-shell", "B": "from-file"})

    def test_missing_file_has_a_helpful_message(self):
        with self.assertRaisesRegex(envfile.EnvFileError, r"copy config/\.env\.example"):
            envfile.load(os.path.join(self.tmp.name, "nope"), {})

    def test_require_names_what_is_missing(self):
        with self.assertRaisesRegex(envfile.EnvFileError, "missing in config/.env: X Y"):
            envfile.require(["X", "Y", "Z"], {"Z": "1"})


class LoadConfig(unittest.TestCase):
    def test_full_config_and_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w") as f:
                f.write("WORK_EMAIL=a@work.com b@work.com\nPERSONAL_NAME=Me\nPERSONAL_EMAIL=me@p.com\n"
                        "EXPECTED_REMOTE=git@github.com:me/mirror.git\nMIRROR_PR_REVIEWS=OFF\n")
            with mock.patch.dict(os.environ, {}, clear=False):
                for k in ("MIRROR_DIR", "PERSONAL_SSH_KEY", "WORK_GH_USER", "MIRROR_LOG_DIR", "MIRROR_PR_REVIEWS"):
                    os.environ.pop(k, None)
                cfg = config.load_config(path)
        self.assertEqual(cfg.work_emails, ["a@work.com", "b@work.com"])
        self.assertEqual(cfg.mirror_dir, os.path.join(os.path.expanduser("~"), "work-contributions"))
        self.assertFalse(cfg.pr_reviews)
        self.assertEqual((cfg.ssh_key, cfg.gh_user, cfg.log_dir), ("", "", ""))

    def test_missing_required_setting_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w") as f:
                f.write("WORK_EMAIL=a@work.com\n")
            with mock.patch.dict(os.environ, {"WORK_EMAIL": "", "PERSONAL_NAME": "", "PERSONAL_EMAIL": "", "EXPECTED_REMOTE": ""}):
                with self.assertRaisesRegex(envfile.EnvFileError, "PERSONAL_NAME PERSONAL_EMAIL EXPECTED_REMOTE"):
                    config.load_config(path)


if __name__ == "__main__":
    unittest.main()
