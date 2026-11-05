# Event Schema Specification

Every trace event in `~/.agents/traces/YYYY-MM-DD.jsonl` is a single JSON object.

## Common Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `ts` | string | Yes | ISO 8601 UTC timestamp (e.g. `"2026-08-20T03:15:22Z"`). |
| `session` | string | Yes | Session identifier. |
| `type` | string | Yes | `tool_call`, `llm_call`, `file_edit`, `error`, `session_start`, `session_end`, `message`, or `custom`. |
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

### 5. `message`
One chat turn. `metadata.role` is `user`, `assistant`, `system`, or `tool`. `metadata.content` is the text.

```json
{
  "ts": "2026-08-20T03:15:40Z",
  "session": "s-8f92a",
  "type": "message",
  "metadata": {
    "role": "user",
    "content": "hello",
    "digest": "a1b2c3d4e5f6",
    "sha256": "<64 hex>",
    "chars": 5
  }
}
```

`record_message` always stores `digest` (first 12 hex chars of SHA-256), `sha256`, and `chars` for the full text. Older lines may omit them.

### 6. `session_start` & `session_end`
Demarcate run boundaries.
```json
{"ts": "2026-08-20T03:15:00Z", "session": "s-8f92a", "type": "session_start", "model": "gemini-3.7-flash"}
{"ts": "2026-08-20T03:15:35Z", "session": "s-8f92a", "type": "session_end"}
```

---

## Retained content

Message text, tool `result`, `error`, and `stack` stay inline while they are at most 20,000 characters. Above that, the JSONL field keeps a 20,000-character prefix and the full UTF-8 body is stored at `content/<sha256[:2]>/<sha256>`.

The matching metadata keys (prefix `result_`, `error_`, or `stack_` for those fields; no prefix for `metadata.content`):

| Key | Meaning |
|---|---|
| `sha256` | SHA-256 hex of the full text |
| `digest` | First 12 hex characters. This is the `<ref digest>` value |
| `chars` | Original length in Unicode code points |
| `truncated` | `true` when the JSONL field is only the prefix |

Prompt previews that do not fit carry the same facts: `<ref digest="…" sha256="…" chars="…" truncated="1"/>`. That marker is an address. It is not a summary of the missing text.

`read_content(session, digest, offset=0, limit=None)` loads the body for that session, checks the digest against the full bytes, then returns the character window. `offset` and `limit` are code-point indexes. A mismatch raises `DigestMismatchError`. Lines written by older versions are still readable: the call returns the text that was actually stored and checks the digest against those bytes. Characters dropped before this layout existed are not recoverable.
