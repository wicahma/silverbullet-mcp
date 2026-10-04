---
name: silverbullet-mcp
description: Read, write, append, delete and search pages in a SilverBullet space through the silverbullet-mcp bridge — over stdio (`npx -y silverbullet-mcp --stdio`) or a self-hosted HTTP endpoint. Use whenever the user asks to list, read, create, append to, delete or search notes/pages in SilverBullet, or mentions SilverBullet or their notes space.
---

# SilverBullet MCP

Stateless MCP bridge in front of SilverBullet's built-in `/.fs` HTTP API. It stores nothing;
every tool call is forwarded to the SilverBullet space with the credentials the server holds.

`your-host.example` below stands for **your own** deployment's host. Replace it with the
address where you run the bridge — or use the local default, `http://127.0.0.1:9339/mcp`.

## Endpoint

| | |
|---|---|
| URL | your deployment's base URL plus `/mcp`, e.g. `https://your-host.example/mcp` — `POST` only (JSON-RPC 2.0) |
| Health | `GET /mcp/health` → `{"status":"ok"}` (unauthenticated) |
| Auth | `Authorization: Bearer <MCP_TOKEN>` — missing/invalid → `401 {"error":"unauthorized"}` |
| Protocol | `2025-03-26` (also negotiates `2024-11-05`, `2025-06-18`) |
| Self-host | `python3 sb_mcp.py`; env `MCP_TOKEN`, `SB_URL` (default `http://127.0.0.1:8123`), `SB_AUTH_TOKEN`, `SB_USER_AGENT`, `MCP_HOST`/`MCP_PORT` (default `127.0.0.1:9339`) |
| stdio | `npx -y silverbullet-mcp --stdio` (or `python3 sb_mcp.py --stdio`) for clients that spawn the server — Claude Desktop, Cursor. No `MCP_TOKEN` in this mode; `SB_URL` must expose `/.fs` |

## Tools

| Tool | Arguments | Result |
|---|---|---|
| `list_pages` | — | newline-separated `*.md` page paths, sorted |
| `read_note` | `path` | raw markdown body |
| `write_note` | `path`, `content` | `wrote <path>` — full overwrite, creates if absent |
| `append_note` | `path`, `content` | `appended to <path>` — creates if absent, adds `\n` separator when needed |
| `delete_note` | `path` | `deleted <path>` — missing page is not an error |
| `search_notes` | `query` | `path:line: text` hits, case-insensitive substring, or `no matches` |

## Client configuration

```json
{
  "mcp": {
    "servers": {
      "silverbullet": {
        "type": "http",
        "url": "https://your-host.example/mcp",
        "headers": { "Authorization": "Bearer <MCP_TOKEN>" }
      }
    }
  }
}
```

`.agents/mcp.json` style clients use a top-level `mcpServers` key with the same body.
Never write the real token into a file that gets committed.

Clients that spawn a local process instead (Claude Desktop's
`claude_desktop_config.json`, Cursor's `.cursor/mcp.json`) use the npm package —
same `mcpServers` shape, `command` plus `args`, and no token:

```json
{
  "mcpServers": {
    "silverbullet": {
      "command": "npx",
      "args": ["-y", "silverbullet-mcp", "--stdio"],
      "env": { "SB_URL": "http://127.0.0.1:8123" }
    }
  }
}
```

`SB_URL` must be an address where SilverBullet's `/.fs` is reachable. If it sits
behind SSO the bridge gets the sign-in HTML back and reports a JSON parse error —
point it at localhost or an internal hostname instead.

## Calling it without MCP support

```bash
curl -s -X POST https://your-host.example/mcp \
  -H "Authorization: Bearer $MCP_TOKEN" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"read_note","arguments":{"path":"Aeris/architecture.md"}}}'
```

Other methods: `initialize`, `tools/list`, and `notifications/initialized` (returns `202`, empty body).
Batch arrays are accepted. Max request body 4 MB.

## Rules and gotchas

- Only `.md` pages. Any other extension → error `only .md pages are supported`.
- `path` is space-relative; no leading `/`, no `..` segments (`path traversal not allowed`). Subfolders are fine: `Aeris/docs/_design.md`.
- `write_note` destroys the rest of the page. Read first, or use `append_note` for additive edits.
- `delete_note` is idempotent — deleting a missing page still returns success.
- `search_notes` reads every page on each call (no index). On a large space prefer `list_pages` plus targeted `read_note`.
- Upstream write/delete calls carry `X-Sync-Mode: true` so SilverBullet's sync engine reconciles them.
- A `500`/`502` from the bridge that mentions the upstream means SilverBullet itself is unreachable or its `SB_AUTH_TOKEN` is wrong — the MCP layer is fine.

## Verify connectivity before blaming the MCP

```bash
curl -s https://your-host.example/mcp/health                       # {"status":"ok"}
curl -s -X POST https://your-host.example/mcp -H "Authorization: Bearer $MCP_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_pages","arguments":{}}}'
```

A page list means both hops are healthy. `401` = token, connection error = host/reverse proxy,
upstream error text = SilverBullet itself.

## Getting an endpoint when the user has none

The bridge is published as an npm package (`npx -y silverbullet-mcp`, then the endpoint is
`http://127.0.0.1:9339/mcp`) and ships as an agent plugin whose `mcp_token` user-config value
supplies the bearer token. For a hosted space, the base URL is the user's own domain plus `/mcp`.

Source: https://github.com/wicahma/silverbullet-mcp (stdlib-only Python, single file, no dependencies).
