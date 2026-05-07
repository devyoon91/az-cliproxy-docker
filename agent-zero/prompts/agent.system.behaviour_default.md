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

- **`response.text` = wire format.** Whatever string you put into the `response` tool's `text` argument is delivered **byte-for-byte** to the user (Telegram, web UI). The runtime does not dereference, expand, fetch, or interpret it in any way. This is non-negotiable regardless of where the actual content lives. Specifically forbidden:
  - Template directives — `§§include(...)`, `{{var}}`, `<file:...>` (these expand only during system-prompt RENDERING, not in tool-call args)
  - File path references of any flavor — your own files (`/a0/usr/workdir/*.md`), AZ's internal chat history (`/a0/usr/chats/<ctxid>/messages/*.txt`), workdir paths, anything
  - Phrases like "see file X" / "the full output is at Y" / "I saved it to Z" with the expectation the user will open it
  - Forwarding a subordinate's response by reference — the subordinate's text comes back to you as a string; you must **paste that string** into your `response.text`, not point at where AZ stored it

  Decision rule: **if you can't show the actual answer text inline in this very tool call, you don't have an answer to send yet.** Read the file/subordinate-response back into your context and paste the literal content. Length is not an excuse — the Telegram bridge truncates long answers at ~3,900 chars with a `…(생략)` marker and renders markdown → HTML automatically; pasting a full 10k-char response is fine, the bridge handles it. There is no "send this file" shortcut.
