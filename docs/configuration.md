# Configuration

Both files can also be edited on the setup page: `python3 -I src/mirror.py ui` ([GUI](gui.md)). `python3 -I src/mirror.py setup`
(or `make setup`) creates them from the templates if they are missing, and prints where they are. The templates' example
values (`you@company.com`, `your-user/work-contributions`, the example repos) count as not filled in: a run stops and says
which ones to replace, and example entries in the project list are never cloned.

Your private values are kept in two files that are not part of the code, so the repo is safe to publish and every user has
their own:

| File | Holds | Template |
| --- | --- | --- |
| your settings file, `.env` | Emails, name, mirror folder and remote, optional overrides | `src/contribution_mirror/templates/env.example` |
| your project list, `repos.json` | Your projects: local clone paths and/or git URLs | `src/contribution_mirror/templates/repos.example.json` |

## Where the files are

The files go in the per-user folders of your operating system, so updating, cleaning (`git clean -fdx`) or installing the
tool never touches them:

| System | Settings file and project list | Logs: `mirror.log`, `api-state.json`, `mirror.lock`, the temporary clones in `tmp/` |
| --- | --- | --- |
| Linux | `~/.config/contribution-mirror` (`$XDG_CONFIG_HOME` is respected) | `~/.local/state/contribution-mirror` (`$XDG_STATE_HOME` is respected) |
| macOS | `~/Library/Application Support/contribution-mirror` | `~/Library/Logs/contribution-mirror` |
| Windows | `%APPDATA%\contribution-mirror` | `%LOCALAPPDATA%\contribution-mirror\logs` |

Two rules keep existing setups where they are:

- A checkout that already has `config/.env` or `config/repos.json` keeps using `config/` in the project folder, and one
  that already has `logs/` keeps using `logs/`.
- `MIRROR_ENV_FILE`, `MIRROR_REPOS_FILE` and `MIRROR_LOG_DIR` (set in your shell, not in the file) override each file or
  folder.

`mirror.py setup` prints the paths in use. On Linux and macOS the two files, `mirror.log` (with its rotated copies) and `api-state.json` are owner-only
(mode 600), and the folders the tool creates are owner-only too (mode 700).

