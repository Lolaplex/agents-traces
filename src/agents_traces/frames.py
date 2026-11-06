"""Typed context frames — non-destructive compaction.

A ``frame`` event replaces a span of earlier events when a prompt is
assembled, while the covered events stay in the store untouched. This
mirrors the per-node summarization model used by Devin's ``message_nodes``
metadata (``summarized_from``, ``num_tokens_preceding``): the summary is a
view, the raw chain keeps provenance.

Frame payload lives in ``metadata``:

- ``kind``: ``compaction`` (default), extensible (``milestone``, ``note``)
- ``summarized_from``: ``{"first_ts", "last_ts", "count"}`` — events have no
  ids, so the provenance link is a ts range
- ``tokens_preceding``: chars/4 estimate of covered event payloads
- ``content``: the text replayed into the assembled prompt
- ``source``: who wrote the frame (``deterministic`` or a model name)
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .models import TraceEvent
from .store import TraceStore

FRAME_TYPE = "frame"
KIND_COMPACTION = "compaction"


def is_frame(event: TraceEvent) -> bool:
    return event.type == FRAME_TYPE


def frame_meta(event: TraceEvent) -> dict[str, Any]:
    md = event.metadata if isinstance(event.metadata, dict) else {}
    return md


def _estimate_tokens(events: Iterable[TraceEvent]) -> int:
    # chars/4 over the serialized payload — an accounting estimate, not a
    # tokenizer; kept deliberately honest about its precision.
    total = 0
    for e in events:
        try:
            total += len(e.to_json())
        except Exception:
            pass
    return total // 4


def deterministic_content(events: list[TraceEvent]) -> str:
    """Skeleton summary of a covered span: counts + span + grep anchor."""
    from .prompt import content_digest

    roles: dict[str, int] = {}
    types: dict[str, int] = {}
    texts: list[str] = []
    first = last = ""
    for e in events:
        types[e.type] = types.get(e.type, 0) + 1
        md = e.metadata if isinstance(e.metadata, dict) else {}
        role = str(md.get("role") or "")
        if role:
            roles[role] = roles.get(role, 0) + 1
        content = str(md.get("content") or "")
        if content:
            texts.append(content)
        if e.ts:
            if not first or e.ts < first:
                first = e.ts
            if not last or e.ts > last:
                last = e.ts
    role_bits = " ".join(f"{r}:{n}" for r, n in sorted(roles.items()))
    type_bits = " ".join(f"{t}:{n}" for t, n in sorted(types.items()))
    digest = content_digest("\n".join(texts))[:12]
    return (
        f"[compacted {sum(types.values())} events · {type_bits}"
        + (f" · {role_bits}" if role_bits else "")
        + f" · {first}→{last} · ref {digest}]"
    )


def compact_session(
    session: str,
    *,
    store: Optional[TraceStore] = None,
    keep_last: int = 6,
    content: str = "",
    source: str = "deterministic",
    kind: str = KIND_COMPACTION,
    days: int = 90,
) -> Optional[TraceEvent]:
    """Append a frame covering all session events before the newest ``keep_last``.

    ``content`` overrides the replayed text (e.g. an LLM summary written by
    the caller); empty means the deterministic skeleton. Frame events are not
    counted toward ``keep_last`` and never cover themselves. Returns None when
    there is nothing new to compact.
    """
    target = store or TraceStore()
    events = [
        e
        for e in target.iter_events(
            files=target.list_trace_files(days=days), session_id=session
        )
    ]
    events.sort(key=lambda e: e.ts or "")
    if not events:
        return None

    body = [e for e in events if not is_frame(e)]
    if len(body) <= keep_last:
        return None
    covered = body[: len(body) - keep_last]

    # Only events after the newest existing frame are new coverage.
    prior_last = ""
    for e in events:
        if is_frame(e):
            rng = frame_meta(e).get("summarized_from") or {}
            lt = str(rng.get("last_ts") or "")
            if lt > prior_last:
                prior_last = lt
    covered = [e for e in covered if not prior_last or (e.ts or "") > prior_last]
    if not covered:
        return None

    frame = TraceEvent(
        session=session,
        type=FRAME_TYPE,
        ts=covered[-1].ts,
        metadata={
            "kind": kind,
            "summarized_from": {
                "first_ts": covered[0].ts,
                "last_ts": covered[-1].ts,
                "count": len(covered),
            },
            "tokens_preceding": _estimate_tokens(covered),
            "content": content or deterministic_content(covered),
            "source": source,
        },
    )
    target.append(frame)
    return frame
