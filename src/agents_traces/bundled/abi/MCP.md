# MCP Tool Surface Specification

The `agents-traces` server exposes the following FastMCP tools for AI coding agents:

## 1. `get_last_session_trace`
- **Description**: Retrieves the full chronological execution trace and formatted timeline of the current or most recent agent session.
- **Parameters**:
  - `session_id` (string, optional): Target session ID. If omitted, uses the latest active session.
  - `limit` (integer, optional): Maximum number of recent events to return (default: 40).
- **Returns**: Formatted ANSI/text timeline showing tool calls, durations, edits, and error points.

## 2. `get_recent_errors`
- **Description**: Query recent tool failures and exceptions across sessions.
- **Parameters**:
  - `limit` (integer, optional): Number of errors to retrieve (default: 10).
  - `session_id` (string, optional): Filter by specific session ID.
- **Returns**: JSON list of error events with tool arguments, error messages, and tracebacks.

## 3. `get_session_stats`
- **Description**: Computes aggregated resource usage, token counts, estimated costs, and tool success rates.
- **Parameters**:
  - `days` (integer, optional): Number of past days to aggregate (default: 1).
- **Returns**: JSON object containing session count, tokens in/out, estimated cost, and tool reliability metrics.

## 4. `record_trace`
- **Description**: Ingests an observability trace event into the daily append-only JSONL log.
- **Parameters**:
  - `session` (string): Session identifier.
  - `type` (string): Event type discriminator.
  - `tool`, `args`, `status`, `error`, `duration_ms`, `model`, `tokens_in`, `tokens_out`, `cost_usd`, `file`, `lines_added`, `lines_removed`, `metadata`.
- **Returns**: JSON status object confirming recorded event.
