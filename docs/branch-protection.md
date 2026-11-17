# Branch protection (repository ruleset)

The default branch is protected by one GitHub **ruleset**, kept in the repo as
[.github/rulesets/default-branch-protection.json](../.github/rulesets/default-branch-protection.json) so it can be
re-applied to this repo, a fork or a new copy in one command. It is the same ruleset the maintainer uses on their other repos.

| Rule | Effect on the default branch |
| --- | --- |
| `deletion` | the branch cannot be deleted |
| `non_fast_forward` | no force-pushes: history cannot be rewritten |
| `pull_request` | changes arrive through a pull request with **1 approving review**; stale approvals are dismissed when new commits are pushed; merge, squash and rebase are all allowed |

**Repository admins can bypass the ruleset** (`RepositoryRole` 5, "always"), so the owner can still push directly; everyone
else goes through a pull request.

## Apply it

```bash
gh api -X POST repos/OWNER/REPO/rulesets --input .github/rulesets/default-branch-protection.json
```

(Windows PowerShell: the same command, with `.github\rulesets\default-branch-protection.json`.) Or in the web UI:
**Settings > Rules > Rulesets > New ruleset > Import a ruleset** and choose the file.

Check it with `gh api repos/OWNER/REPO/rulesets`.

## Needs a public repo, or a paid plan

GitHub only offers rulesets on **public** repositories on a free account; a **private** repository needs GitHub Pro,
Team or Enterprise (the API answers `403 Upgrade to GitHub Pro or make this repository public`). So on a free account
the ruleset can be applied once the repo is public.

Apply it **after** the first push of the history, or push as an admin: with the ruleset active, a non-admin cannot push
to the default branch directly.
