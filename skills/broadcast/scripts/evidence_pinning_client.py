#!/usr/bin/env python3
"""Minimal MCP stdio client for mcp/evidence-pinning (Phase 4 — evidence
pinning integration).

evidence-pinning-mcp is a TypeScript MCP server, not a Python library — it
can't be `import`ed the way dedup_store.py/source_registry.py/rank.py are.
Rather than reimplement its storage-writing logic in Python (this repo has
hit real bugs before from duplicated logic drifting apart — see
wiki-governor's health_score.py, which deliberately reuses check_vault.py's
vault loading for exactly this reason), this speaks the real MCP stdio
JSON-RPC protocol to the real compiled server (dist/index.js) as a
subprocess. One source of truth for source/claim storage: the TypeScript
server. This module is just a thin transport.

The wire protocol here is not guessed at: it matches
mcp/evidence-pinning/test/server.test.mjs exactly — plain newline-delimited
JSON-RPC 2.0 over stdin/stdout, matched by numeric id, one `initialize` call
before any `tools/call` (that test doesn't send a separate
"notifications/initialized" message either, and its calls work regardless,
so this doesn't send one). Verified live against the actual built server
before being written this way, not assumed from the MCP spec alone.

Every tool call round-trips through JSON twice: once for the outer
JSON-RPC envelope, once for the tool's own JSON-encoded text response (see
index.ts's CallToolRequestSchema handler: `{content: [{type: "text",
text: JSON.stringify(result)}]}`) — call_tool() unwraps both layers so
callers just get the plain result dict.

Requires the server to already be built (`npm run build` in
mcp/evidence-pinning) — this module doesn't build it. No known way to
detect "not built yet" other than the resulting FileNotFoundError when
spawning node against a missing dist/index.js; that error is left to
propagate as-is rather than papered over with a friendlier message, since
the fix (run npm run build) is the same either way and a wrapped message
would just be one more thing to keep in sync with the real error.

Known simplification: no per-call timeout. The server is a local child
process this module itself spawns and fully controls, not a flaky remote
API — the realistic failure mode is the process exiting (which readline()
surfaces immediately as an empty line, not a hang), not it hanging
mid-response. Revisit if that assumption stops holding.

stdlib only, matching this repo's other reference tooling."""

import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SERVER_PATH = os.path.join(HERE, "..", "..", "..", "mcp", "evidence-pinning", "dist", "index.js")


class EvidencePinningError(Exception):
    """Raised when the server returns isError: true for a tool call — e.g.
    pin_claim against an unregistered source_id, verify_claim on an unknown
    claim_id. Carries the server's own error message text unmodified."""


class EvidencePinningClient:
    """A short-lived connection to one evidence-pinning-mcp server process.
    Use as a context manager so the child process is always killed, even
    if a call raises:

        with EvidencePinningClient(store_path="/path/to/store") as client:
            source = client.register_source(url, title)
            claim = client.pin_claim(run_id, text, [source["source_id"]], excerpt)
    """

    def __init__(self, store_path: str, server_path: str = DEFAULT_SERVER_PATH, node_bin: str = "node"):
        self.store_path = store_path
        self.server_path = server_path
        self.node_bin = node_bin
        self._proc: subprocess.Popen | None = None
        self._next_id = 0

    def __enter__(self) -> "EvidencePinningClient":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def start(self) -> None:
        env = dict(os.environ)
        env["EVIDENCE_STORE_PATH"] = self.store_path
        self._proc = subprocess.Popen(
            [self.node_bin, self.server_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,  # line-buffered — required so writes are flushed promptly enough for the server to see them
        )
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "broadcast-evidence-client", "version": "0.1.0"},
        })

    def close(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait()
            # Popen.kill()/.wait() reap the process but don't close the
            # pipe file objects Popen opened for stdin/stdout/stderr —
            # confirmed live (ResourceWarning: unclosed file, from every
            # test in this suite) before this was added.
            for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
                if stream is not None:
                    stream.close()
            self._proc = None

    def _rpc(self, method: str, params: dict) -> dict:
        if self._proc is None:
            raise RuntimeError("EvidencePinningClient is not started — call start() or use it as a context manager")
        msg_id = self._next_id
        self._next_id += 1
        self._proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}) + "\n")
        self._proc.stdin.flush()
        while True:
            line = self._proc.stdout.readline()
            if not line:
                stderr = self._proc.stderr.read()
                raise RuntimeError(f"evidence-pinning-mcp server closed its output unexpectedly. stderr:\n{stderr}")
            message = json.loads(line)
            if message.get("id") == msg_id:
                return message

    def call_tool(self, name: str, arguments: dict) -> dict:
        """Calls one MCP tool and returns its parsed result dict. Raises
        EvidencePinningError if the server reports isError: true."""
        response = self._rpc("tools/call", {"name": name, "arguments": arguments})
        result = response.get("result", {})
        text = result.get("content", [{}])[0].get("text", "{}")
        data = json.loads(text)
        if result.get("isError"):
            raise EvidencePinningError(data.get("error", f"{name} failed with no error message"))
        return data

    # ── Typed convenience wrappers, one per tool — see mcp/evidence-pinning's
    #    src/index.ts for the authoritative tool descriptions/schemas. ──────

    def register_source(self, url: str, title: str, id_hint: str | None = None) -> dict:
        args = {"url": url, "title": title}
        if id_hint:
            args["id_hint"] = id_hint
        return self.call_tool("register_source", args)

    def pin_claim(self, run_id: str, text: str, source_ids: list[str], excerpt: str) -> dict:
        return self.call_tool("pin_claim", {"run_id": run_id, "text": text, "source_ids": source_ids, "excerpt": excerpt})

    def get_claims(self, run_id: str) -> dict:
        return self.call_tool("get_claims", {"run_id": run_id})

    def verify_claim(self, claim_id: str) -> dict:
        return self.call_tool("verify_claim", {"claim_id": claim_id})

    def flag_claim(self, claim_id: str, reason: str) -> dict:
        return self.call_tool("flag_claim", {"claim_id": claim_id, "reason": reason})

    def check_source_decay(self, source_id: str) -> dict:
        return self.call_tool("check_source_decay", {"source_id": source_id})

    def get_provenance(self, target_type: str, target_id: str) -> dict:
        return self.call_tool("get_provenance", {"target_type": target_type, "target_id": target_id})
