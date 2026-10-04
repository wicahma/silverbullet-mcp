#!/usr/bin/env python3
"""MCP server bridging an external agent harness to a SilverBullet space over /.fs.

Two transports, one implementation: streamable HTTP (`python3 sb_mcp.py`, for
remote/shared use) and stdio (`python3 sb_mcp.py --stdio`, for clients that spawn
the bridge as a child process such as Claude Desktop and Cursor)."""

import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SB_URL = os.environ.get("SB_URL", "http://127.0.0.1:8123").rstrip("/")
SB_AUTH_TOKEN = os.environ.get("SB_AUTH_TOKEN", "")
# Sent on every upstream call. Identifying the bridge beats Python's default
# urllib signature: a Cloudflare-fronted host answers that one with 403 / error 1010.
SB_USER_AGENT = os.environ.get("SB_USER_AGENT", "silverbullet-mcp/1.0")
MCP_TOKEN = os.environ.get("MCP_TOKEN", "")
HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "9339"))
PROTOCOL = "2025-03-26"
SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
MAX_BODY = 4 * 1024 * 1024
SERVER_INFO = {"name": "silverbullet-mcp", "version": "1.0.0"}


def sb_request(method, path, body=None):
    url = SB_URL + path
    headers = {"User-Agent": SB_USER_AGENT}
    if SB_AUTH_TOKEN:
        headers["Authorization"] = "Bearer " + SB_AUTH_TOKEN
    data = None
    if body is not None:
        data = body.encode("utf-8")
        headers["Content-Type"] = "text/markdown; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    if method in ("PUT", "DELETE"):
        req.add_header("X-Sync-Mode", "true")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def clean_path(rel):
    rel = (rel or "").strip().lstrip("/")
    if not rel:
        raise ValueError("path is required")
    if ".." in rel.split("/"):
        raise ValueError("path traversal not allowed")
    if not rel.endswith(".md"):
        raise ValueError("only .md pages are supported")
    return rel


def page_path(rel):
    return "/.fs/" + urllib.parse.quote(clean_path(rel), safe="/")


def tool_list_pages(args):
    _, body = sb_request("GET", "/.fs")
    entries = json.loads(body)
    if isinstance(entries, dict):
        entries = entries.get("files", list(entries.values()))
    names = sorted(
        e.get("name") or e.get("path", "")
        for e in entries
        if (e.get("name") or e.get("path", "")).endswith(".md")
    )
    return "\n".join(n for n in names if n)


def tool_read_note(args):
    _, body = sb_request("GET", page_path(args["path"]))
    return body


def tool_write_note(args):
    rel = clean_path(args["path"])
    sb_request("PUT", "/.fs/" + urllib.parse.quote(rel, safe="/"), args.get("content", ""))
    return "wrote " + rel


