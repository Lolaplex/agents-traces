# MCP surface

The Python server in this repository (`python -m agents_traces serve`, FastMCP) is the live tool list. The installed command is `agents-traces` (plural).

## Tools

### `get_last_session_trace(session_id=None, limit=40)`

Chronological timeline for one session. Omit `session_id` for the latest. A `trace:` prefix on the id is stripped. Returns the rendered timeline, not raw JSONL.

### `get_recent_errors(limit=10, session_id=None)`

Recent failed tool calls: tool, args, error, stack. Optional session filter. Same `trace:` prefix rule.

### `get_session_stats(days=1)`

Tokens, estimated USD, tool success for the last N UTC days. `days=1` is today.

### `get_model_stats(model=None, days=7)`

Same window, grouped by model, including failure rates. `model` is a substring filter. CLI mirror: `agents-traces analyze-models`.

### `record_trace(session, type, tool=None, args=None, status=None, error=None, duration_ms=None, model=None, tokens_in=None, tokens_out=None, cost_usd=None, file=None, lines_added=None, lines_removed=None, metadata=None)`

Append one event to today's JSONL. `type` is `tool_call`, `llm_call`, `file_edit`, `error`, `session_start`, `session_end`, or `custom`.

### `ingest_traces()`

Copy recent IDE transcripts (Antigravity, Cursor, Claude, Cline) into the daily files. Skips sessions already stored.

### `session_snap(limit=20)`

Recent user messages from this store (live MCP events and ingested chats). Not agents-memory.

### `session_grep(pattern, since="")`

Search those messages. `since` is optional.

### `session_tail(session_id="", limit=10)`

Last messages for one session id, or the latest lines when `session_id` is empty.
