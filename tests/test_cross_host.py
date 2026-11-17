"""Any source host -> any mirror host. The two halves share nothing but the dates, so each is tested for every host,
and then combined end to end (fake vendor APIs for the source side, a local bare repo as the mirror)."""
import base64
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import _path
from _server import FakeHost
from contribution_mirror import cli, providers, push, remotes
from contribution_mirror.config import Config
from contribution_mirror.providers import PROVIDERS, Token

HOSTS = {"github.com": "github.com", "gitlab.com": "gitlab.com", "bitbucket.org": "bitbucket.org", "codeberg.org": "codeberg.org",
         "dev.azure.com": "dev.azure.com", "ssh.dev.azure.com": "dev.azure.com"}
TOKEN_VARS = ("WORK_TOKEN", "WORK_GITHUB_TOKEN", "WORK_GITLAB_TOKEN", "WORK_BITBUCKET_TOKEN", "WORK_AZURE_DEVOPS_TOKEN",
              "WORK_GITEA_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "BITBUCKET_TOKEN",
              "AZURE_DEVOPS_EXT_PAT", "GITEA_TOKEN", "FORGEJO_TOKEN", "WORK_GH_USER", "GIT_HOST_PROVIDERS",
              "WORK_GITHUB_API_URL", "WORK_GITLAB_API_URL", "WORK_BITBUCKET_API_URL", "WORK_AZURE_DEVOPS_API_URL", "WORK_GITEA_API_URL")


def git(*args, cwd=None):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True, cwd=cwd, env=_path.GIT_ENV).stdout.strip()