def tool_append_note(args):
    rel = clean_path(args["path"])
    existing = ""
    try:
        _, existing = sb_request("GET", "/.fs/" + urllib.parse.quote(rel, safe="/"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    addition = args.get("content", "")
    sep = "" if existing.endswith("\n") or not existing else "\n"
    sb_request("PUT", "/.fs/" + urllib.parse.quote(rel, safe="/"), existing + sep + addition)
    return "appended to " + rel


def tool_delete_note(args):
    rel = clean_path(args["path"])
    try:
        sb_request("DELETE", "/.fs/" + urllib.parse.quote(rel, safe="/"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    return "deleted " + rel


def tool_search_notes(args):
    needle = (args.get("query") or "").lower()
    if not needle:
        raise ValueError("query is required")
    hits = []
    for name in tool_list_pages({}).splitlines():
        try:
            _, body = sb_request("GET", "/.fs/" + urllib.parse.quote(name, safe="/"))
        except urllib.error.HTTPError:
            continue
        for num, line in enumerate(body.splitlines(), 1):
            if needle in line.lower():
                hits.append("%s:%d: %s" % (name, num, line.strip()))
    return "\n".join(hits) or "no matches"


TOOLS = [
    {
        "name": "list_pages",
        "description": "List all markdown pages in the SilverBullet space.",
        "inputSchema": {"type": "object", "properties": {}},
        "readOnlyHint": True,
    },
    {
        "name": "read_note",
        "description": "Read the full markdown content of a page.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Page path, e.g. Projects/Foo.md"}},
            "required": ["path"],
        },
        "readOnlyHint": True,
    },
    {
        "name": "write_note",
        "description": "Create or overwrite a page with the given markdown content.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "append_note",
        "description": "Append markdown to a page, creating it if it does not exist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "delete_note",
        "description": "Delete a page from the space.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "search_notes",
        "description": "Case-insensitive substring search across all pages. Returns path:line: text.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "readOnlyHint": True,
    },
]

HANDLERS = {
    "list_pages": tool_list_pages,
    "read_note": tool_read_note,
    "write_note": tool_write_note,
    "append_note": tool_append_note,
    "delete_note": tool_delete_note,
    "search_notes": tool_search_notes,
}


def dispatch(msg):
    method = msg.get("method")
    mid = msg.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        requested = (msg.get("params") or {}).get("protocolVersion")
        negotiated = requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL
        return ok(
            {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            }
        )
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if not handler:
            return err(-32602, "unknown tool: %s" % name)
        try:
            text = handler(args)
        except urllib.error.HTTPError as e:
            return ok(
                {
                    "content": [{"type": "text", "text": "SilverBullet HTTP %d: %s" % (e.code, e.read().decode("utf-8", "replace"))}],
                    "isError": True,
                }
            )
        except Exception as e:
            return ok({"content": [{"type": "text", "text": "%s: %s" % (type(e).__name__, e)}], "isError": True})
        return ok({"content": [{"type": "text", "text": text or ""}]})
    return err(-32601, "method not found: %s" % method)


def serve_stdio():
    """Newline-delimited JSON-RPC on stdin/stdout, one reply per request.

    No bearer token here: the client already runs this process as its own child,
    so the trust boundary is the process, not the wire.
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            if isinstance(msg, list):
                reply = [r for r in (dispatch(m) for m in msg) if r]
            else:
                reply = dispatch(msg)
        if reply is None:
            continue
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, payload, ctype="application/json"):
        body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not MCP_TOKEN:
            return False
        provided = self.headers.get("Authorization", "")
        return hmac.compare_digest(provided, "Bearer " + MCP_TOKEN)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            self.rfile.read(min(length, MAX_BODY))
            return b"", True
        return (self.rfile.read(length) if length else b""), False

    def do_GET(self):
        if self.path.rstrip("/").split("/")[-1] == "health" or self.path.rstrip("/") in ("/health", "/mcp/health"):
            return self._send(200, {"status": "ok"})
        return self._send(405, {"error": "use POST /mcp"})

    def do_POST(self):
        raw, too_big = self._read_body()
        if too_big:
            return self._send(413, {"error": "payload too large"})
        if self.path.rstrip("/") not in ("/mcp", ""):
            return self._send(404, {"error": "not found"})
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        if isinstance(msg, list):
            out = [r for r in (dispatch(m) for m in msg) if r]
            return self._send(200, out)
        result = dispatch(msg)
        if result is None:
            return self._send(202, "")
        return self._send(200, result)


if __name__ == "__main__":
    if "--stdio" in sys.argv[1:] or os.environ.get("MCP_STDIO"):
        sys.stderr.write("silverbullet-mcp stdio -> %s\n" % SB_URL)
        serve_stdio()
        sys.exit(0)
    if not SB_AUTH_TOKEN:
        sys.stderr.write("warning: SB_AUTH_TOKEN unset, forwarding requests unauthenticated\n")
    if not MCP_TOKEN:
        sys.stderr.write("warning: MCP_TOKEN unset, the endpoint is open to anyone who can reach it\n")
    sys.stderr.write("silverbullet-mcp listening on %s:%d -> %s\n" % (HOST, PORT, SB_URL))
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
