# Event Schema Specification

Every trace event in `~/.agents/traces/YYYY-MM-DD.jsonl` is a single JSON object.

## Common Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `ts` | string | Yes | ISO 8601 UTC timestamp (e.g. `"2026-08-20T03:15:22Z"`). |
| `session` | string | Yes | Session identifier. |
| `type` | string | Yes | `tool_call`, `llm_call`, `file_edit`, `error`, `session_start`, `session_end`, or `custom`. |
| `origin` | string | No | `live` or `ingested`. Omitted on older lines. |

---

## Event Types & Payloads

### 1. `tool_call`
Recorded whenever the agent invokes a tool or terminal command.
```json
{
  "ts": "2026-08-20T03:15:22Z",
  "session": "s-8f92a",
  "type": "tool_call",
  "tool": "search_docs",
  "args": {"docset": "mcp", "query": "FastMCP"},
  "duration_ms": 12.4,
  "status": "ok",
  "result": "..."
}
```

### 2. `llm_call`
Recorded for inference tokens and estimated cost.
```json
{
  "ts": "2026-08-20T03:15:25Z",
  "session": "s-8f92a",
  "type": "llm_call",
  "model": "gemini-3.7-flash",
  "tokens_in": 1420,
  "tokens_out": 380,
  "cost_usd": 0.00042,
  "duration_ms": 620
}
```

### 3. `file_edit`
Tracks file modifications and diff metrics.
```json
{
  "ts": "2026-08-20T03:15:28Z",
  "session": "s-8f92a",
  "type": "file_edit",
  "file": "src/engine.py",
  "lines_added": 14,
  "lines_removed": 2
}
```

### 4. `error`
Tracks tool execution failures and exceptions.
```json
{
  "ts": "2026-08-20T03:15:30Z",
  "session": "s-8f92a",
  "type": "error",
  "tool": "run_command",
  "error": "UnicodeEncodeError on line 118",
  "stack": "Traceback..."
}
```

### 5. `session_start` & `session_end`
Demarcate run boundaries.
```json
{"ts": "2026-08-20T03:15:00Z", "session": "s-8f92a", "type": "session_start", "model": "gemini-3.7-flash"}
{"ts": "2026-08-20T03:15:35Z", "session": "s-8f92a", "type": "session_end"}
```
