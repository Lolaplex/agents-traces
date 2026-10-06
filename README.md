<p align="center">
  <a href="https://github.com/Lolaplex/agents-traces/releases"><img src="https://img.shields.io/badge/version-0.1.0-blue.svg?style=flat-square" alt="Version 0.1.0"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-Standard-orange.svg?style=flat-square" alt="MCP"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.10+-3776AB.svg?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="https://pypi.org/project/agents-traces/"><img src="https://img.shields.io/pypi/v/agents-traces.svg?style=flat-square" alt="PyPI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg?style=flat-square" alt="License"></a>
</p>

<p align="center">
  <strong>Append-only JSONL session traces.</strong><br>
  One daily file. Assemble rebuilds the chat payload for one request.
</p>

## Quickstart

```bash
pip install agents-traces && agents-traces init
```

Creates `~/.agents/traces/`, copies skills, and merges MCP into host configs whose directories already exist.

> [!TIP]
> **🤖 Agent-Driven Setup:**
> Give your coding agent **this repo** (clone or URL), then tell it to **"install and set up agents-traces."**

Source checkouts can also be installed and managed with [vand](https://github.com/Lolaplex/vand).

---

## What it does

Each UTC day is one append-only file: `~/.agents/traces/YYYY-MM-DD.jsonl` (override `AGENTS_TRACES_DIR`). No daemon, no database. Append is a file write.

**Inspect.** MCP reads the same files an agent just wrote: timeline, recent errors, token and cost totals, per-model failure rates.

**Assemble.** `agents-traces assemble --session <id>` rebuilds chat-completions messages for that session from the JSONL. Tool bodies can be dropped; user turns stay.

**Session text.** `session_snap`, `session_grep`, and `session_tail` search message text in this store. They do not read [agents-memory](https://github.com/Lolaplex/agents-memory).

**Ingest.** `ingest` copies IDE transcripts (Antigravity, Cursor, Claude, Cline) into the daily files. Live sessions already on disk are not duplicated.

**Retention.** `cleanup` deletes files older than `--keep-days` (default 30). `--compress` gzips them instead.

Bare `agents-traces` (no subcommand) prints today's stats. The MCP server is `serve` or `mcp`, not the default.

---

## Where it runs

**Floor:** any MCP client or a terminal. `python -m agents_traces serve` is enough.

`init` autowires hosts it finds. It does not install an IDE.

| Layer | What you get | Who |
| --- | --- | --- |
| **JSONL + MCP/CLI** | Record, inspect, stats, assemble | Any MCP host / any shell |
| **Autowire on init** | Merge `agents-traces` into the host MCP file when that config directory already exists; copy `trace-inspect` and `trace-stats` | Cursor, Claude Desktop, Zed (`context_servers`), Antigravity / Gemini, Windsurf, Codex, Roo, Cline. Skills land in `~/.agents/skills`, `~/.cursor/skills`, `~/.gemini/config/skills` |

**MCP without autowire:** point the host at `python -m agents_traces serve`.

---

## MCP tools

Primary surface. Agents diagnose a failed loop here.

| Tool | What it does |
| :--- | :--- |
| `get_last_session_trace` | Timeline for one session, or the latest. Optional `trace:` prefix on the id |
| `get_recent_errors` | Failed tool calls with args and stack |
| `get_session_stats` | Tokens, estimated cost, tool success for the last N days |
| `get_model_stats` | Same window, split by model, with failure rates |
| `record_trace` | Append one event |
| `ingest_traces` | Pull IDE transcripts into the daily files |
| `session_snap` | Recent user messages in the traces store |
| `session_grep` | Search those messages |
| `session_tail` | Last lines for one session, or the latest |
| `trace_seal` | Cryptographically seal tool calls into a SHA-256 hash chain |
| `trace_verify` | Verify cryptographic integrity of a sealed hash chain |
| `trace_audit` | Audit for non-determinism, redundant calls, and scope violations |

Twelve tools. Full contract: [`abi/MCP.md`](abi/MCP.md).

---

## CLI

Ops / install / batch. Machine-readable catalog: `python -m agents_traces --help-json` (do not scrape `--help`).

| Command | Purpose |
| --- | --- |
| `agents-traces init` | Traces dir, skills, MCP merge |
| `agents-traces stats [--date YYYY-MM-DD \| --days N] [--by-model] [--json]` | Tokens, cost, tool rates. No flags = today |
| `agents-traces analyze-models [--days N]` | Stats split by model (default 7 days) |
| `agents-traces inspect [session-id]` | Terminal timeline. No id = latest session |
| `agents-traces sessions [--days N] [--limit N]` | Recent sessions with token and cost totals |
| `agents-traces tail [-n N] [-f] [--raw]` | End of today's file. `-f` follows |
| `agents-traces record …` | Append one event from a shell |
| `agents-traces assemble --session ID [--limit N] [--days N] [--include-tools]` | Rebuild chat messages for that session |
| `agents-traces ingest` | IDE transcripts → daily JSONL |
| `agents-traces cleanup [--keep-days N] [--compress]` | Delete old files, or gzip them |
| `agents-traces seal [session\|file] [-o out]` | Seal tool calls into SHA-256 hash chain |
| `agents-traces verify <file>` | Verify cryptographic hash chain integrity |
| `agents-traces audit [session\|file] [--scope file]` | Replay audit for non-determinism & redundancy |
| `agents-traces serve` / `mcp` | stdio MCP. Not the default command |
| `agents-traces skills` / `sync-mcp` | Re-copy skills, or re-merge MCP |

---

## ABI

Layout and contracts in [`abi/`](abi/):

- [`WHY.md`](abi/WHY.md) — JSONL instead of a tracing stack
- [`LAYOUT.md`](abi/LAYOUT.md) — daily files
- [`SCHEMA.md`](abi/SCHEMA.md) — event fields
- [`MCP.md`](abi/MCP.md) — tool surface
- [`CLI.md`](abi/CLI.md) — commands

---

## Tests

```bash
python tests/run_all_tests.py
```

---

## License

MIT. See [LICENSE](LICENSE).
