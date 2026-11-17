import base64
import os
import unittest
from datetime import date
from unittest import mock

import _path  # noqa: F401
from _server import FakeHost
from contribution_mirror import providers
from contribution_mirror.httpapi import Api
from contribution_mirror.providers import PROVIDERS, Token

SINCE = date(2026, 1, 1)
b64 = lambda s: "Basic " + base64.b64encode(s.encode()).decode()


class Detection(unittest.TestCase):
    def setUp(self):
        providers._cache.clear()
        self.addCleanup(providers._cache.clear)

    def test_known_hosts(self):
        for scope, name in {"github.com": "github", "gitlab.com": "gitlab", "bitbucket.org": "bitbucket",
                            "dev.azure.com/acme": "azure", "acme.visualstudio.com": "azure",
                            "codeberg.org": "gitea"}.items():
            self.assertEqual(providers.provider_for(scope, use_probe=False).name, name, scope)

    def test_unknown_host_is_generic_commits_only(self):
        p = providers.provider_for("git.example.invalid", use_probe=False)
        self.assertEqual((p.name, p.has_activity), ("generic", False))

    def test_env_override_for_self_hosted(self):
        with mock.patch.dict(os.environ, {"GIT_HOST_PROVIDERS": "git.corp.com=gitlab, code.corp.com = Gitea,junk"}):
            self.assertEqual(providers.provider_for("git.corp.com", use_probe=False).name, "gitlab")
            self.assertEqual(providers.provider_for("code.corp.com", use_probe=False).name, "gitea")

    def test_probe_identifies_unknown_servers(self):
        with mock.patch.object(providers, "probe", return_value="gitlab") as probe:
            self.assertEqual(providers.provider_for("git.mystery.com").name, "gitlab")
            providers.provider_for("git.mystery.com")
            probe.assert_called_once()                    # cached


class HowATokenIsSent(unittest.TestCase):
    def test_api_headers(self):
        t = Token("x", "SECRET")
        self.assertEqual(PROVIDERS["github"].headers(t)["Authorization"], "Bearer SECRET")
        self.assertEqual(PROVIDERS["gitlab"].headers(t)["Authorization"], "Bearer SECRET")
        self.assertEqual(PROVIDERS["gitea"].headers(t)["Authorization"], "token SECRET")
        bb = PROVIDERS["bitbucket"]
        self.assertEqual(bb.headers(t)["Authorization"], "Bearer SECRET")                       # access token
        self.assertEqual(bb.headers(Token("x", "me:apppw"))["Authorization"], b64("me:apppw"))  # app password
        az = PROVIDERS["azure"]
        self.assertEqual(az.headers(t)["Authorization"], b64(":SECRET"))                        # PAT
        self.assertEqual(az.headers(Token("az", "AAD", kind="bearer"))["Authorization"], "Bearer AAD")

    def test_git_over_https_header_per_host(self):
        t = Token("x", "SECRET")
        self.assertEqual(PROVIDERS["github"].git_header(t), b64("x-access-token:SECRET"))
        self.assertEqual(PROVIDERS["gitlab"].git_header(t), b64("oauth2:SECRET"))
        self.assertEqual(PROVIDERS["gitea"].git_header(t), b64("token:SECRET"))
        self.assertEqual(PROVIDERS["bitbucket"].git_header(t), b64("x-token-auth:SECRET"))
        self.assertEqual(PROVIDERS["bitbucket"].git_header(Token("x", "me:apppw")), b64("me:apppw"))
        self.assertEqual(PROVIDERS["azure"].git_header(t), b64(":SECRET"))
        self.assertEqual(PROVIDERS["azure"].git_header(Token("az", "AAD", kind="bearer")), "Bearer AAD")

    def test_api_urls(self):
        self.assertEqual(PROVIDERS["github"].api_base("github.com"), "https://api.github.com")
        self.assertEqual(PROVIDERS["github"].api_base("ghe.corp.com"), "https://ghe.corp.com/api/v3")
        self.assertEqual(PROVIDERS["gitlab"].api_base("git.corp.com"), "https://git.corp.com/api/v4")
        self.assertEqual(PROVIDERS["gitea"].api_base("codeberg.org"), "https://codeberg.org/api/v1")
        self.assertEqual(PROVIDERS["azure"].api_base("dev.azure.com/acme"), "https://dev.azure.com/acme")
        with mock.patch.dict(os.environ, {"WORK_GITLAB_API_URL": "https://override/api/v4"}):
            self.assertEqual(PROVIDERS["gitlab"].api_url("gitlab.com"), "https://override/api/v4")


