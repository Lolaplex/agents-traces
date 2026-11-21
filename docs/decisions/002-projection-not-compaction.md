# 002 Prompt is a projection, not a compaction

Do not concatenate full turn bodies into chat/completions. Do not LLM-summarize the log.

Traces stay append-only and complete. Assemble emits a bounded working set of frozen previews (768 chars). Overflow is `<ref digest=…>` into that event.

Old large tool/command stdio is evicted first, in place: the tool message stays (API pairing), the body becomes `<stdio evicted digest chars/>`. The latest tool round keeps a preview. Small results (errors, "ok") stay. User/assistant text is not dropped to make room for an old dump. Once stubbed, the stub is byte-stable.

Facts that must outlive the working set are memory tools, not a digest of the chat.
