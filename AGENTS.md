# AGENTS.md — agents-traces Developer Guidelines

## Architecture Principles
- **Zero Heavy Dependencies**: Pure Python standard library + `mcp>=1.0.0,<2`. No vector DBs, no Docker, no external daemons.
- **Append-Only JSONL**: All traces reside in `~/.agents/traces/YYYY-MM-DD.jsonl`. Plain JSON lines, human-readable, fast streaming.
- **Sub-0.05ms Append**: Direct atomic file append with zero lock contention.

## Commands
- Run test suite: `python tests/run_all_tests.py`
- Sync bundled assets: `python scripts/sync_bundled.py`
- Test CLI stats: `python -m agents_traces stats`
- Start FastMCP: `python -m agents_traces serve`