class AgainstAFakeHost(unittest.TestCase):
    """Each provider's queries, run against a local server that answers like the vendor's documented API."""

    def api(self, host, provider, secret="T", **kw):
        return Api(host.url, provider.headers(Token("t", secret, **kw)))

    def test_gitlab_merge_requests_and_approvals(self):
        p = PROVIDERS["gitlab"]
        with FakeHost() as h:
            h.routes["/user"] = (200, {"id": 7, "username": "me"})
            h.routes["/users/7/events"] = lambda q: (200, [{"created_at": "2026-03-01T10:00:00.123Z"}]
                                                     if q["action"] == "created" else [{"created_at": "2026-03-02T11:00:00.000Z"}] * 2)
            api = self.api(h, p)
            self.assertEqual(p.whoami(api), "me")
            prs, reviews = p.fetch(api, "me", [], SINCE)
            self.assertEqual((len(prs), len(reviews)), (1, 2))
            events = [r for r in h.requests if r["path"].endswith("/events")]
            self.assertEqual({e["query"]["target_type"] for e in events}, {"merge_request"})
            self.assertEqual(events[0]["query"]["after"], "2025-12-31")           # `after` is exclusive
            self.assertEqual(events[0]["auth"], "Bearer T")

    def test_bitbucket_pull_requests_stop_at_the_window(self):
        p = PROVIDERS["bitbucket"]
        with FakeHost() as h:
            h.routes["/2.0/user"] = (200, {"nickname": "me", "uuid": "{abc}"})
            h.routes["/2.0/pullrequests/%7Babc%7D"] = lambda q: (200, {
                "values": [{"created_on": "2026-05-01T00:00:00.000000+00:00"}, {"created_on": "2026-04-01T00:00:00+00:00"},
                           {"created_on": "2025-06-01T00:00:00+00:00"},          # older than the window: stop here
                           {"created_on": "2025-05-01T00:00:00+00:00"}]})
            api = self.api(h, p)
            self.assertEqual(p.whoami(api), "me")
            prs, reviews = p.fetch(api, "me", [], SINCE)
            self.assertEqual((len(prs), reviews), (2, []))
            self.assertEqual(h.requests[-1]["query"]["state"], ["OPEN", "MERGED", "DECLINED", "SUPERSEDED"])

    def test_bitbucket_follows_the_next_link(self):
        p = PROVIDERS["bitbucket"]
        with FakeHost() as h:
            h.routes["/2.0/user"] = (200, {"nickname": "me", "uuid": "{abc}"})
            h.routes["/2.0/pullrequests/%7Babc%7D"] = lambda q: (200, {
                "values": [{"created_on": "2026-05-01T00:00:00+00:00"}], "next": f"{h.url}/2.0/more?page=2"})
            h.routes["/2.0/more"] = (200, {"values": [{"created_on": "2026-04-01T00:00:00+00:00"}]})
            self.assertEqual(len(p.fetch(self.api(h, p), "me", [], SINCE)[0]), 2)

    def test_azure_pull_requests_per_listed_repo(self):
        p = PROVIDERS["azure"]
        with FakeHost() as h:
            h.routes["/_apis/connectionData"] = (200, {"authenticatedUser": {"id": "guid-1", "providerDisplayName": "Me"}})
            h.routes["/My%20Proj/_apis/git/repositories/web/pullrequests"] = (200, {"value": [
                {"creationDate": "2026-02-01T08:00:00.1234567Z"}, {"creationDate": "2024-01-01T08:00:00Z"}]})
            api = self.api(h, p, "PAT")
            self.assertEqual(p.whoami(api), "Me")
            prs, reviews = p.fetch(api, "Me", ["acme/My%20Proj/web"], SINCE)
            self.assertEqual((prs, reviews), (["2026-02-01T08:00:00.1234567Z"], []))     # the 2024 one is outside the window
            pr_req = h.requests[-1]
            self.assertEqual(pr_req["query"]["searchCriteria.creatorId"], "guid-1")
            self.assertEqual(pr_req["auth"], b64(":PAT"))

    def test_gitea_pull_requests_and_reviews(self):
        p = PROVIDERS["gitea"]
        with FakeHost() as h:
            h.routes["/user"] = (200, {"login": "me"})
            def search(q):
                if q.get("created_by") == "me":
                    return 200, [{"created_at": "2026-02-01T00:00:00Z"}]
                return 200, [{"number": 5, "user": {"login": "other"}, "repository": {"full_name": "o/r"}},
                             {"number": 6, "user": {"login": "me"}, "repository": {"full_name": "o/r"}}]   # my own PR: skipped
            h.routes["/repos/issues/search"] = search
            h.routes["/repos/o/r/pulls/5/reviews"] = (200, [
                {"user": {"login": "me"}, "submitted_at": "2026-03-03T00:00:00Z", "state": "APPROVED"},
                {"user": {"login": "me"}, "submitted_at": "2026-03-04T00:00:00Z", "state": "PENDING"},   # not submitted yet
                {"user": {"login": "x"}, "submitted_at": "2026-03-05T00:00:00Z", "state": "APPROVED"}])
            api = self.api(h, p)
            prs, reviews = p.fetch(api, "me", [], SINCE)
            self.assertEqual((prs, reviews), (["2026-02-01T00:00:00Z"], ["2026-03-03T00:00:00Z"]))
            self.assertEqual(h.requests[0]["auth"], "token T")

    def test_github_pull_requests_and_reviews(self):
        p = PROVIDERS["github"]
        with FakeHost() as h:
            h.routes["/user"] = (200, {"login": "me"})
            def search(q):
                if q["q"].startswith("author:me"):
                    return 200, {"total_count": 1, "items": [{"created_at": "2026-02-01T00:00:00Z"}]}
                return 200, {"total_count": 1, "items": [{"number": 9, "repository_url": f"{h.url}/repos/o/r"}]}
            h.routes["/search/issues"] = search
            h.routes["/repos/o/r/pulls/9/reviews"] = (200, [{"user": {"login": "me"}, "submitted_at": "2026-03-03T00:00:00Z"},
                                                            {"user": {"login": "x"}, "submitted_at": "2026-03-04T00:00:00Z"}])
            api = self.api(h, p)
            self.assertEqual(p.whoami(api), "me")
            self.assertEqual(p.fetch(api, "me", [], SINCE), (["2026-02-01T00:00:00Z"], ["2026-03-03T00:00:00Z"]))

    def test_github_score_counts_my_commits(self):
        p = PROVIDERS["github"]
        with FakeHost() as h:
            h.routes["/repos/o/mine/commits"] = (200, [{"sha": "1"}])
            h.routes["/repos/o/seen/commits"] = (200, [])
            api = self.api(h, p)
            self.assertEqual(p.score(api, "me", "o/mine"), (1, 1))
            self.assertEqual(p.score(api, "me", "o/seen"), (0, 1))
            self.assertEqual(p.score(api, "me", "o/hidden"), (0, 0))

    def test_scores_for_hosts_that_cannot_filter_commits_are_access_only(self):
        with FakeHost() as h:
            h.routes["/projects/g%2Fs%2Fr"] = (200, {})
            h.routes["/2.0/repositories/ws/slug"] = (200, {})
            h.routes["/repos/o/r"] = (200, {})
            h.routes["/Proj/_apis/git/repositories/web"] = (200, {})
            self.assertEqual(PROVIDERS["gitlab"].score(self.api(h, PROVIDERS["gitlab"]), "me", "g/s/r"), (0, 1))
            self.assertEqual(PROVIDERS["bitbucket"].score(self.api(h, PROVIDERS["bitbucket"]), "me", "ws/slug"), (0, 1))
            self.assertEqual(PROVIDERS["gitea"].score(self.api(h, PROVIDERS["gitea"]), "me", "o/r"), (0, 1))
            self.assertEqual(PROVIDERS["azure"].score(self.api(h, PROVIDERS["azure"], "P"), "me", "acme/Proj/web"), (0, 1))
            self.assertEqual(PROVIDERS["gitlab"].score(self.api(h, PROVIDERS["gitlab"]), "me", "g/hidden"), (0, 0))

    def test_a_bad_token_fails_whoami(self):
        with FakeHost() as h:
            h.routes["/user"] = (401, {"message": "bad token"})
            with self.assertRaises(Exception):
                PROVIDERS["github"].whoami(self.api(h, PROVIDERS["github"]))


if __name__ == "__main__":
    unittest.main()
