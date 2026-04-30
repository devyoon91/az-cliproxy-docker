- favor linux commands for simple tasks where possible instead of python

- For ANY `github.com` URL (PRs, issues, repos, branches, files), use `gh` CLI **first** before any browser-based tool. Examples:
  - `gh pr view <url|number> --repo <owner>/<repo> --json title,body,files,additions,deletions`
  - `gh pr diff <url|number> --repo <owner>/<repo>`
  - `gh issue view <url|number> --repo <owner>/<repo>`
  - `gh api repos/<owner>/<repo>/pulls/<N>/files`
  The container ships with `gh` already authenticated via `GITHUB_TOKEN` (scopes: `repo`, `admin:org`, `workflow`) so private repos work without extra setup. **Never ask the user for a GitHub PAT** — if `gh` returns 404/403 unexpectedly, surface the actual error and the URL you tried; don't fall back to asking for credentials.

- Before asking the user for credentials, env vars, or configuration, **check the existing setup**:
  - Env: `env | grep -iE "<var>"` or `[ -n "$VAR" ] && echo set`
  - GitHub: `gh auth status`
  - Git: `git config --get user.name`, `git config --get user.email`
  - Anthropic / OpenAI keys: `[ -n "$ANTHROPIC_API_KEY" ] && echo "key present"`
  Only ask the user when the value is actually missing.
