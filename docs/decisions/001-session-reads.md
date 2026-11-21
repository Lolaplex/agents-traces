# 001 Session reads live here; harness ingest is the writer

`session_view.py` and MCP `session_snap` / `session_grep` / `session_tail` read `~/.agents/traces` message events. Product jsonl is never opened from this package except by `ingest`. Memory may wrap the same functions for baton + existing tool names.

Harness `traces_ingest` is the scheduled writer. Live interceptor remains process-scoped (`proc-*`). Ingested chats keep `cursor-<uuid>` / `agy-*`. One conversation exists once: drop ingested twins of live tool fingerprints.

`agents-brain` is a later bundle, not a merge of this store with memory.
