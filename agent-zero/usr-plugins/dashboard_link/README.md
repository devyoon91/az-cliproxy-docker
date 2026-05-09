# Dashboard Link Plugin

One-click open of the telegram-bridge cost dashboard from agent-zero.

> 새 플러그인 작성 가이드: [`docs/plugins.md`](../../../docs/plugins.md)
> (Alpine store 패턴, 슬롯 가이드, 흔한 실수). 이 플러그인이 그 가이드의 두
> 레퍼런스 중 하나다.

## Why this exists

The bridge serves the dashboard at `http://localhost:8443/dashboard?token=<token>`,
but the URL is awkward to type and the token rotates per deployment.
This plugin turns it into a button next to Browser / Compact / PDF in
the chat input action row.

## How it works

```
[Dashboard button click]
  → fetchApi POST /plugins/dashboard_link/get_token
      ← reads DASHBOARD_TOKEN from agent-zero env (env_file: .env)
      ← returns { token, port }
  → window.open(`${protocol}//${hostname}:${port}/dashboard?token=...`, "_blank")
```

The hostname comes from `window.location` so SSH-tunneled setups
(`ssh -L 8443:localhost:8443 host`) work without configuration —
whatever host you reached agent-zero on, the dashboard opens on the
same host at port 8443.

## Layout

```
agent-zero/usr-plugins/dashboard_link/
├── plugin.yaml
├── api/
│   └── get_token.py                              # ApiHandler — POST /plugins/dashboard_link/get_token
├── webui/
│   └── dashboard-link-store.js                   # Alpine store with open() method
└── extensions/
    └── webui/
        └── chat-input-bottom-actions-start/
            └── dashboard-button.html             # button → $store.dashboardLink.open()
```

## API

```
POST /api/plugins/dashboard_link/get_token
GET  /api/plugins/dashboard_link/get_token   (read-only — same response)
```

Returns:

| Status | Body | Meaning |
|---|---|---|
| 200    | `{ "token": "<value>", "port": 8443 }` | Token available |
| 503    | `{ "error": "DASHBOARD_TOKEN not set", "hint": "..." }` | Disabled, set env + recreate |

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `DASHBOARD_TOKEN` | (required) | Same value the bridge uses; without it `/dashboard` and `/api/stats` 404 on the bridge side |
| `DASHBOARD_PORT`  | `8443`     | Override only if `docker-compose.yml` maps the bridge to a different host port |

Both vars come from `az-cliproxy-docker/.env` (loaded by the
`env_file:` directive in docker-compose). Recreate `agent-zero` after
changing them so the new env is picked up.

## Why a separate plugin (vs adding to chat_pdf_export)

Different concern. PDF export operates on chat content; this opens an
external service. Bundling them would couple the install/disable
toggle to two unrelated features. Same reason chat_pdf_export
isn't part of `_chat_compaction`.
