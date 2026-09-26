# CLI

Installed command: `agents-traces`. Machine-readable catalog: `python -m agents_traces --help-json`.

No subcommand prints today's stats. It does not start the MCP server.

## Subcommands

- `agents-traces init` — create `~/.agents/traces/`, copy `trace-inspect` and `trace-stats`, merge MCP into host configs whose directories already exist.
- `agents-traces stats [--date YYYY-MM-DD | --days N] [--by-model] [--json]` — tokens, cost, tool rates. There is no `--today` flag.
- `agents-traces analyze-models [--days N] [--json]` — per-model breakdown. Default window is 7 days.
- `agents-traces inspect [session_id]` — terminal timeline. Omit the id for the latest session.
- `agents-traces sessions [--days N] [--limit N]` — recent sessions.
- `agents-traces tail [-n N] [-f] [--raw]` — end of today's file. `-f` follows. `--raw` prints JSONL.
- `agents-traces record --session … --type …` — append one event. Optional `--tool`, `--status`, `--error`, `--duration`, `--tokens-in`, `--tokens-out`, `--cost`, `--file`, `--data`.
- `agents-traces assemble --session ID [--limit N] [--days N] [--include-tools]` — rebuild chat-completions messages for that session.
- `agents-traces ingest` — IDE transcripts into the daily JSONL.
- `agents-traces cleanup [--keep-days N] [--compress]` — delete files older than N days (default 30), or gzip them when `--compress` is set.
- `agents-traces serve` / `agents-traces mcp` — FastMCP stdio server.
- `agents-traces skills` — copy skills again.
- `agents-traces sync-mcp` — merge MCP config again.
