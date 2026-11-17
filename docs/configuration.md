# Configuration

Both files can also be edited on the setup page: `python3 -I src/mirror.py ui` ([GUI](gui.md)).

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
| `WORK_TOKEN` | Force one login by token for every host whose kind is known: the public hosts, and servers named in `GIT_HOST_PROVIDERS` or given a `WORK_<HOST>_API_URL`. A server recognised only by probing it never receives it. |
| `WORK_GITHUB_TOKEN`, `WORK_GITLAB_TOKEN`, `WORK_BITBUCKET_TOKEN`, `WORK_AZURE_DEVOPS_TOKEN`, `WORK_GITEA_TOKEN` | Force one login for that host by token (read access to pull requests and repository metadata; authorize it for SSO if your organisation uses it). See [hosts](hosts.md). |
| `WORK_GH_USER` | Force one `gh` account as your GitHub work account. |
| `WORK_<HOST>_API_URL` | Self-hosted servers whose API is not at the usual place, e.g. `WORK_GITHUB_API_URL=https://github.example.com/api/v3`. Hosts: `GITHUB`, `GITLAB`, `BITBUCKET`, `AZURE_DEVOPS`, `GITEA`. |
| `GIT_HOST_PROVIDERS` | Tell the tool what a self-hosted server is, e.g. `git.corp.com=gitlab,code.corp.com=gitea`. |
| `MIRROR_PR_REVIEWS` | `off` mirrors commits only (no PRs and reviews). |
| `PERSONAL_SSH_KEY` | SSH key used **only** for pushing to the mirror (handy for unattended runs without an agent). Windows paths are fine. |
| `MIRROR_LOG_DIR` | Where `mirror.log`, `api-state.json` and `mirror.lock` go. Default `logs/`. |
| `MIRROR_CLONE_CACHE` | A folder in which to keep the clones of URL entries between runs, so later runs only fetch new commits (faster for long histories). Default: empty, temporary clones deleted after each run. The cached clones hold the commit metadata of those repos (authors, dates, messages), so keep the folder private. |
| `MIRROR_ENV_FILE` | (Set in your shell, not in the file) path of a different env file. |
| `MIRROR_REPOS_FILE` | (Set in your shell, not in the file) path of a different project list for `sync`. |

A variable already set in your shell overrides the file. Keep the file private (`chmod 600 config/.env`), especially if it holds a token: on Linux and macOS each run warns if other users can read it.

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
- **URL** (ssh, https, or an ssh host alias from your `~/.ssh/config`; any host, see [hosts](hosts.md)): for repos you have not cloned. Each run makes a bare, tree-less clone in a temp folder (commit metadata only), reads only the commit timestamps, and deletes it; with `MIRROR_CLONE_CACHE` the clone is kept and refreshed instead.
- Duplicates are ignored, and a URL that is the same `owner/repo` as the `origin` of a listed local repo is skipped (whatever the spelling). A commit that is in two listed repos anyway (a fork, a second clone) is counted once.
- **Older format:** files written before paths and URLs were merged into one list have the URLs under a separate `"remote_repos"` key. That still works (each run prints a short note); to update, move those URLs into `"repos"` and delete the key.
- A path that is missing or not a git repo is skipped with a warning (on stderr); the rest still run. A file that is not valid JSON, or not `{"repos": [...]}`, stops the run with a message saying so. The file is read as UTF-8 (a BOM from a Windows editor is fine).
- The owners of the listed repos also decide which pull requests and reviews count: activity in other organisations or in your own side projects is left out (on GitLab: activity outside the listed projects).
- To add a project, add a line. No code change and no other setup needed: see [authentication](authentication.md).
- Precedence: `--src` or folder names passed to `sync` > `config/repos.json` > scanning all sibling folders (only if `config/repos.json` does not exist).
- A different file can be used with `--repos-file <path>`.
