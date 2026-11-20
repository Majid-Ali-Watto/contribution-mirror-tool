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


class OnlyTheListedWork(AgainstAFakeHost):
    """PRs and reviews outside the owners (or GitLab projects) of the listed repos are not mirrored."""

    def test_github_keeps_only_the_listed_owners(self):
        p = PROVIDERS["github"]
        with FakeHost() as h:
            def search(q):
                if q["q"].startswith("author:me"):
                    return 200, {"total_count": 2, "items": [
                        {"created_at": "2026-02-01T00:00:00Z", "repository_url": f"{h.url}/repos/Acme/web"},
                        {"created_at": "2026-02-02T00:00:00Z", "repository_url": f"{h.url}/repos/me/side-project"}]}
                return 200, {"total_count": 2, "items": [{"number": 1, "repository_url": f"{h.url}/repos/acme/api"},
                                                         {"number": 2, "repository_url": f"{h.url}/repos/oss/lib"}]}
            h.routes["/search/issues"] = search
            h.routes["/repos/acme/api/pulls/1/reviews"] = (200, [{"user": {"login": "me"}, "submitted_at": "2026-03-03T00:00:00Z"}])
            h.routes["/repos/oss/lib/pulls/2/reviews"] = (200, [{"user": {"login": "me"}, "submitted_at": "2026-03-04T00:00:00Z"}])
            got = p.fetch(self.api(h, p), "me", ["acme/web"], SINCE)
            self.assertEqual(got, (["2026-02-01T00:00:00Z"], ["2026-03-03T00:00:00Z"]))
            self.assertFalse(any("oss/lib" in r["path"] for r in h.requests))       # not even looked up

    def test_issues_opened_on_each_host_that_lists_them(self):
        with FakeHost() as h:                                                # GitHub: search, listed owners only
            h.routes["/search/issues"] = lambda q: (200, {"total_count": 2, "items": [
                {"created_at": "2026-02-05T00:00:00Z", "repository_url": f"{h.url}/repos/acme/web"},
                {"created_at": "2026-02-06T00:00:00Z", "repository_url": f"{h.url}/repos/oss/lib"}]}) \
                if q["q"].startswith("author:me type:issue") else (500, {})
            p = PROVIDERS["github"]
            self.assertEqual(p.fetch_issues(self.api(h, p), "me", ["acme/web"], SINCE), ["2026-02-05T00:00:00Z"])
        with FakeHost() as h:                                                # GitLab: issue events in listed projects
            h.routes["/user"] = (200, {"id": 7})
            h.routes["/projects/acme%2Fweb"] = (200, {"id": 1})
            h.routes["/users/7/events"] = lambda q: (200, [{"created_at": "2026-02-07T00:00:00Z", "project_id": 1},
                                                           {"created_at": "2026-02-08T00:00:00Z", "project_id": 2}]) \
                if (q["action"], q["target_type"]) == ("created", "issue") else (200, [])
            p = PROVIDERS["gitlab"]
            self.assertEqual(p.fetch_issues(self.api(h, p), "me", ["acme/web"], SINCE), ["2026-02-07T00:00:00Z"])
        with FakeHost() as h:                                                # Gitea: issue search by creator
            h.routes["/repos/issues/search"] = lambda q: (200, [
                {"created_at": "2026-02-09T00:00:00Z", "repository": {"full_name": "acme/a"}}]) \
                if (q.get("type"), q.get("created_by")) == ("issues", "me") else (200, [])
            p = PROVIDERS["gitea"]
            self.assertEqual(p.fetch_issues(self.api(h, p), "me", ["acme/a"], SINCE), ["2026-02-09T00:00:00Z"])
        for name in ("bitbucket", "azure"):                                  # no issues to read there
            self.assertEqual((PROVIDERS[name].issues, PROVIDERS[name].fetch_issues(None, "me", [], SINCE)), (False, []))

    def test_gitea_reviews_use_the_update_time_and_listed_owners(self):
        p = PROVIDERS["gitea"]
        with FakeHost() as h:
            def search(q):
                if q.get("created_by") == "me":
                    return 200, [{"created_at": "2026-02-01T00:00:00Z", "repository": {"full_name": "acme/a"}},
                                 {"created_at": "2026-02-02T00:00:00Z", "repository": {"full_name": "other/b"}}]
                return 200, [{"number": 5, "user": {"login": "x"}, "repository": {"full_name": "acme/a"}}]
            h.routes["/repos/issues/search"] = search
            h.routes["/repos/acme/a/pulls/5/reviews"] = (200, [{"user": {"login": "me"}, "submitted_at": "2026-03-03T00:00:00Z",
                                                                 "state": "APPROVED"}])
            prs, reviews = p.fetch(self.api(h, p), "me", ["acme/x"], SINCE)
            self.assertEqual((prs, reviews), (["2026-02-01T00:00:00Z"], ["2026-03-03T00:00:00Z"]))
            review_search = [r for r in h.requests if r["query"].get("reviewed") == "true"][0]["query"]
            # a PR opened before the window but reviewed inside it must be found: filter on update time, not creation
            self.assertEqual(review_search.get("since"), "2026-01-01T00:00:00Z")
            self.assertNotIn("created_after", review_search)

    def test_bitbucket_keeps_only_the_listed_workspaces(self):
        p = PROVIDERS["bitbucket"]
        with FakeHost() as h:
            h.routes["/2.0/user"] = (200, {"nickname": "me", "uuid": "{abc}"})
            h.routes["/2.0/pullrequests/%7Babc%7D"] = (200, {"values": [
                {"created_on": "2026-05-01T00:00:00+00:00", "destination": {"repository": {"full_name": "acme/app"}}},
                {"created_on": "2026-04-01T00:00:00+00:00", "destination": {"repository": {"full_name": "me/hobby"}}}]})
            self.assertEqual(p.fetch(self.api(h, p), "me", ["Acme/mobile"], SINCE), (["2026-05-01T00:00:00+00:00"], []))

    def test_gitlab_keeps_only_the_listed_projects_and_never_drops_on_a_failed_lookup(self):
        p = PROVIDERS["gitlab"]
        with FakeHost() as h:
            h.routes["/user"] = (200, {"id": 7, "username": "me"})
            h.routes["/projects/g%2Fp"] = (200, {"id": 11})
            h.routes["/users/7/events"] = lambda q: (200, [{"created_at": "2026-03-01T10:00:00Z", "project_id": 11},
                                                            {"created_at": "2026-03-02T10:00:00Z", "project_id": 99}]
                                                     if q["action"] == "created" else [])
            self.assertEqual(len(p.fetch(self.api(h, p), "me", ["g/p"], SINCE)[0]), 1)
            with mock.patch("sys.stderr"):
                self.assertEqual(len(p.fetch(self.api(h, p), "me", ["g/p", "g/unknown"], SINCE)[0]), 2)

    def test_azure_asks_the_server_for_the_window_only(self):
        p = PROVIDERS["azure"]
        with FakeHost() as h:
            h.routes["/_apis/connectionData"] = (200, {"authenticatedUser": {"id": "g1", "providerDisplayName": "Me"}})
            h.routes["/P/_apis/git/repositories/web/pullrequests"] = (200, {"value": []})
            p.fetch(self.api(h, p, "PAT"), "Me", ["acme/P/web"], SINCE)
            q = h.requests[-1]["query"]
            self.assertEqual((q["searchCriteria.minTime"], q["searchCriteria.queryTimeRangeType"]),
                             ("2026-01-01T00:00:00Z", "created"))


