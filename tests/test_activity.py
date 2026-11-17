import contextlib
import io
import os
import tempfile
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest import mock

import _path  # noqa: F401
from contribution_mirror import activity


def pick(scope, login, fetch, label="Fake", reviews=True):
    provider = SimpleNamespace(label=label, reviews=reviews, fetch=fetch)
    return SimpleNamespace(login=login, provider=provider, source="test", scope=scope, api=lambda: "api")


class Activity(unittest.TestCase):
    def run_activity(self, picks, repos, explicit=False):
        creds = SimpleNamespace(pick=lambda s: picks.get(s))
        out = io.StringIO()
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = activity.fetch_activity(creds, repos, {}, True, True, explicit)
        return result, out.getvalue(), err.getvalue()

    def test_timestamps_are_normalised_and_accounts_recorded(self):
        fetch = lambda api, login, repos, since: (["2026-01-01T10:00:00.1234567+05:00"], ["2026-01-02T00:00:00Z"] * 2)
        (items, who, _), out, _ = self.run_activity({"github.com": pick("github.com", "me", fetch)}, {"github.com": ["o/r"]})
        self.assertEqual(dict(items), {"github-api:pull-requests": Counter({"2026-01-01T05:00:00Z": 1}),
                                       "github-api:reviews": Counter({"2026-01-02T00:00:00Z": 2})})
        self.assertEqual(who, {"github.com": ("me", "test")})
        self.assertIn("github.com (Fake) as me via test", out)

    def test_two_hosts_are_combined(self):
        f1 = lambda api, login, repos, since: (["2026-01-01T00:00:00Z"], [])
        f2 = lambda api, login, repos, since: (["2026-02-01T00:00:00Z"], ["2026-02-02T00:00:00Z"])
        picks = {"github.com": pick("github.com", "me", f1), "gitlab.com": pick("gitlab.com", "me2", f2)}
        (items, who, _), out, _ = self.run_activity(picks, {"github.com": ["a/b"], "gitlab.com": ["g/p"]})
        self.assertEqual(sum(dict(items)["github-api:pull-requests"].values()), 2)
        self.assertEqual(who, {"github.com": ("me", "test"), "gitlab.com": ("me2", "test")})

    def test_hosts_without_review_data_say_so(self):
        fetch = lambda api, login, repos, since: (["2026-01-01T00:00:00Z"], [])
        _, out, _ = self.run_activity({"bitbucket.org": pick("bitbucket.org", "me", fetch, "Bitbucket Cloud", reviews=False)},
                                      {"bitbucket.org": ["w/r"]})
        self.assertIn("[pull requests only]", out)

    def test_repos_of_each_scope_are_passed_to_its_provider(self):
        got = {}
        def fetch(api, login, repos, since):
            got["repos"] = repos
            return [], []
        self.run_activity({"dev.azure.com/acme": pick("dev.azure.com/acme", "me", fetch)}, {"dev.azure.com/acme": ["acme/p/r"]})
        self.assertEqual(got["repos"], ["acme/p/r"])

    def test_no_login_is_a_quiet_skip_unless_the_account_was_forced(self):
        (items, who, _), out, err = self.run_activity({}, {"github.com": ["o/r"]})
        self.assertEqual((items, who, "skipped" in out, err), ([], {}, True, ""))
        (_, who, _), _, err = self.run_activity({}, {"github.com": ["o/r"]}, explicit=True)
        self.assertEqual(who, {})
        self.assertIn("warning", err)

    def test_an_api_failure_is_a_warning_and_commits_still_go_on(self):
        def boom(api, login, repos, since):
            raise RuntimeError("API 500 for /x")
        (items, who, _), _, err = self.run_activity({"github.com": pick("github.com", "me", boom)}, {"github.com": ["o/r"]})
        self.assertEqual((items, who), ([], {}))
        self.assertIn("PR/review fetch for github.com failed", err)

    def test_one_host_failing_does_not_drop_the_others(self):
        def boom(api, login, repos, since):
            raise RuntimeError("API 500 for /x")
        ok = lambda api, login, repos, since: (["2026-01-01T00:00:00Z"], [])
        picks = {"gitlab.com": pick("gitlab.com", "me", boom), "github.com": pick("github.com", "me", ok)}
        (items, done, _), _, err = self.run_activity(picks, {"gitlab.com": ["g/p"], "github.com": ["o/r"]})
        self.assertEqual(sum(dict(items)["github-api:pull-requests"].values()), 1)
        self.assertEqual(done, {"github.com": ("me", "test")})          # the failed host is re-checked in full next time
        self.assertIn("gitlab.com failed", err)

    def test_the_saved_window_is_per_host(self):
        got = {}
        def fetch_for(scope):
            def fetch(api, login, repos, since):
                got[scope] = since
                return [], []
            return fetch
        st = {"version": 2, "scopes": {"github.com": {"account": "me", "last_success": "2026-06-10T00:00:00+00:00"}}}
        creds = SimpleNamespace(pick=lambda s: pick(s, "me", fetch_for(s)))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            activity.fetch_activity(creds, {"github.com": ["o/r"], "gitlab.com": ["g/p"]}, st, True, False, False)
        self.assertGreater(got["github.com"], got["gitlab.com"])        # incremental for one, full for the new host
        self.assertIn("gitlab.com (Fake) as me via test, full (no saved state)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
