# Why: Append-Only JSONL vs Bloated Observability Stacks

## The Problem with Current Agent Observability (LangSmith, Langfuse, Arize Phoenix)

1. **Massive Infrastructure Bloat**: Running them locally requires Docker monsters running PostgreSQL, ClickHouse, Redis, OpenTelemetry collectors, and Node.js/Next.js web dashboards.
2. **SaaS Vendor Lock-in**: Cloud alternatives require API keys, monthly subscriptions, and introduce network latency to every tool execution.
3. **Synchronous Blocking Overhead**: Intrusive SDK wrappers hook into async loops, causing race conditions, timeouts, and memory leaks.
4. **Opaque Proprietary Schemas**: Storing logs in relational or columnar databases prevents developers from easily inspecting, grepping, or piping traces in terminal.

## The agents-traces Solution

1. **Zero-Daemon, Zero-Database, Zero-Cloud**: Traces are written directly to disk as append-only newline-delimited JSON (`~/.agents/traces/YYYY-MM-DD.jsonl`).
2. **Sub-0.05ms Write Latency**: Direct Python file append mode (`open(..., "a")`) introduces negligible overhead (<0.05ms).
3. **Streaming Generator Processing**: Pythons lazy file readers process 1,000,000 log lines in under 0.2s using less than 8 MB of RAM.
4. **Agentic Self-Diagnosis**: FastMCP tools (`get_last_session_trace`, `get_recent_errors`) allow coding agents to inspect their own tool execution history and fix errors autonomously.
5. **Unix Philosophy**: Everything can be inspected with standard CLI tools (`grep`, `jq`, `tail -f`, `agents-traces inspect`).
