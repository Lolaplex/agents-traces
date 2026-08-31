# AGENTS.md — agents-traces Developer Guidelines

## Architecture Principles
- **Zero Heavy Dependencies**: Pure Python standard library + `mcp>=1.0.0,<2`. No vector DBs, no Docker, no external daemons.
- **Append-Only JSONL**: Daily files `~/.agents/traces/YYYY-MM-DD.jsonl`. The one rewrite is `drop_sessions`: ingested product-jsonl twins of live MCP tool calls. Conversation bodies stay in the product folder, never markdown memory.
- **Identity directory**: `~/.agents/identity.json` maps aliases → user → active session. Not traces, not markdown. DID later.
- **Sub-0.05ms Append**: Direct atomic file append with zero lock contention.

## Commands
- Run test suite: `python tests/run_all_tests.py`
- Sync bundled assets: `python scripts/sync_bundled.py`
- Test CLI stats: `python -m agents_traces stats`
- Ingest vendor chats: `python -m agents_traces ingest` (Cursor / Antigravity / Claude product jsonl → traces; bodies stay in the product folder)
- Assemble the agent: `python -m agents_traces assemble --session ses_…` (or legacy `telegram-123`)
- Session reads: MCP `session_snap` / `session_grep` / `session_tail` (and `session_view.py`) read traces, not product jsonl
- Start FastMCP: `python -m agents_traces serve`
