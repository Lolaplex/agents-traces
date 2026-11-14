# Event Schema Specification

Every trace event in `<ws>/traces/YYYY-MM-DD.jsonl` is a single JSON object.

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

---

## Redaction

`TraceStore.append` redacts secrets before it writes the JSONL line or a content blob. Surrounding text stays. The removed value is `[redacted]`. Digests (`sha256`, `digest`, `chars`) are recomputed from the redacted text when those keys are already present and the field was not already truncated.

Built-in coverage:

- Token shapes with a stable vendor prefix (`sk-`, `ghp_`, `github_pat_`, `glpat-`, `xox[baprs]-`, `AKIA`/`ASIA`, `AIza`, `sk_live_`/`sk_test_`, `hf_`, `npm_`, `eyJ` JWTs, PEM private keys, and a few others of the same kind). A fixed-length prefix such as `ghp_` plus 36 letters is removed even with no digit. Variable-length prefixes still require a digit, so `sk-implementation-details` stays.
- `Authorization` and `Proxy-Authorization` values (`Bearer`, `Basic`, `Token`), and a long bare `Bearer` credential.
- `user:password@host` in URLs. The user and host stay.
- Assignments and JSON fields whose key is a secret name: `password`, `passwd`, `passphrase`, `pwd`, `secret`, `token`, `api_key`, `access_token`, `private_key`, `client_secret`, `authorization`, and the same name with a prefix (`x-api-key`, `aws_secret_access_key`). A secret key's value is replaced entirely, including nested fields. `authorization` is only removed when the value looks like a credential, so `authorization: enabled` stays.
- Uppercase environment assignments whose name marks the value secret. A component of `SECRET`, `PASSWORD`, `TOKEN`, `API_KEY`, `ACCESS_KEY`, or `PRIVATE_KEY` counts (`GITHUB_TOKEN`, `OPENAI_API_KEY`, `AWS_SECRET_ACCESS_KEY`), including a short value. `TOKEN_COUNT` and `MAX_TOKENS` do not. A bare `TOKEN` does not. The same words in lowercase follow the key rules above (`api_key=example` stays; `"api_key": "example"` does not).
- An unquoted value that is one ordinary dictionary word is kept, so `password: use a manager` stays. `password: hunter2` and `pwd=abc` are still removed.

Free-text passwords that are not an assignment, header, env value, or known token shape are left as written.

Extra patterns are regular expressions; the whole match is replaced. Pass them to `TraceStore(..., extra_patterns=[...])` or `configure_redaction(extra_patterns=[...])`. `AGENTS_TRACE_REDACT_EXTRA` is a JSON array of strings, read when an event is written. A pattern that does not compile, passed to the store or `configure_redaction`, raises `ValueError` and the message does not include the pattern. Patterns from the environment that do not compile are skipped. A pattern is also skipped, and not logged, when it is longer than 200 characters, nests a quantifier, repeats an unbounded `.*` or `.+`, or matches the empty string. Extra patterns scan only the first 65536 characters. If the `regex` package is installed, each extra pattern has a timeout; otherwise the stdlib `re` engine is used and has none. `AGENTS_TRACE_REDACT=0` disables the default redactor. `TraceStore(..., redact=False)` disables it for that store.

Lines already on disk are not rewritten.
