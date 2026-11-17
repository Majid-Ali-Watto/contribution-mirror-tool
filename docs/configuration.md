# Configuration

Private values live in two git-ignored files inside `config/`, so the repo is safe to publish and every developer uses their own:

| File | Holds | Template |
| --- | --- | --- |
| `config/.env` | Emails, name, mirror folder and remote, optional overrides | `config/.env.example` |
| `config/repos.json` | Your projects: local clone paths and/or git URLs | `config/repos.example.json` |

## `config/.env`

Plain `KEY=VALUE` lines, one per line. Comments go on their own line (no inline comments). The file is parsed, never executed as shell.

**Required**

| Key | Meaning |
| --- | --- |
| `WORK_EMAIL` | Your author email in the work repos. Several: separate with spaces. |
| `PERSONAL_NAME` | Author name of the mirror commits. |
| `PERSONAL_EMAIL` | Author email of the mirror commits. Must be **verified** on your personal GitHub account, otherwise they will not show on your graph. |
| `EXPECTED_REMOTE` | Remote of the mirror repo. `sync` refuses to push to any other repo. Any URL form works (ssh or https): only `owner/repo` is compared. The mirror can be on any host, but GitHub is the one verified to show history on your graph: see [hosts](hosts.md#source-host-and-mirror-host). |

**Optional** (normally not needed)

| Key | Meaning |
| --- | --- |
| `MIRROR_DIR` | Local folder of the mirror repo. Default `~/work-contributions`. Created if missing. |
| `WORK_TOKEN` | Force one login for **any** host by token. |
| `WORK_GITHUB_TOKEN`, `WORK_GITLAB_TOKEN`, `WORK_BITBUCKET_TOKEN`, `WORK_AZURE_DEVOPS_TOKEN`, `WORK_GITEA_TOKEN` | Force one login for that host by token (read access to pull requests and repository metadata; authorize it for SSO if your organisation uses it). See [hosts](hosts.md). |
| `WORK_GH_USER` | Force one `gh` account as your GitHub work account. |
| `WORK_<HOST>_API_URL` | Self-hosted servers whose API is not at the usual place, e.g. `WORK_GITHUB_API_URL=https://github.example.com/api/v3`. Hosts: `GITHUB`, `GITLAB`, `BITBUCKET`, `AZURE_DEVOPS`, `GITEA`. |
| `GIT_HOST_PROVIDERS` | Tell the tool what a self-hosted server is, e.g. `git.corp.com=gitlab,code.corp.com=gitea`. |
| `MIRROR_PR_REVIEWS` | `off` mirrors commits only (no PRs and reviews). |
| `PERSONAL_SSH_KEY` | SSH key used **only** for pushing to the mirror (handy for unattended runs without an agent). Windows paths are fine. |
| `MIRROR_LOG_DIR` | Where `mirror.log` and `api-state.json` go. Default `logs/`. |
| `MIRROR_ENV_FILE` | (Set in your shell, not in the file) path of a different env file. |

A variable already set in your shell overrides the file. Keep the file private (`chmod 600 config/.env`), especially if it holds a token.

## `config/repos.json`

List your projects. **Each entry is either a local clone path or a git URL, in any mix:**

```json
{
  "repos": [
    "/path/to/a-repo-you-have-cloned",
    "git@github.com:YourOrg/a-repo-over-ssh.git",
    "https://github.com/YourOrg/a-repo-over-https.git"
  ]
}
```

- **Local path:** must be a git repo (contain `.git`); read directly, nothing is searched inside it. `~` is expanded. It is `git fetch`ed first so a stale clone does not hide commits.
- **URL** (ssh, https, or an ssh host alias from your `~/.ssh/config`; any host, see [hosts](hosts.md)): for repos you have not cloned. Each run makes a bare, tree-less clone in a temp folder (about 100 KB), reads only the commit timestamps, and deletes it.
- Duplicates are ignored, and a URL that is the same `owner/repo` as the `origin` of a listed local repo is skipped (whatever the spelling), so nothing is counted twice. The older `remote_repos` key (a list of URLs) still works.
- A path that is missing or not a git repo is skipped with a warning (on stderr); the rest still run.
- To add a project, add a line. No code change and no other setup needed: see [authentication](authentication.md).
- Precedence: `--src` or folder names passed to `sync` > `config/repos.json` > scanning all sibling folders (only if `config/repos.json` does not exist).
- A different file can be used with `--repos-file <path>`.
