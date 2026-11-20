"""Machine-readable CLI spec (--help-json) for Cordis discovery."""

from __future__ import annotations

import argparse
import json

_S = {"type": "string"}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    out: dict = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


def help_json() -> dict:
    return {
        "command": "agents-traces",
        "description": "Append-only JSONL session traces and per-request assemble.",
        "commands": {
            "stats": {
                "usage": "agents-traces stats [--date D] [--days N] [--by-model] [--json]",
                "description": "Token, cost and tool statistics.",
                "side_effects": "read-only",
                "parameters": _obj({
                    "date": _S, "days": {"type": "integer"},
                    "by_model": {"type": "boolean"}, "json": {"type": "boolean"},
                }),
            },
            "analyze-models": {
                "usage": "agents-traces analyze-models [--days N] [--json]",
                "description": "Tool failure rates and empirical traps by model.",
                "side_effects": "read-only",
            },
            "inspect": {
                "usage": "agents-traces inspect [SESSION_ID]",
                "description": "Timeline for a session (defaults to latest).",
                "side_effects": "read-only",
                "parameters": _obj({"session_id": _S}),
            },
            "sessions": {
                "usage": "agents-traces sessions [--days N] [--limit N]",
                "description": "Recent active sessions.",
                "side_effects": "read-only",
                "parameters": _obj({"days": {"type": "integer"}, "limit": {"type": "integer"}}),
            },
            "tail": {
                "usage": "agents-traces tail [-n N] [-f] [--raw]",
                "description": "Tail trace events.",
                "side_effects": "read-only",
                "parameters": _obj({
                    "lines": {"type": "integer"}, "follow": {"type": "boolean"},
                    "raw": {"type": "boolean"},
                }),
            },
            "record": {
                "usage": "agents-traces record [--session ID] [--type T] [--tool T] [--status S] [--data JSON] ...",
                "description": "Record a trace event.",
                "side_effects": "mutating",
            },
            "cleanup": {
                "usage": "agents-traces cleanup [--keep-days N] [--compress]",
                "description": "Delete or compress old traces.",
                "side_effects": "mutating",
            },
            "assemble": {
                "usage": "agents-traces assemble --session ID [--limit N] [--days N] [--include-tools]",
                "description": "Rebuild message history for a session.",
                "side_effects": "read-only",
                "parameters": _obj(
                    {
                        "session": _S,
                        "limit": {"type": "integer"},
                        "days": {"type": "integer"},
                        "include_tools": {"type": "boolean"},
                    },
                    ["session"],
                ),
            },
            "export": {
                "usage": "agents-traces export [--session ID] [--output PATH] [--no-tools] [--no-frames] [--days N]",
                "description": "Export a session transcript.",
                "side_effects": "read-only",
            },
            "read-content": {
                "usage": "agents-traces read-content --session ID --digest D [--offset N] [--limit N] [--json]",
                "description": "Resolve a <ref digest> to the retained body (digest checked).",
                "side_effects": "read-only",
                "parameters": _obj(
                    {
                        "session": _S,
                        "digest": _S,
                        "offset": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "json": {"type": "boolean"},
                    },
                    ["session", "digest"],
                ),
            },
            "compact": {
                "usage": "agents-traces compact --session ID [--keep-last N] [--days N] [--content TEXT] [--json]",
                "description": "Write a compaction frame covering old events.",
                "side_effects": "mutating",
                "parameters": _obj(
                    {
                        "session": _S,
                        "keep_last": {"type": "integer"},
                        "days": {"type": "integer"},
                        "content": _S,
                        "json": {"type": "boolean"},
                    },
                    ["session"],
                ),
            },
            "ingest": {
                "usage": "agents-traces ingest",
                "description": "Ingest IDE transcripts into traces.",
                "side_effects": "mutating",
            },
            "seal": {
                "usage": "agents-traces seal [TARGET] [--session ID] [--output PATH] [--json]",
                "description": "Cryptographically seal tool calls into a SHA-256 hash chain.",
                "side_effects": "mutating",
                "parameters": _obj({
                    "target": _S, "session": _S, "output": _S, "json": {"type": "boolean"},
                }),
            },
            "verify": {
                "usage": "agents-traces verify PATH [--json]",
                "description": "Verify a sealed hash chain.",
                "side_effects": "read-only",
                "parameters": _obj({"path": _S, "json": {"type": "boolean"}}, ["path"]),
            },
            "audit": {
                "usage": "agents-traces audit [TARGET] [--session ID] [--scope PATH] [--json]",
                "description": "Audit tool calls: non-determinism, redundant calls, scope violations.",
                "side_effects": "read-only",
                "parameters": _obj({
                    "target": _S, "session": _S, "scope": _S, "json": {"type": "boolean"},
                }),
            },
            "replay": {
                "usage": "agents-traces replay [TARGET] [--session ID] [--scope PATH] [--json]",
                "description": "Alias for audit.",
                "side_effects": "read-only",
            },
            "init": {
                "usage": "agents-traces init",
                "description": "Auto-configure rules, MCP, and skills in all IDEs.",
                "side_effects": "mutating",
            },
            "sync": {
                "usage": "agents-traces sync [--init]",
                "description": "Auto-configure rules, MCP, and skills across all IDEs.",
                "side_effects": "mutating",
            },
            "skills": {
                "usage": "agents-traces skills",
                "description": "Sync trace skills into IDE customization roots.",
                "side_effects": "mutating",
            },
            "sync-mcp": {
                "usage": "agents-traces sync-mcp",
                "description": "Inject MCP config into host IDEs.",
                "side_effects": "mutating",
            },
            "serve": {
                "usage": "agents-traces serve",
                "description": "Start FastMCP stdio server (mcp is an alias).",
                "side_effects": "mutating",
            },
        },
    }


def emit_help_json(argv: list[str], parser: argparse.ArgumentParser, name: str = "agents-trace") -> None:
    """Emit machine-readable CLI specification as JSON."""
    _ = argv, parser, name
    print(json.dumps(help_json(), indent=2))
