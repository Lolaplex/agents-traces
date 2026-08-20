# CLI Specification

`agents-traces` provides the following CLI subcommands:

## Subcommands

- `agents-traces init`: Plug & Play setup: auto-configure MCP servers across Cursor, Antigravity, Claude Desktop, and Zed, and sync trace skills.
- `agents-traces stats [--today | --date YYYY-MM-DD | --days N | --json]`: Aggregated token usage, costs, tool reliability, and top errors.
- `agents-traces inspect [session_id]`: Render colored execution timeline for a session.
- `agents-traces sessions [--days N] [--limit N]`: List recent sessions with durations and token stats.
- `agents-traces tail [-n N] [-f] [--raw]`: Live streaming trace viewer.
- `agents-traces record --session ... --type ...`: Record a trace event from shell/scripts.
- `agents-traces cleanup [--keep-days 30] [--compress]`: Maintain and purge old trace files.
- `agents-traces serve` / `agents-traces mcp`: Start the FastMCP stdio server.
- `agents-traces skills`: Sync trace skills into IDE customization roots.
- `agents-traces sync-mcp`: Auto-inject MCP config into installed IDEs.
