import os
import unittest
from unittest import mock

import _path  # noqa: F401
from contribution_mirror import credentials
from contribution_mirror.providers import GENERIC, PROVIDERS, Token

REPOS = {"github.com": ["Org/work-repo"]}
GITHUB = PROVIDERS["github"]


class FakeApi:
    """Stands in for GitHub: tokens map to (login, repos that login can see, repos with that login's commits)."""
    ACCOUNTS = {"tok-personal": ("me-personal", set(), set()),
                "tok-work": ("me-work", {"Org/work-repo"}, {"Org/work-repo"}),
                "tok-reader": ("someone", {"Org/work-repo"}, set())}   # can see the repo, has no commits there

    def __init__(self, base_url, headers=None):
        self.account = self.ACCOUNTS.get((headers or {}).get("Authorization", "").replace("Bearer ", ""))

    def get(self, path, **params):
        if self.account is None:
            raise RuntimeError("GitHub API 401")
        login, can_see, has_commits = self.account
        if path == "/user":
            return {"login": login}
        repo = path.split("/repos/")[1].rsplit("/commits", 1)[0]
        if repo not in can_see:
            raise RuntimeError("GitHub API 404")
        return [{"sha": "x"}] if repo in has_commits else []


def cands(*tokens):
    return mock.patch.object(credentials, "candidates", lambda provider, scope, gh_user: iter(tokens))


@mock.patch.object(credentials, "Api", FakeApi)
class ChoosingTheAccount(unittest.TestCase):
    def pick(self, repos=REPOS):
        return credentials.Creds(repos, None).pick("github.com")

    def test_account_with_my_commits_wins_even_if_listed_last(self):
        with cands(Token("gh:me-personal", "tok-personal"), Token("gh:someone", "tok-reader"), Token("gh:me-work", "tok-work")):
            p = self.pick()
        self.assertEqual((p.login, p.source, p.provider.name), ("me-work", "gh:me-work", "github"))

    def test_account_that_only_sees_the_repo_beats_one_that_sees_nothing(self):
        with cands(Token("a", "tok-personal"), Token("b", "tok-reader")):
            self.assertEqual(self.pick().login, "someone")

    def test_explicit_choice_is_used_without_scoring(self):
        with cands(Token("WORK_GITHUB_TOKEN", "tok-personal", explicit=True)):
            self.assertEqual(self.pick().login, "me-personal")

    def test_invalid_tokens_are_skipped(self):
        with cands(Token("env", "garbage"), Token("gh", "tok-work")):
            self.assertEqual(self.pick().login, "me-work")

    def test_nothing_usable_gives_none(self):
        with cands(Token("env", "garbage")):
            self.assertIsNone(self.pick())
        with cands(Token("gh", "tok-personal")):     # valid login, but cannot see any listed project
            self.assertIsNone(self.pick())

    def test_without_listed_repos_the_first_valid_login_is_used(self):
        with cands(Token("a", "garbage"), Token("b", "tok-personal")):
            self.assertEqual(self.pick({}).login, "me-personal")

    def test_result_is_cached_per_scope(self):
        creds = credentials.Creds(REPOS, None)
        with cands(Token("gh", "tok-work")):
            first = creds.pick("github.com")
        with cands(Token("gh", "tok-personal")):     # would change the answer if asked again
            self.assertIs(creds.pick("github.com"), first)

    def test_a_host_without_a_supported_api_gives_none(self):
        with mock.patch.object(credentials, "provider_for", lambda scope: GENERIC):
            self.assertIsNone(credentials.Creds({"git.example.com": ["a/b"]}, None).pick("git.example.com"))


class CandidateOrder(unittest.TestCase):
    ENV = {v: "" for v in ("WORK_TOKEN", "WORK_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "WORK_GITLAB_TOKEN", "GITLAB_TOKEN")}

    def run_candidates(self, provider, env, gh_user=None, cli=(), helper=None, scope="github.com"):
        with mock.patch.dict(os.environ, {**self.ENV, **env}), \
                mock.patch.object(type(provider), "cli_tokens", lambda self, s, only_user=None: iter(cli)), \
                mock.patch.object(credentials, "helper_token", lambda host: helper):
            return list(credentials.candidates(provider, scope, gh_user))

    def test_order_env_then_vendor_cli_then_credential_helper(self):
        got = self.run_candidates(GITHUB, {"GH_TOKEN": "e1", "GITHUB_TOKEN": "e2"}, cli=[Token("gh:a", "g1")],
                                  helper=Token("git-credential", "h1"))
        self.assertEqual([t.secret for t in got], ["e1", "e2", "g1", "h1"])
        self.assertFalse(any(t.explicit for t in got))

    def test_work_token_variables_are_explicit_and_exclusive(self):
        got = self.run_candidates(GITHUB, {"WORK_GITHUB_TOKEN": "w", "GH_TOKEN": "e1"}, cli=[Token("gh:a", "g1")])
        self.assertEqual(got, [Token("WORK_GITHUB_TOKEN", "w", True)])
        got = self.run_candidates(GITHUB, {"WORK_TOKEN": "any", "WORK_GITHUB_TOKEN": "w"})
        self.assertEqual(got, [Token("WORK_TOKEN", "any", True)])           # the generic one is checked first

    def test_each_provider_reads_its_own_variables(self):
        gitlab = PROVIDERS["gitlab"]
        got = self.run_candidates(gitlab, {"GITLAB_TOKEN": "gl", "GH_TOKEN": "gh"}, scope="gitlab.com")
        self.assertEqual([t.secret for t in got], ["gl"])                    # a GitHub token is never offered to GitLab
        self.assertEqual(self.run_candidates(gitlab, {"WORK_GITLAB_TOKEN": "w"}, scope="gitlab.com"),
                         [Token("WORK_GITLAB_TOKEN", "w", True)])

    def test_gh_user_restricts_to_that_account(self):
        seen = {}
        def fake_cli(self, scope, only_user=None):
            seen["only"] = only_user
            return iter([Token("gh:" + only_user, "g", explicit=True)])
        with mock.patch.dict(os.environ, self.ENV), mock.patch.object(type(GITHUB), "cli_tokens", fake_cli), \
                mock.patch.object(credentials, "helper_token", lambda h: Token("never", "x")):
            got = list(credentials.candidates(GITHUB, "github.com", "me-work"))
        self.assertEqual((seen["only"], got), ("me-work", [Token("gh:me-work", "g", True)]))

    def test_explicit_choice_detection(self):
        with mock.patch.dict(os.environ, self.ENV):
            self.assertFalse(credentials.explicit_choice())
            self.assertTrue(credentials.explicit_choice("someone"))
        with mock.patch.dict(os.environ, {**self.ENV, "WORK_GITLAB_TOKEN": "x"}):
            self.assertTrue(credentials.explicit_choice())


if __name__ == "__main__":
    unittest.main()