class ProbeMemory(unittest.TestCase):
    def setUp(self):
        for d in (providers._cache, providers._learned, providers._probed):
            d.clear()
            self.addCleanup(d.clear)

    def test_a_definite_answer_is_remembered_for_the_next_run(self):
        with mock.patch.object(providers, "probe", return_value="gitea"):
            providers.provider_for("code.corp.example")
        self.assertEqual(providers.probed(), {"code.corp.example": "gitea"})
        providers._cache.clear()
        providers._learned.clear()
        providers.remember({"code.corp.example": "gitea"})                   # what the next run loads
        with mock.patch.object(providers, "probe") as probe:
            self.assertEqual(providers.provider_for("code.corp.example").name, "gitea")
            probe.assert_not_called()

    def test_a_lookup_without_probing_does_not_stop_a_later_probe(self):
        with mock.patch.object(providers, "probe", return_value="gitea") as probe:
            self.assertEqual(providers.provider_for("code.corp.example", use_probe=False).name, "generic")
            probe.assert_not_called()
            self.assertEqual(providers.provider_for("code.corp.example").name, "gitea")

    def test_reset_forgets_this_process_lookups(self):
        with mock.patch.object(providers, "probe", return_value="gitea"):
            providers.provider_for("code.corp.example")
        providers.reset()
        self.assertEqual((providers._cache, providers._learned, providers.probed()), ({}, {}, {}))

    def test_every_work_token_variable_is_kept_from_child_programs(self):
        from contribution_mirror import gitutil
        self.assertEqual(providers.WORK_TOKEN_VARS[0], "WORK_TOKEN")
        for name in providers.WORK_TOKEN_VARS:
            self.assertRegex(name, gitutil._SECRET_VARS)
        for p in PROVIDERS.values():
            self.assertTrue(set(p.work_envs) <= set(providers.WORK_TOKEN_VARS), p.name)

    def test_offline_is_not_remembered(self):
        with mock.patch.object(providers, "probe", return_value=None):
            self.assertEqual(providers.provider_for("code.corp.example").name, "generic")
        self.assertEqual(providers.probed(), {})

    def test_only_hosts_named_by_a_person_are_trusted_with_tokens(self):
        with mock.patch.object(providers, "probe", return_value="gitlab"), \
                mock.patch.dict(os.environ, {"GIT_HOST_PROVIDERS": "git.corp.example=gitlab", "WORK_GITLAB_API_URL": ""}):
            self.assertTrue(providers.trusted("gitlab.com"))
            self.assertTrue(providers.trusted("git.corp.example"))
            self.assertFalse(providers.trusted("claims-to-be-gitlab.example"))
            os.environ["WORK_GITLAB_API_URL"] = "https://claims-to-be-gitlab.example/api/v4"
            self.assertTrue(providers.trusted("claims-to-be-gitlab.example"))   # the user chose that API address


if __name__ == "__main__":
    unittest.main()
