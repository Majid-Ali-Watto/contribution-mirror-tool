# Authentication

There is nothing to configure: whichever way you already use your git host works. Add your project URLs and run.
This page applies to every host; per-host details (GitLab, Bitbucket, Azure DevOps, Gitea) are in [hosts](hosts.md).

| You use | Cloning, fetching, pushing | PRs and reviews |
| --- | --- | --- |
| **ssh** (keys or agent, host aliases such as `git@github-work:...`) | your normal ssh setup | needs a login from the next two rows (or a `WORK_*TOKEN`); without one only commits are mirrored |
| **https** (credential helper, stored token) | your normal credential helper | the token your credential helper stores |
| **the host's CLI** (`gh`, `glab`, `az`) | used as a fallback when the above fails | the token of the account that has your work commits |

To see which login is used for each host: `mirror.py accounts` (or the Accounts tab of the [setup page](gui.md#accounts)).

## How it works

1. **Git operations always try your own setup first** (ssh config, `core.sshCommand`, credential helpers). If that fails, they are retried **over https with a token found automatically**, so an https URL works for someone who only has ssh keys or only a CLI, and an ssh URL works for someone who only has a CLI. Each host wants the token in a slightly different shape (GitHub `x-access-token`, GitLab `oauth2`, Bitbucket `x-token-auth` or app password, Azure DevOps PAT or Azure AD bearer, Gitea `token`); the tool knows them.
2. **Tokens are looked for in this order:** the explicit `WORK_TOKEN` / `WORK_<HOST>_TOKEN` variables, the host's conventional variables (`GH_TOKEN`, `GITLAB_TOKEN`, ...), the host's CLI (every logged-in account), then your git credential helper. A token is only used if the host accepts it, and a token for one host is never offered to another: `WORK_TOKEN` and the conventional variables only go to hosts whose kind is known (see [hosts](hosts.md#tokens)), and `GH_TOKEN` only to github.com.
3. **The right account is chosen automatically.** With several logins (e.g. personal and work), the one that has *your* commits in the projects you listed wins (on hosts that cannot filter commits by account, the one that can see the project), so your personal account is never used just because it is the active one. The choice is remembered in `logs/api-state.json` and reused while that login still works (`--full-api` compares the accounts again). An explicit variable (`WORK_TOKEN`, `WORK_GITHUB_TOKEN`, `WORK_GH_USER`, ...) in `config/.env` forces a specific one.
4. **ssh host aliases are understood.** `git@github-work:Org/repo.git` is resolved with `ssh -G` (and ssh-only front doors such as `ssh.github.com` or `altssh.gitlab.com` map to the main host) to find the real host. Self-hosted and Enterprise servers work too: see [hosts](hosts.md).
5. **The push to your personal mirror** uses your normal setup (or `PERSONAL_SSH_KEY` if set). If it fails, it falls back to https with a token of the account that owns the mirror repo. On GitHub, GitLab and Gitea the token's account is checked first and must be the repo's owner, so a work login that merely has access is never used to push (a mirror owned by an organisation therefore needs your normal ssh or https setup to work).

Git is told never to prompt (and Git Credential Manager never opens a window), so an unattended run needs credentials that work without typing (an ssh key without passphrase or an agent, or an https credential helper). The fetches and clones of your work repos run in parallel, so for them ssh is also told not to ask for a passphrase or a new host key (`BatchMode`): use `ssh-agent`, or the https fallback is used. Your own `GIT_SSH_COMMAND` or `core.sshCommand` is never replaced.

## Security

- Tokens are passed to git through the environment (not on the command line, so not visible in `ps`) and are never printed or written to logs or the state file. The state file stores only the account name and where its login came from (e.g. `gh:alice`).
- The `WORK_*TOKEN` variables are removed from the environment of every program the tool starts (git, ssh, credential helpers, `gh`/`glab`/`az`, notifications).
- If a URL contains a token (`https://user:token@host/...`), it is shown as `https://***@host/...` in every warning, error, log line and notification.
- Tokens are only sent over https. A link in an API response that points to a different host is never followed, so a token cannot be redirected elsewhere.
- Any git failure is reported with git's own message. It is never treated as "no commits".
- The code is `credentials.py` (finding and choosing logins), `providers/` (how each host wants a token) and `gitutil.py` (the auth environment for git).
