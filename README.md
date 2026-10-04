# silverbullet-mcp

A tiny MCP server that gives any MCP-capable agent read/write access to a
[SilverBullet](https://silverbullet.md) space.

No dependencies — Python 3 stdlib only. It is a translator: it forwards MCP tool
calls to SilverBullet's built-in `/.fs` HTTP API, which already supports read,
write and delete. Nothing needs to be installed or patched inside SilverBullet.

It is small enough to read in one sitting, and it speaks both MCP transports that
clients actually use: **stdio** (spawned by Claude Desktop, Cursor, and friends)
and **streamable HTTP** (shared or remote access behind a reverse proxy).

## Quick start

Run it as a server:

```bash
git clone https://github.com/wicahma/silverbullet-mcp.git
cd silverbullet-mcp
cp .env.example .env      # set MCP_TOKEN, and SB_URL if SilverBullet is elsewhere
set -a; . ./.env; set +a
python3 sb_mcp.py         # streamable HTTP on http://127.0.0.1:9339/mcp
```

Or let a client spawn it — no clone required, since it ships on npm:

```bash
SB_URL=http://127.0.0.1:8123 npx -y silverbullet-mcp --stdio
```

## Tools

| Tool | Arguments | What it does |
|---|---|---|
| `list_pages` | — | list every markdown page |
| `read_note` | `path` | read a page |
| `write_note` | `path`, `content` | create or overwrite a page |
| `append_note` | `path`, `content` | append, creating the page if absent |
| `delete_note` | `path` | delete a page |
| `search_notes` | `query` | case-insensitive substring search, returns `path:line: text` |

Paths are space-relative (`Projects/Foo.md`), restricted to `.md`, and `..`
segments are rejected.

## Transports

| | stdio | streamable HTTP |
|---|---|---|
| Start | `--stdio`, or `MCP_STDIO=1` | default |
| Framing | newline-delimited JSON-RPC on stdin/stdout | `POST /mcp` |
| Auth | none — the client already owns the process | `Authorization: Bearer $MCP_TOKEN` |
| Good for | Claude Desktop, Cursor, any client that spawns the server | remote or shared access, reverse proxies, several clients |

Both come from the same code and expose the same six tools. `MCP_TOKEN` only
matters in HTTP mode; the `SB_*` variables describe the upstream space in both.

## Client setup

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

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

### Cursor

Same body, in `.cursor/mcp.json` for one project or `~/.cursor/mcp.json`
globally.

### Any client that speaks remote MCP

```json
{
  "mcpServers": {
    "silverbullet": {
      "type": "http",
      "url": "https://your-host.example/mcp",
      "headers": { "Authorization": "Bearer <MCP_TOKEN>" }
    }
  }
}
```

Local, with no reverse proxy in front:

```json
{
  "mcpServers": {
    "silverbullet": {
      "type": "http",
      "url": "http://127.0.0.1:9339/mcp",
      "headers": { "Authorization": "Bearer <MCP_TOKEN>" }
    }
  }
}
```

Note for remote clients: `url` must be, or must proxy to, this bridge — not
SilverBullet itself. SilverBullet has no MCP endpoint of its own; that is what
this project adds.

## Configuration

All configuration is environment variables, so the same code runs for anyone.

| Variable | Default | Meaning |
|---|---|---|
| `MCP_TOKEN` | *(empty)* | **Required in HTTP mode.** Bearer token clients must send. Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `SB_URL` | `http://127.0.0.1:8123` | SilverBullet base URL. Must expose `/.fs` |
| `SB_AUTH_TOKEN` | *(empty)* | Optional. Sent to SilverBullet as `Authorization: Bearer ...` |
| `SB_USER_AGENT` | `silverbullet-mcp/1.0` | Sent on upstream calls. Some CDNs answer Python's default urllib signature with `403` / error `1010` |
| `MCP_HOST` | `127.0.0.1` | Bind address |
| `MCP_PORT` | `9339` | Bind port |
| `MCP_STDIO` | *(unset)* | Any value switches to stdio, same as `--stdio` |

## HTTP endpoints

- `POST /mcp` — JSON-RPC 2.0. Bearer token required. A notification returns `202`.
- `GET /health` (also `GET /mcp/health`) — `{"status":"ok"}`, no auth, no internal details.

Protocol versions negotiated: `2024-11-05`, `2025-03-26`, `2025-06-18`.
Maximum request body 4 MB. Batch arrays are accepted.

## Auth

The bearer token is the gate. Fail closed: if `MCP_TOKEN` is set, every request
without a matching token is rejected. If it is unset the server still starts (for
local debugging) but logs a loud warning.

**Do not expose this server to the internet without a token.** Anyone who can
reach `/mcp` can read, overwrite and delete every page in the space.

### SilverBullet's own auth

Setting `SB_AUTH_TOKEN` alone on recent SilverBullet builds (verified on the
2.10.0 Rust build) does **not** enable authentication — anonymous requests still
succeed. Per upstream docs the token is only an alternative bearer path; the
server actually enforces auth when `SB_USER` is configured. If in doubt, probe
your own instance with a deliberately wrong bearer token: if it still returns
`200`, auth is off.

Practical consequence: put the token gate at this bridge (or at your reverse
proxy), not at SilverBullet.

## Deployment

### systemd user unit

```ini
[Unit]
Description=SilverBullet MCP bridge
After=network-online.target

[Service]
EnvironmentFile=%h/silverbullet-mcp.env
WorkingDirectory=%h/silverbullet-mcp
ExecStart=/usr/bin/python3 sb_mcp.py
Restart=on-failure

[Install]
WantedBy=default.target
```

Keep the env file mode `600`.

### Behind a reverse proxy

Works fine behind Cloudflare Tunnel, nginx, Caddy, etc. Two notes from real
deployments, both learned the hard way:

- Route by path on an existing hostname if you are on a free TLS plan: some
  providers issue certificates for the apex and a single subdomain level only, so
  a two-level hostname may fail the TLS handshake. A path route on an
  already-covered host avoids that entirely.
- Machine clients cannot complete SSO. If your host sits behind an identity-aware
  proxy (Cloudflare Access and similar), bypass the MCP path and let the bearer
  token be the gate. This is the usual split: the SilverBullet UI and its `/.fs`
  API stay behind SSO, while `/mcp` is public but token-gated.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `401 unauthorized` | Missing or wrong `MCP_TOKEN` in the client's header |
| `403` with `error code: 1010` | A CDN/WAF rejected the upstream request signature. Set `SB_USER_AGENT` to an identifying value, and allow machine clients on the API path |
| Upstream replies with an HTML sign-in page, or the bridge reports `JSONDecodeError` | `SB_URL` points at a UI that is behind SSO. Point it at an address where `/.fs` is actually reachable (localhost, or an internal hostname) |
| `HttpError 500` / `502` mentioning upstream | The bridge is fine; SilverBullet is down or `SB_AUTH_TOKEN` is wrong |
| Tools missing in the client after editing config | Restart the client — MCP servers are connected at startup |
| `npx` exits immediately | `python3` is not on `PATH`; the npm wrapper is only a launcher for `sb_mcp.py` |

## Security notes

- Paths are restricted to `.md` and `..` traversal segments are rejected.
- `write_note` overwrites with no revision history.
- Binds to loopback by default; the reverse proxy is the sole ingress.
- `search_notes` reads every page on each call. There is no index.
- The bridge stores nothing. It holds no state beyond the environment it was
  started with, so a restart is always safe.

## Distribution

Published as an npm package and as an agent plugin from this same repository.

```bash
npm publish --access public     # package "silverbullet-mcp"
```

`server.json` is the official MCP Registry entry (schema `2025-12-11`): the npm
package, run over stdio. It deliberately declares no remote endpoint — that is
each deployment's own URL, so add a `remotes` entry if you want yours listed.

```bash
brew install mcp-publisher
mcp-publisher login github
mcp-publisher validate          # validates server.json
mcp-publisher publish
```

The registry name is reverse-DNS and must match the publishing GitHub account:
replace `OWNER` in `server.json` (`"name"`) and `package.json` (`"mcpName"`) with
your GitHub login before publishing — the two must be identical.

Agent plugin manifests live at `.zcode-plugin/plugin.json` (ZCode) and
`.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` (Claude Code and
the skills.sh layout). The plugin ships the MCP server plus
`skills/silverbullet-mcp/`, and reads the bearer token from the client's user
config as `mcp_token`, so no secret is stored in the repository.

## License

MIT
