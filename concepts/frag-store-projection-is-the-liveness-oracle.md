---
type: belief
id: frag-store-projection-is-the-liveness-oracle
provenance: R10 review (agents-traces#10) — a `local_n >= len(remote)` early return in `_merge_store_msgs` silently resurrected /undo-ed turns and dropped a peer's newer turns
ts: 2026-11-24
---

Belief: when merging local trace JSONL with the synced `msg:*`
projection in `store.db`, the projection is the cross-host liveness
oracle — its tombstones, not any count, decide which locally cached
turns still exist.

A count comparison can never see `msg:deleted='true'` or an active
`frame:*` coverage span: a peer's `/undo` shrinks the remote row count
below the local count, and the shortcut then replays the undone turns
while dropping the peer's newer ones. The merge always reads the
projection's dead rows and active span list first, suppresses matching
local events (including locally held frames a newer synced frame
retired), and only then unions in the live remote rows.

Dedupe identity is the turn — `(ts, role)` — with a
redaction-normalized body fallback for peer clock skew. The body hash
alone is wrong in both directions: it collapses legitimate repeats
("ok" sent twice at different ts) and misses path-redacted twins
(klanker stores `~/...` where the trace kept `$HOME/...`).
