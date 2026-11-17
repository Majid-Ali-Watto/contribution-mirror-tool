# Supported git hosts

Your **work repos can live on any git host**. Reading commits is plain `git`, so it works everywhere: GitHub, GitLab,
Bitbucket, Azure DevOps, Gitea/Forgejo, self-hosted, anything that speaks git. What differs per host is the optional
extras: the https token fallback, finding your account automatically, and pull request / review timestamps.

| Host | Commits | Pull / merge requests you opened | Reviews | Status |
| --- | --- | --- | --- | --- |
| **GitHub** (github.com, Enterprise Server) | yes | yes | every submitted review | **verified live** |
| **GitLab** (gitlab.com, self-managed) | yes | merge requests opened | merge requests you approved | implemented from the API docs, tested against a simulated server |
| **Bitbucket Cloud** (bitbucket.org) | yes | pull requests opened | no (the API exposes no review timestamps) | implemented from the API docs, tested against a simulated server |
| **Azure DevOps** (dev.azure.com) | yes | pull requests opened (in the repos you list) | no | implemented from the API docs, tested against a simulated server |
| **Gitea / Forgejo / Codeberg** | yes | pull requests opened | reviews submitted | implemented from the API docs, tested against a simulated server |
| Anything else (Bitbucket Server / Data Center, SourceHut, AWS CodeCommit, Gerrit, plain git) | yes | no | no | commits are always mirrored |

**What "status" means.** GitHub is the only host I could try with a real account. The others are written from each
vendor's documented REST API and tested against a local fake server that answers like that API (see
`tests/test_providers.py`), so the request shapes, pagination and date handling are covered, but they have not been
run against a live GitLab, Bitbucket, Azure DevOps or Gitea account. Treat them as beta: if a lookup fails you get a
warning and **commits are still mirrored**. Please report what you see.

## Source host and mirror host

The two sides are independent: the source side *reads* dates from your work host, the mirror side *pushes* empty
commits to your personal repo. They share nothing but those dates, so any combination is possible.

| Work repos on | Mirror on | Result |
| --- | --- | --- |
| any host above | **GitHub** / GitHub Enterprise | Works, and your full history shows on the graph. **Verified live for GitHub to GitHub** (the usual work account to personal account setup). Other sources are tested against simulated APIs. |
| the same host as the mirror (GitHub to GitHub, GitLab to GitLab, ...) | the same host | Fine: the mirror just has to be a different repo in your personal account. |
| any host above | **GitLab**, **Gitea / Forgejo / Codeberg**, **Azure DevOps**, **Bitbucket** | Pushing works (it is plain git; tested for each host's URL forms, GitLab subgroups and the https-token fallback). Whether it shows up on that host's graph, and on which day, is **different and unverified**: see below. Treat as experimental. |

**Why GitHub is the verified target.** GitHub builds its contribution graph from the dates of the commits, which is what
lets backdated empty commits reproduce your history (verified on a real account). Other hosts work differently, as far
as I could establish from their documentation:

- **GitLab:** the [profile calendar](https://docs.gitlab.com/user/profile/contributions_calendar) is built from contribution
  *events*, and a GitLab issue ([Show commits in contribution graph](https://gitlab.com/gitlab-org/gitlab/-/issues/383168))
  says it counts issues, merge requests, pushes and comments, but not commits themselves. GitLab's documentation is
  inconsistent on whether commits or pushes count, and I could not confirm it from the docs alone. An event dated when the
  push happens is the likely behaviour, which would put your history on the day you push it.
- **Gitea / Forgejo:** the heatmap is built from the server's activity records, grouped by day; the source I found
  filters them by the time the record was created, i.e. when the push happened (this is an inference from source code,
  not a documented guarantee).
- **Bitbucket Cloud** and **Azure DevOps:** I found no contribution graph built from commit dates (to my knowledge
  Bitbucket Cloud has none).

If your mirror is on one of those, a run prints a one-line note about this. What you can expect: **new activity pushed by
the scheduled run on the day it happens** is the case most likely to look right; a first-time backfill of a whole year
would appear all on the day of the first push. If you try it, please tell me what your graph shows.

The mirror repo should be **empty** (or already use `main` as its default branch): the tool pushes a branch called `main`,
and on GitHub only commits on the default branch count.

## Listing projects

Any URL `git clone` accepts, or a local path, in `config/repos.json`:

```json
{
  "repos": [
    "git@github.com:acme/web.git",
    "https://gitlab.com/acme/platform/api.git",
    "git@bitbucket.org:acme/mobile.git",
    "https://dev.azure.com/acme/Shop/_git/backend",
    "https://codeberg.org/acme/tool.git",
    "/path/to/a-local-clone"
  ]
}
```

Notes per host:

- **GitLab** subgroups are kept (`group/sub/project`).
- **Azure DevOps** https (`.../_git/repo`), ssh (`git@ssh.dev.azure.com:v3/org/project/repo`) and `org.visualstudio.com` URLs
  are recognised as the same repository. Credentials belong to an organisation, so they are looked up per organisation.
- **Ssh-only front doors** are understood: `ssh.github.com`, `altssh.gitlab.com`, `altssh.bitbucket.org` map to the main host,
  and your own `~/.ssh/config` aliases are resolved with `ssh -G`.

## Tokens

Normally nothing to set: the tool uses what is already on your machine (see [authentication](authentication.md)). To
choose an account explicitly, set one of these in `config/.env` (or the environment):

| Host | Explicit (forces the account) | Also read automatically |
| --- | --- | --- |
| any | `WORK_TOKEN` | the git credential helper |
| GitHub | `WORK_GITHUB_TOKEN`, `WORK_GH_USER` (a `gh` account) | `GH_TOKEN`, `GITHUB_TOKEN`, the `gh` CLI |
| GitLab | `WORK_GITLAB_TOKEN` | `GITLAB_TOKEN`, the `glab` CLI |
| Bitbucket Cloud | `WORK_BITBUCKET_TOKEN` (`username:app_password`, or a repository/workspace access token) | `BITBUCKET_TOKEN` |
| Azure DevOps | `WORK_AZURE_DEVOPS_TOKEN` (a PAT) | `AZURE_DEVOPS_EXT_PAT`, the `az` CLI (Azure AD token) |
| Gitea / Forgejo | `WORK_GITEA_TOKEN` | `GITEA_TOKEN`, `FORGEJO_TOKEN` |

A token only needs read access: pull requests / merge requests and repository metadata. If your organisation uses SSO,
authorize the token for it.

## Self-hosted servers

The host is recognised by name for the public ones. For a self-hosted server the tool asks it what it is (a quick
request to its public version endpoint). If that is blocked or wrong, say what it is:

```
GIT_HOST_PROVIDERS=git.corp.com=gitlab,code.corp.com=gitea,github.corp.com=github
```

If the API is not at the usual place, set `WORK_GITHUB_API_URL`, `WORK_GITLAB_API_URL`, `WORK_BITBUCKET_API_URL`,
`WORK_AZURE_DEVOPS_API_URL` or `WORK_GITEA_API_URL`. Tokens are only sent over https (plain http is accepted for
`localhost` only).

## Adding another host

A host is one small file in `src/contribution_mirror/providers/`: how a token is sent, how to ask who you are, and how
to list your pull requests and reviews. See `gitea.py` (the smallest) and `tests/test_providers.py` for the pattern.
