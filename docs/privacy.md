# Privacy

*Last updated: 2026-11-20.*

**Short version: this tool runs on your own computer, and the developer never receives any data from it.** There is no
account, no telemetry, no analytics and no server run by the developer. Everything below describes what the software
does on your machine, so you can decide whether it suits you. This page describes the software; it is not legal advice.

## What the developer collects

Nothing. The code contains no analytics or tracking, and it is not configured to contact the developer or any server run
by the developer. You can check this: the only web addresses written into the code are the public API addresses of the
git hosts it supports (`api.github.com`, `api.bitbucket.org`, `dev.azure.com`), and a test
(`tests/test_repo_files.py`) fails if any other address is added.

## What the tool reads on your computer

| What | Why | Where it goes |
| --- | --- | --- |
| The **author email, author date and hash** of commits in the repos you list | to find your work activity (the hash only to count a commit that is in two listed repos once) | held in memory; only the dates are written to your mirror |
| The **message** of commits that mention one of your work emails | only to find a `Co-authored-by:` line naming you (such commits count for you too) | held in memory during the run, never written or logged |
| The **timestamps** of pull / merge requests you opened, reviews you made and issues you opened (from your git host's API) | to include them in the mirror | only the timestamps are written to your mirror |
| Your settings file (`.env`) and project list (`repos.json`) | to know who you are and which projects to scan | stay in those files, on your computer, private to you ([where they are](configuration.md#where-the-files-are)) |
| Tokens and logins already on your computer: environment variables, the `gh` / `glab` / `az` command line tools, your git credential helper | to talk to your git host | used in memory for the request; **never printed, logged or written to disk** by the tool |

It does **not** read or copy source code, file names or paths, commit messages, commit hashes, repository names or pull
request titles into the mirror. See [how it works](how-it-works.md).

## The setup page

`mirror.py ui` starts a small web server **on your own computer only** (`127.0.0.1`), protected by a one-time key, so
that your browser can show the setup and sync page. It loads nothing from the internet and sends nothing anywhere
except what a sync sends anyway. Token values are never shown on it. Details: [GUI](gui.md#security).

## What the tool writes

- **Your mirror repo** (on the host you choose): empty commits with the message `mirror`, your chosen name and email, and
  the original dates. This is the only data that leaves your computer on purpose, and it goes to *your own* repository.
- **Local files** in your log folder ([where it is](configuration.md#where-the-files-are)): `mirror.log` (a summary line per run,
  warnings, and the names of your repo folders; never tokens) and `api-state.json` (per host: your account name, where its
  login came from, and the time of the last run; and what kind of server each self-hosted host is). They are owner-only
  (mode 600 on Linux and macOS), like your settings file and project list, and so are the rotated copies of the log.
- **Temporary clones** of repos you listed by URL, in the `tmp/` folder of your log folder: commit metadata only, deleted
  when the scan ends, and emptied by the next run if a run was stopped before that. A host that ignores the tree-less filter
  sends file contents into them while they exist. Only if you set `MIRROR_CLONE_CACHE`, the clones are kept in that folder
  between runs instead.

Delete your log folder at any time; it is rebuilt. To remove the mirrored data, delete the mirror repo on your git host.

## Credentials in output

Everything shown on screen, written to the log or sent in a notification goes through a filter first. In an `http(s)` URL the
whole user part (`user:token@`) is replaced by `***`. In other URLs (ssh and the like) only a password is replaced, and the
value of an `Authorization:` header is replaced too. Tokens are not written to the state file either.

## Who the tool talks to

Only the hosts you point it at, over the connections your own git and network setup use:

- **Your git hosts** (GitHub, GitLab, Bitbucket, Azure DevOps, Gitea/Forgejo or your own server) for `git fetch`, `git clone`
  and `git push`, and their **REST APIs** for pull request, review and issue timestamps. Your credentials are sent to the host they
  belong to and nowhere else, only over `https` (plain `http` is accepted for `localhost` only), and a link in a response
  pointing to another host is never followed.
- **A server it does not recognise**, if you list one: up to three requests to its public version endpoints
  (`/api/v4/version`, `/api/v1/version`, `/api/v3/meta`) to find out what software it runs. You can skip this by naming the
  software yourself (`GIT_HOST_PROVIDERS`).
- **A connectivity check** in the scheduled run: it opens a TCP connection (without sending data) to your mirror's host and
  `github.com` to see whether the network is up.

It also starts programs already on your computer: `git`, `ssh -G` (to resolve a host alias; local only), the optional `gh`,
`glab` and `az` command line tools (to read a token you already have), and your system's notification command.

Those hosts have their own privacy policies and terms; they see your activity as they normally would, plus the extra requests
the tool makes with your own credentials.

## Your responsibilities

- The commit **timestamps** of work done for an employer can be information they consider confidential. Check your
  employer's policy before mirroring them. That decision is yours.
- Keep your settings file (`.env`) private. `mirror.py setup` creates it owner-only (mode 600 on Linux and macOS). If you put a
  token in it, anyone who can read the file can use that token.
- Push the mirror with **personal** credentials, not credentials managed by an employer.

## Contact

Questions or concerns about privacy: Majid Ali, [majidaliqau@gmail.com](mailto:majidaliqau@gmail.com) ·
[portfolio](https://majidev.netlify.app) · [LinkedIn](https://www.linkedin.com/in/majid-ali-074b97245/). See also the
[terms](terms.md).