class Isolated(unittest.TestCase):
    """No real host, token or ssh config may influence a test."""
    def setUp(self):
        env = mock.patch.dict(os.environ, _path.GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        for k in TOKEN_VARS:
            os.environ.pop(k, None)
        remotes._hosts.clear()
        remotes._hosts.update(HOSTS)
        providers._cache.clear()
        self.addCleanup(remotes._hosts.clear)
        self.addCleanup(providers._cache.clear)


# name: (mirror URL as configured, origin spelled another way, https push URL, git auth user:secret)
MIRRORS = {
    "github":    ("git@github.com:me/mirror.git", "https://github.com/me/mirror", "https://github.com/me/mirror.git", "x-access-token:SECRET"),
    "gitlab":    ("git@gitlab.com:me/team/mirror.git", "https://gitlab.com/me/team/mirror", "https://gitlab.com/me/team/mirror.git", "oauth2:SECRET"),
    "bitbucket": ("git@bitbucket.org:me/mirror.git", "https://me@bitbucket.org/me/mirror.git", "https://bitbucket.org/me/mirror.git", "x-token-auth:SECRET"),
    "gitea":     ("git@codeberg.org:me/mirror.git", "https://codeberg.org/me/mirror.git", "https://codeberg.org/me/mirror.git", "token:SECRET"),
    "azure":     ("git@ssh.dev.azure.com:v3/acme/Proj/mirror", "https://acme@dev.azure.com/acme/Proj/_git/mirror",
                  "https://dev.azure.com/acme/Proj/_git/mirror", ":SECRET"),
}


class MirrorOnAnyHost(Isolated):
    def cfg(self, url):
        return Config([], "Me", "m@p.com", url, "/m")

    def test_the_safety_check_accepts_any_spelling_of_the_right_repo_and_refuses_another(self):
        for name, (expected, origin, _, _) in MIRRORS.items():
            def fake_git(mirror, *args, env=None, origin=origin):
                if args[:2] == ("remote", "get-url"):
                    return 0, origin, ""
                return 0, "same-sha", ""                       # local main == remote-tracking ref: nothing to push
            with self.subTest(host=name), mock.patch.object(push, "_git", fake_git):
                self.assertEqual(push.push_mirror(self.cfg(expected)), "nothing to push")
            def other_git(mirror, *args, env=None, origin=origin):
                return (0, origin.replace("mirror", "someone-elses"), "") if args[:2] == ("remote", "get-url") else (0, "x", "")
            with self.subTest(host=name, refused=True), mock.patch.object(push, "_git", other_git):
                with self.assertRaisesRegex(push.PushError, "refusing to push"):
                    push.push_mirror(self.cfg(expected))

    def test_the_https_token_fallback_uses_each_hosts_url_and_token_shape(self):
        for name, (expected, _, https, user_secret) in MIRRORS.items():
            seen = {}
            def fake_run_git(args, env, timeout, seen=seen):
                seen.update(args=args, env=env)
            provider = PROVIDERS[name]
            host = push.real_host(expected)
            with self.subTest(host=name), mock.patch.object(type(provider), "cli_tokens", lambda s, scope, only_user=None: iter([Token("cli", "SECRET")])), \
                    mock.patch.object(push, "run_git", fake_run_git), mock.patch.object(push, "_git", return_value=(0, "", "")), \
                    mock.patch.object(push, "helper_token", lambda h: None), \
                    mock.patch.object(push, "_token_owns", lambda *a: True):
                self.assertIsNone(push._push_with_token(self.cfg(expected)))
            self.assertIn(https, seen["args"])
            self.assertEqual(seen["args"][-1], "main:main")
            self.assertEqual(seen["env"]["GIT_CONFIG_KEY_0"], f"http.https://{providers.host_of(host)}/.extraheader")
            value = seen["env"]["GIT_CONFIG_VALUE_0"]
            self.assertTrue(value.startswith("Authorization: Basic "), value)
            self.assertEqual(base64.b64decode(value.split("Basic ", 1)[1]).decode(), user_secret)
            self.assertNotIn("SECRET", " ".join(seen["args"]))                       # never on the command line

    def test_heads_up_for_hosts_other_than_github_only(self):
        self.assertIsNone(push.mirror_host_note(MIRRORS["github"][0]))
        for name in ("gitlab", "bitbucket", "gitea", "azure"):
            note = push.mirror_host_note(MIRRORS[name][0])
            self.assertIn(PROVIDERS[name].label, note, name)
            self.assertIn("Only GitHub is verified", note)
        self.assertIsNone(push.mirror_host_note("git@git.unknown.example:me/m.git"))       # unknown host: no claim either way
        os.environ["GIT_HOST_PROVIDERS"] = "ghe.corp.com=github"
        providers._cache.clear()
        self.assertIsNone(push.mirror_host_note("git@ghe.corp.com:me/m.git"))               # GitHub Enterprise counts as GitHub


def days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


# Fake servers must answer with FIXED timestamps: a value computed per request would differ by a second between two
# runs, and the same pull request would then (correctly) look like a new one.
def github_routes(h):
    created, reviewed = days_ago(10), days_ago(9)
    h.routes["/user"] = (200, {"login": "me"})
    h.routes["/search/issues"] = lambda q: (200, {"total_count": 1, "items": [{"created_at": created}]}) if q["q"].startswith("author:me") \
        else (200, {"total_count": 1, "items": [{"number": 9, "repository_url": f"{h.url}/repos/acme/web"}]})
    h.routes["/repos/acme/web/pulls/9/reviews"] = (200, [{"user": {"login": "me"}, "submitted_at": reviewed}])
    return 1, 1


def gitlab_routes(h):
    created, approved = days_ago(10), days_ago(8)
    h.routes["/user"] = (200, {"id": 7, "username": "me"})
    h.routes["/users/7/events"] = lambda q: (200, [{"created_at": created}] if q["action"] == "created" else [{"created_at": approved}] * 2)
    return 1, 2


def gitea_routes(h):
    created, reviewed = days_ago(10), days_ago(9)
    h.routes["/user"] = (200, {"login": "me"})
    h.routes["/repos/issues/search"] = lambda q: (200, [{"created_at": created}]) if q.get("created_by") == "me" \
        else (200, [{"number": 5, "user": {"login": "x"}, "repository": {"full_name": "acme/tool"}}])
    h.routes["/repos/acme/tool/pulls/5/reviews"] = (200, [{"user": {"login": "me"}, "submitted_at": reviewed, "state": "APPROVED"}])
    return 1, 1


def bitbucket_routes(h):
    first, second = days_ago(10), days_ago(20)
    h.routes["/2.0/user"] = (200, {"nickname": "me", "uuid": "{abc}"})
    h.routes["/2.0/pullrequests/%7Babc%7D"] = (200, {"values": [{"created_on": first}, {"created_on": second}]})
    return 2, 0


def azure_routes(h):
    created = days_ago(10).replace("Z", ".1234567Z")
    h.routes["/_apis/connectionData"] = (200, {"authenticatedUser": {"id": "g1", "providerDisplayName": "Me"}})
    h.routes["/Proj/_apis/git/repositories/web/pullrequests"] = (200, {"value": [{"creationDate": created}]})
    return 1, 0


# source host: (origin URL of the work repo, env var that names the API, env var for the token, fake routes)
SOURCES = {
    "github":    ("git@github.com:acme/web.git", "WORK_GITHUB_API_URL", "WORK_GITHUB_TOKEN", github_routes),
    "gitlab":    ("https://gitlab.com/acme/platform/api.git", "WORK_GITLAB_API_URL", "WORK_GITLAB_TOKEN", gitlab_routes),
    "gitea":     ("https://codeberg.org/acme/tool.git", "WORK_GITEA_API_URL", "WORK_GITEA_TOKEN", gitea_routes),
    "bitbucket": ("git@bitbucket.org:acme/mobile.git", "WORK_BITBUCKET_API_URL", "WORK_BITBUCKET_TOKEN", bitbucket_routes),
    "azure":     ("https://dev.azure.com/acme/Proj/_git/web", "WORK_AZURE_DEVOPS_API_URL", "WORK_AZURE_DEVOPS_TOKEN", azure_routes),
}


class AnySourceToAnyMirror(Isolated):
    """A work repo hosted on <source>, mirrored (commits + that host's pull requests/reviews) into a bare repo that
    stands in for the mirror on any host."""
    def run_one(self, source):
        origin, api_env, token_env, routes = SOURCES[source]
        with tempfile.TemporaryDirectory() as t, FakeHost() as host:
            expected_api = routes(host)
            os.environ[api_env], os.environ[token_env] = host.url, "SECRET"
            work = _path.make_repo(os.path.join(t, "work"), [("me@work.com", days_ago(30)), ("me@work.com", days_ago(29))])
            git("remote", "add", "origin", origin, cwd=work)
            bare = os.path.join(t, "mirror-on-any-host.git")
            git("init", "-q", "--bare", "-b", "main", bare)
            cfg = Config(["me@work.com"], "Me", "me@personal.com", bare, os.path.join(t, "mirror"))
            args = ["--work-email", "me@work.com", "--mirror", cfg.mirror_dir, "--name", "Me", "--email", "me@personal.com",
                    "--src", work, "--no-fetch", "--apply", "--log-dir", os.path.join(t, "logs")]
            with contextlib.redirect_stdout(io.StringIO()) as out:
                first = cli.run(args)
                outcome = push.push_mirror(cfg)
                again = cli.run(args)
                outcome2 = push.push_mirror(cfg)
            return {"expected": 2 + sum(expected_api), "first": first.created, "again": again.created, "pushed": outcome,
                    "nothing": outcome2, "remote": int(git("rev-list", "--count", "main", cwd=bare)), "out": out.getvalue()}

    def test_each_source_host_reaches_the_mirror_and_reruns_add_nothing(self):
        for source in SOURCES:
            with self.subTest(source=source):
                r = self.run_one(source)
                self.assertEqual(r["first"], r["expected"], r["out"])
                self.assertEqual((r["pushed"], r["remote"]), ("pushed", r["expected"]))
                self.assertEqual((r["again"], r["nothing"]), (0, "nothing to push"))

    def test_the_account_used_is_reported_per_host(self):
        for source, label in {"gitlab": "GitLab", "azure": "Azure DevOps", "bitbucket": "Bitbucket Cloud"}.items():
            with self.subTest(source=source):
                self.assertIn(f"({label}) as", self.run_one(source)["out"])

    def test_hosts_without_review_data_say_so(self):
        self.assertIn("[pull requests only]", self.run_one("bitbucket")["out"])
        self.assertNotIn("[pull requests only]", self.run_one("gitlab")["out"])


if __name__ == "__main__":
    unittest.main()
