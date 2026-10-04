#!/usr/bin/env node
// Thin launcher for the bundled Python bridge. Node only finds a python3 and
// forwards signals; all MCP logic lives in sb_mcp.py (stdlib only, no deps).
const { spawn, spawnSync } = require("node:child_process");
const { join } = require("node:path");

const script = join(__dirname, "..", "sb_mcp.py");
const python = ["python3", "python"].find(
  (p) => spawnSync(p, ["--version"], { stdio: "ignore" }).status === 0
);

if (!python) {
  console.error("silverbullet-mcp: python3 not found on PATH");
  process.exit(127);
}

const child = spawn(python, [script, ...process.argv.slice(2)], {
  stdio: "inherit",
  env: process.env,
});

for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => child.kill(sig));
}
child.on("exit", (code) => process.exit(code ?? 1));
