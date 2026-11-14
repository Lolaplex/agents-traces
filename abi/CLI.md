# CLI

Installed command: `agents-traces`. Machine-readable catalog: `python -m agents_traces --help-json`.

No subcommand prints today's stats. It does not start the MCP server.

## Subcommands

- `agents-traces init` — create `<ws>/traces/`, copy `trace-inspect` and `trace-stats`, merge MCP into host configs whose directories already exist.
- `agents-traces stats [--date YYYY-MM-DD | --days N] [--by-model] [--json]` — tokens, cost, tool rates. There is no `--today` flag.
- `agents-traces analyze-models [--days N] [--json]` — per-model breakdown. Default window is 7 days.
- `agents-traces inspect [session_id]` — terminal timeline. Omit the id for the latest session.
- `agents-traces sessions [--days N] [--limit N]` — recent sessions.
- `agents-traces tail [-n N] [-f] [--raw]` — end of today's file. `-f` follows. `--raw` prints JSONL.
- `agents-traces record --session … --type …` — append one event. Optional `--tool`, `--status`, `--error`, `--duration`, `--tokens-in`, `--tokens-out`, `--cost`, `--file`, `--data`.
- `agents-traces assemble --session ID [--limit N] [--days N] [--include-tools]` — rebuild chat-completions messages for that session.
- `agents-traces export [--session ID] [--format markdown|json] [--output path] [--no-tools] [--no-frames] [--days N]` — write that session's chat (messages, tool calls, compaction frames) from the traces. Bodies are the retained text. Omit the session for the latest. `--format` accepts `markdown`, `md`, or `json`. Exit 1 when the session has no events.
- `agents-traces read --session ID --digest HEX [--offset N] [--limit N] [--json]` — fetch retained message or tool text. Checks the digest, then applies the character window. Exit 1 on mismatch or miss. `--json` prints the window object (or `{"error": "digest_mismatch"|"not_found"|...}`).
- `agents-traces ingest` — IDE transcripts into the daily JSONL.
- `agents-traces cleanup [--keep-days N] [--compress]` — delete files older than N days (default 30), or gzip them when `--compress` is set.
- `agents-traces seal [session_or_path] [--output file] [--json]` — cryptographically seal tool calls into a SHA-256 hash chain starting at genesis.
- `agents-traces verify <path> [--json]` — verify cryptographic integrity of a sealed hash chain.
- `agents-traces audit [session_or_path] [--scope file] [--json]` — replay tool calls to detect non-determinism, redundant calls, and scope violations.
- `agents-traces replay …` — alias for `audit`.
- `agents-traces serve` / `agents-traces mcp` — FastMCP stdio server.
- `agents-traces skills` — copy skills again.
- `agents-traces sync-mcp` — merge MCP config again.
