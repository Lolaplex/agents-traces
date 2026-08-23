# agents-traces

<p align="left">
  <a href="https://github.com/Lolaplex/agents-traces/releases"><img src="https://img.shields.io/badge/version-0.42.0-blue.svg?style=flat-square" alt="Version 0.42.0"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-Standard-orange.svg?style=flat-square" alt="MCP"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.10+-3776AB.svg?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="https://pypi.org/project/agents-traces/"><img src="https://img.shields.io/pypi/v/agents-traces.svg?style=flat-square" alt="PyPI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg?style=flat-square" alt="License"></a>
</p>

**Ultra-fast, zero-bloat local JSONL observability and tracing for AI coding agents.**  
Sub-millisecond event streaming, cost/token metrics, and autonomous error self-healing. Shared across **Cursor**, **Claude Code**, **Antigravity**, and **Zed**.

---

## Quickstart

### 1-Step Setup

```bash
pip install agents-traces && agents-traces init
```

Scaffolds `~/.agents/traces/`, autowires MCP configurations into your installed IDEs, and registers assistant skills.

### 2. Agent-Driven Setup (Zero Friction)

> [!TIP]
> **🤖 Agent-Driven Setup (Zero Friction):**  
> Simply tell your coding agent: **"Install and set up agents-traces for me."**  
> The agent installs the package, runs `agents-traces init`, and automatically diagnoses tool failures using `get_recent_errors`.

*Source checkouts can also be installed and managed using [vand](https://github.com/Lolaplex/vand).*

---

## Why `.agents/traces`?

Existing observability platforms (LangSmith, Langfuse, Arize Phoenix) require:
- Heavy Docker stacks (PostgreSQL, ClickHouse, Redis, webservers)
- Cloud accounts, API keys, network latency, and SaaS subscriptions
- Intrusive SDK wrappers that add execution overhead to fast agent loops

**`agents-traces` replaces 500 MB containers with lightweight, append-only JSON lines (`~/.agents/traces/`):**
- **Sub-0.05ms Append Speed:** Pure atomic file appends with zero lock contention.
- **Zero Daemon / Zero Database:** Plain daily JSONL logs (`~/.agents/traces/YYYY-MM-DD.jsonl`).
- **Autonomous Self-Healing:** Exposes MCP tools (`get_last_session_trace`, `get_recent_errors`) so agents can inspect their own errors and fix failing loops.
- **Rich Terminal Timeline:** LangSmith-style visual timeline directly in your terminal.

---

## Architecture & Flow

```text
 ┌─────────────────────────────────────────────────────────────┐
 │                     CODING AGENT / IDE                      │
 │    Cursor · Antigravity · Claude Code · Custom Agents       │
 └──────────────────────────────┬──────────────────────────────┘
                                │  Append in <0.05ms
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │                  LOCAL DAILY TRACE STORE                    │
 │          ~/.agents/traces/YYYY-MM-DD.jsonl                  │
 └──────────────┬───────────────────────────────┬──────────────┘
                │                               │
                ▼                               ▼
 ┌─────────────────────────────┐ ┌─────────────────────────────┐
 │     TERMINAL TIMELINE       │ │     MCP SELF-HEALING        │
 │  agents-traces stats        │ │  get_last_session_trace     │
 │  agents-traces inspect      │ │  get_recent_errors          │
 │  agents-traces tail -f      │ │  Autonomous Loop Repair     │
 └─────────────────────────────┘ └─────────────────────────────┘
```

---

## CLI Reference

| Command | Purpose |
|---------|---------|
| `agents-traces init` | Plug & Play setup: auto-configures MCP in Cursor, Antigravity, Claude Desktop, Zed |
| `agents-traces stats` | Displays aggregated tokens, costs, tool success/error breakdown |
| `agents-traces stats --days 7` | Aggregates token and tool metrics across the past 7 days |
| `agents-traces inspect [session-id]` | Renders a rich terminal execution timeline for a session |
| `agents-traces sessions` | Lists recent active coding sessions with token/cost summaries |
| `agents-traces tail -f` | Live streams real-time trace events in your terminal |
| `agents-traces cleanup --keep-days 30` | Compresses (gzip) or purges traces older than $N$ days |
| `agents-traces serve` | Runs the FastMCP stdio server (default) |

---

## MCP Tools Reference

| Tool | Parameters | Description |
| :--- | :--- | :--- |
| `get_last_session_trace` | `session_id` (optional), `limit` (default: `40`) | Returns the complete chronological execution timeline of tools, model calls, and file edits. |
| `get_recent_errors` | `limit` (default: `10`), `session_id` (optional) | Retrieves recent failed tool calls, error stack traces, and arguments for agent self-repair. |
| `get_session_stats` | `days` (default: `1`) | Aggregates token consumption, estimated USD costs, and tool success rates. |
| `record_trace` | `session`, `type`, `tool`, `status`, `error`, `duration_ms` | Programmatically logs custom trace events into the local JSONL store. |

---

## Supported Ecosystem

- **Claude Code:** Bound via MCP server and `.agents/skills/trace-inspect`.
- **Google Antigravity:** Integrated via `.gemini/config` rules and `agents-traces` MCP.
- **Cursor:** Automatically configures `.cursor/mcp.json` and agent rules.
- **Zed:** Configures `context_servers` and mirrors assistant skills.
- **VS Code / Windsurf:** Autowires Cline / Roo-Code MCP configuration.

---

## Open ABI Specification

Detailed architectural specifications live in [`abi/`](abi/):
- [`abi/WHY.md`](abi/WHY.md) — Rationale & why lightweight JSONL beats heavy Docker stacks.
- [`abi/LAYOUT.md`](abi/LAYOUT.md) — Daily partition taxonomy in `~/.agents/traces/`.
- [`abi/SCHEMA.md`](abi/SCHEMA.md) — Full JSON schema for trace events and spans.
- [`abi/MCP.md`](abi/MCP.md) — Tool surface definitions and schemas.
- [`abi/CLI.md`](abi/CLI.md) — Terminal commands and formatting specification.

---

## Testing & Verification

Run the test suite across all engines:

```bash
python tests/run_all_tests.py
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
