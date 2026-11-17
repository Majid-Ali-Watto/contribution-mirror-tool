import unittest
from unittest import mock
import _path  # noqa: F401
from contribution_mirror import remotes


class RemoteUrls(unittest.TestCase):
    def test_is_url_tells_urls_from_paths(self):
        for u in ["git@github.com:o/r.git", "https://github.com/o/r", "ssh://git@h/o/r", "git@github-work:Org/r.git"]:
            self.assertTrue(remotes.is_url(u), u)
        for p in ["/home/u/repo", "~/code/app", "../x", "relative/dir", "C:/x"]:
            self.assertFalse(remotes.is_url(p), p)

    def test_same_repo_any_spelling(self):
        spellings = ["git@github-work:Acme-Corp/Repo.git", "git@github.com:acme-corp/repo", "ssh://git@github.com:22/Acme-Corp/repo.git",
                     "https://github.com/Acme-Corp/repo.git", "https://user:tok@github.com/Acme-Corp/repo/"]
        self.assertEqual({remotes.repo_key(u) for u in spellings}, {"acme-corp/repo"})

    def test_repo_path_keeps_case_and_nested_groups(self):
        self.assertEqual(remotes.repo_path("git@github.com:Org/Repo.git"), "Org/Repo")
        self.assertEqual(remotes.repo_path("git@gitlab.com:grp/sub/proj.git"), "grp/sub/proj")

    def test_url_host(self):
        self.assertEqual(remotes.url_host("ssh://git@ghe.corp.com:2222/o/r"), "ghe.corp.com")
        self.assertEqual(remotes.url_host("git@github-work:o/r"), "github-work")
        self.assertEqual(remotes.url_host("https://GitHub.com/o/r"), "github.com")

    def test_real_host_resolves_aliases_and_strips_ssh_prefix(self):
        remotes._hosts.clear()
        remotes._hosts["github-work"] = "github.com"      # what `ssh -G` would answer, cached
        self.assertEqual(remotes.real_host("git@github-work:Org/r.git"), "github.com")
        self.assertEqual(remotes.https_url("git@github-work:Org/Repo.git"), "https://github.com/Org/Repo.git")
        remotes._hosts.clear()


class NoSshForHttps(unittest.TestCase):
    def test_https_urls_never_run_ssh_and_aliases_cannot_be_options(self):
        remotes._hosts.clear()
        self.addCleanup(remotes._hosts.clear)
        with mock.patch.object(remotes.subprocess, "run") as run:
            self.assertEqual(remotes.real_host("https://git.corp.example/o/r.git"), "git.corp.example")
            run.assert_not_called()
        with mock.patch.object(remotes.subprocess, "run", return_value=mock.Mock(stdout="hostname gitlab.com\n")) as run:
            remotes.real_host("git@my-alias:o/r.git")
        self.assertEqual(run.call_args[0][0], ["ssh", "-G", "--", "my-alias"])


if __name__ == "__main__":
    unittest.main()


class OtherHosts(unittest.TestCase):
    def test_azure_devops_https_and_ssh_are_the_same_repo(self):
        forms = ["https://acme@dev.azure.com/acme/My%20Project/_git/web", "https://dev.azure.com/acme/My%20Project/_git/web",
                 "git@ssh.dev.azure.com:v3/acme/My%20Project/web", "https://acme.visualstudio.com/My%20Project/_git/web"]
        self.assertEqual({remotes.repo_path(u) for u in forms}, {"acme/My%20Project/web"})

    def test_gitlab_subgroups_keep_every_level(self):
        self.assertEqual(remotes.repo_path("git@gitlab.com:group/sub/deep/proj.git"), "group/sub/deep/proj")
        self.assertEqual(remotes.repo_path("https://gitlab.com/group/sub/proj"), "group/sub/proj")

    def test_bitbucket_and_gitea_forms(self):
        self.assertEqual(remotes.repo_key("git@bitbucket.org:Team/Repo.git"), "team/repo")
        self.assertEqual(remotes.repo_key("https://user@bitbucket.org/Team/Repo.git"), "team/repo")
        self.assertEqual(remotes.repo_key("ssh://git@codeberg.org/Me/App.git"), "me/app")

    def test_ssh_front_doors_map_to_the_main_host(self):
        remotes._hosts.clear()
        self.addCleanup(remotes._hosts.clear)
        remotes._hosts.update({"alias-gl": "gitlab.com"})
        for ssh_host, main in {"ssh.github.com": "github.com", "altssh.gitlab.com": "gitlab.com",
                               "altssh.bitbucket.org": "bitbucket.org", "ssh.dev.azure.com": "dev.azure.com",
                               "vs-ssh.visualstudio.com": "dev.azure.com"}.items():
            with mock.patch.object(remotes.subprocess, "run", return_value=mock.Mock(stdout=f"hostname {ssh_host}\n")):
                remotes._hosts.pop("x", None)
                self.assertEqual(remotes.real_host(f"git@x:o/r.git"), main, ssh_host)

    def test_scope_is_the_host_or_the_azure_organisation(self):
        remotes._hosts.clear()
        self.addCleanup(remotes._hosts.clear)
        remotes._hosts.update({"github.com": "github.com", "gitlab.com": "gitlab.com", "dev.azure.com": "dev.azure.com",
                               "ssh.dev.azure.com": "dev.azure.com"})
        self.assertEqual(remotes.scope_of("git@github.com:o/r.git"), "github.com")
        self.assertEqual(remotes.scope_of("https://gitlab.com/g/s/r"), "gitlab.com")
        self.assertEqual(remotes.scope_of("git@ssh.dev.azure.com:v3/acme/proj/repo"), "dev.azure.com/acme")
        self.assertEqual(remotes.scope_of("https://dev.azure.com/other/proj/_git/repo"), "dev.azure.com/other")

    def test_https_url_for_each_host(self):
        remotes._hosts.clear()
        self.addCleanup(remotes._hosts.clear)
        remotes._hosts.update({"gitlab.com": "gitlab.com", "ssh.dev.azure.com": "dev.azure.com", "dev.azure.com": "dev.azure.com"})
        self.assertEqual(remotes.https_url("git@gitlab.com:g/s/r.git"), "https://gitlab.com/g/s/r.git")
        self.assertEqual(remotes.https_url("git@ssh.dev.azure.com:v3/acme/proj/repo"),
                         "https://dev.azure.com/acme/proj/_git/repo")


if __name__ == "__main__":
    unittest.main()