## The settings file (`.env`)

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
| `MIRROR_DIR` | Local folder of the mirror repo. Default `~/work-contributions`. Created if missing. See [the mirror folder](#the-mirror-folder). |
| `WORK_TOKEN` | Force one login by token for every host whose kind is known: the public hosts, and servers named in `GIT_HOST_PROVIDERS` or given a `WORK_<HOST>_API_URL`. A server recognised only by probing it never receives it. |
| `WORK_GITHUB_TOKEN`, `WORK_GITLAB_TOKEN`, `WORK_BITBUCKET_TOKEN`, `WORK_AZURE_DEVOPS_TOKEN`, `WORK_GITEA_TOKEN` | Force one login for that host by token (read access to pull requests and repository metadata; authorize it for SSO if your organisation uses it). See [hosts](hosts.md). |
| `WORK_GH_USER` | Force one `gh` account as your GitHub work account. |
| `WORK_<HOST>_API_URL` | Self-hosted servers whose API is not at the usual place, e.g. `WORK_GITHUB_API_URL=https://github.example.com/api/v3`. Hosts: `GITHUB`, `GITLAB`, `BITBUCKET`, `AZURE_DEVOPS`, `GITEA`. |
| `GIT_HOST_PROVIDERS` | Tell the tool what a self-hosted server is, e.g. `git.corp.com=gitlab,code.corp.com=gitea`. |
| `MIRROR_PR_REVIEWS` | `off` mirrors commits only (no pull requests, reviews or issues). |
| `MIRROR_SINCE`, `MIRROR_UNTIL` | Only mirror dates on or after / on or before a day, `YYYY-MM-DD` (e.g. when you joined or left). Each date is compared by its calendar day in its own time zone, as the graph shows it. Empty: no limit. Dates already in the mirror are never removed. |
| `PERSONAL_SSH_KEY` | SSH key used **only** for pushing to the mirror (handy for unattended runs without an agent). Any path works, spaces included. |
| `MIRROR_LOG_DIR` | Where `mirror.log`, `api-state.json`, `mirror.lock` and the temporary clones (`tmp/`) go. Default: your log folder (see [where the files are](#where-the-files-are)). |
| `MIRROR_CLONE_CACHE` | A folder in which to keep the clones of URL entries between runs, so later runs only fetch new commits (faster for long histories). Default: empty, temporary clones deleted after each run. The cached clones hold the commit metadata of those repos (authors, dates, messages), so keep the folder private. |
| `MIRROR_ENV_FILE` | (Set in your shell, not in the file) path of a different settings file. |
| `MIRROR_REPOS_FILE` | (Set in your shell, not in the file) path of a different project list for `sync`. |

A variable already set in your shell overrides the file. Keep the file private: `mirror.py setup` creates it with mode 600, and
the page keeps it that way. This matters most if it holds a token: on Linux and macOS each run warns if other users can read it.

## The project list (`repos.json`)

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
- **URL** (ssh, https, or an ssh host alias from your `~/.ssh/config`; any host, see [hosts](hosts.md)): for repos you have not cloned. Each run makes a bare, tree-less clone (commit metadata only) in a temporary folder inside your log folder (`tmp/`, private to you), reads only the commit timestamps, and deletes it; with `MIRROR_CLONE_CACHE` the clone is kept and refreshed instead.
- Duplicates are ignored, and a URL that is the same `owner/repo` as the `origin` of a listed local repo is skipped (whatever the spelling). A commit that is in two listed repos anyway (a fork, a second clone) is counted once.
- **Older format:** files written before paths and URLs were merged into one list have the URLs under a separate `"remote_repos"` key. That still works (each run prints a short note); to update, move those URLs into `"repos"` and delete the key.
- A path that is missing or not a git repo is skipped with a warning (on stderr); the rest still run. A file that is not valid JSON, or not `{"repos": [...]}`, stops the run with a message saying so. The file is read as UTF-8 (a BOM from a Windows editor is fine).
- The owners of the listed repos also decide which pull requests and reviews count: activity in other organisations or in your own side projects is left out (on GitLab: activity outside the listed projects).
- To add a project, add a line. No code change and no other setup needed: see [authentication](authentication.md).
- Precedence: `--src`, or folder names passed to `sync`, replace the list. Without a project list and without `--src`, a run stops with an error that says to run `mirror.py setup` or pass `--src`. It never scans other folders by itself.
- A different file can be used with `--repos-file <path>`.

## Profiles (several setups)

A profile is a separate setup with its own settings file and project list, for example one per employer, or a current
and a former job. Each profile can push to the same mirror repo or to another one.

- The usual files are the **default** profile. Profile `NAME` keeps its files in `profiles/NAME/` next to them
  (`profiles/NAME/.env` and `profiles/NAME/repos.json`). A name is up to 40 letters, digits, `-` and `_`.
- Choose one for a command with `--profile NAME` before the command: `mirror.py --profile acme setup` creates its files,
  `mirror.py --profile acme sync` syncs it. `MIRROR_PROFILE=acme` in your shell does the same.
- On the page, the **Profile** menu at the top switches every tab to that profile; **New profile…** adds one (its files are
  created when you first save).
- **Automatic runs sync every profile** that has a settings file, one after the other.
- All profiles share the log folder (one log, one run lock); each keeps its own `api-state-NAME.json`, so their logins
  do not mix.

## The mirror folder

The mirror is a local git repo, at `MIRROR_DIR`, that the tool writes the empty commits into and pushes to `EXPECTED_REMOTE`.
A folder that happens to be a git repo may hold real work, so the tool only writes to a repo it can recognise as the mirror:

- A repo the tool creates is marked with the git setting `contribution-mirror.mirror=true`.
- An existing folder without that mark is used only if every commit in it is a mirror commit (an empty tree, message
  `mirror`), and is then marked. Otherwise the run stops with a message and writes nothing.

If your personal repo started with a README, its first commit is not a mirror commit, so a clone of it on a second computer
is refused. Mark that clone yourself, once:

```bash
git -C ~/work-contributions config contribution-mirror.mirror true
```
