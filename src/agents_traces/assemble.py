"""The agent is a trace: reconstruct chat-completions messages per request.

Conversation bodies live in ~/.agents/traces. Product jsonl stays in the
vendor folder. Memory holds pointers only. Binaries are referenced, not inlined
(multimodal / TTS / STT later).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .content import content_sha256, load_retained
from .identity import alias_id, legacy_session_id
from .models import TraceEvent
from .prompt import HISTORY_CHARS, PREVIEW_CHARS, collapse_old_stdio, preview_text, stamp_content
from .store import TraceStore


def session_id_for(channel: str, user: str | int) -> str:
    """Legacy session key (`telegram-42`). Prefer IdentityStore.resolve()."""
    return legacy_session_id(channel, user)


def _meta(event: TraceEvent) -> dict[str, Any]:
    return event.metadata if isinstance(event.metadata, dict) else {}


def _binary_suffix(event: TraceEvent) -> str:
    refs = _meta(event).get("binary_refs") or []
    if not isinstance(refs, list):
        return ""
    bits = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        path = ref.get("path") or ref.get("uri") or ""
        media = ref.get("media_type") or ref.get("type") or "application/octet-stream"
        if path:
            bits.append(f"[binary path={path} type={media}]")
    return " ".join(bits)


def _message_body(event: TraceEvent, store: Optional[TraceStore] = None) -> str:
    """Exact stored message text. The ref digest is this string, not a render."""
    md = _meta(event)
    text = str(md.get("content") or md.get("prompt") or "")
    if store is not None:
        text = load_retained(store.traces_dir, text, md, "")
    return text


def _fit_newest(
    messages: list[dict[str, Any]],
    *,
    max_n: int,
    max_chars: int,
) -> list[dict[str, Any]]:
    """Keep newest turns that fit. Drop oldest from the projection only.

    Call collapse_old_stdio first: old command output must not crowd out
    user/assistant text. Traces are not rewritten. No summary line.
    """
    acc: list[dict[str, Any]] = []
    used = 0
    for msg in reversed(messages):
        n = len(str(msg.get("content") or ""))
        if acc and max_n > 0 and len(acc) >= max_n:
            break
        if acc and max_chars > 0 and used + n > max_chars:
            break
        acc.append(msg)
        used += n
    acc.reverse()
    return acc


def _prev_session_from_traces(session: str, store: TraceStore) -> str:
    """Newest rollover link for a session, recorded by IdentityStore.resolve."""
    for e in store.iter_events(session_id=session, event_type="rollover"):
        md = e.metadata if isinstance(e.metadata, dict) else {}
        if md.get("prev_session"):
            return str(md["prev_session"])  # iter is newest-file-first
    return ""


_MERGE_TS_SKEW_SEC = 5.0


def _store_text_roots(traces_dir) -> list[str]:
    """Local roots klanker strips to ``~`` before projecting ``msg:text`` —
    this host's equivalents, so bodies compare equal across the sync."""
    roots = {str(Path.home())}
    for raw in (
        os.environ.get("AGENTS_HOST_DIR") or "",
        str(Path(traces_dir).parent) if traces_dir else "",
    ):
        if raw:
            roots.add(raw)
    return sorted((r for r in roots if len(r) > 3), key=len, reverse=True)


def _norm_store_text(text: str, roots: list[str]) -> str:
    for root in roots:
        text = text.replace(root, "~")
        if os.sep != "/":
            text = text.replace(root.replace(os.sep, "/"), "~")
    return text


def _ts_close(a: str, b: str) -> bool:
    """ISO timestamps within merge skew (peer clocks drift by seconds)."""
    if not a or not b:
        return False
    if a == b:
        return True
    try:
        da = datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return False
    return abs((da - db).total_seconds()) <= _MERGE_TS_SKEW_SEC


def _same_turn(
    ts: str,
    role: str,
    text: str,
    other: tuple[str, str, str],
) -> bool:
    """One turn, two projections: ``(ts, role)`` pins it; for minor peer
    clock skew the redaction-normalized body has to match too. Equal
    bodies at distant ts are distinct turns (``"ok"`` sent twice)."""
    ots, orole, otext = other
    if role != orole:
        return False
    if ts and ots and ts == ots:
        return True
    return bool(text) and text == otext and _ts_close(ts, ots)


def _merge_store_msgs(
    events: list[TraceEvent],
    session: str,
    store: TraceStore,
) -> list[TraceEvent]:
    """Union in live ``msg:*`` rows the local JSONL lacks — and drop
    locally cached turns the projection tombstoned.

    A peer's turns arrive via synced ``<ws>/store.db`` without the peer's
    traces. The projection is also the cross-host liveness oracle:
    ``msg:deleted='true'`` rows and ACTIVE ``frame:*`` ts-spans suppress
    local ``/undo``-ed or ``/compress``-covered copies that would
    otherwise resurrect (a count shortcut can never see that). Dedupe of
    the union is ``(ts, role)`` with a normalized-body fallback for skew,
    so repeated identical turns survive and ``~``-redacted synced bodies
    never duplicate their unredacted local twins.
    """
    try:
        from .storemsgs import session_projection

        proj = session_projection(session, traces_dir=store.traces_dir)
    except Exception:
        return events
    remote = proj.get("live") or []
    dead = proj.get("dead") or []
    spans = proj.get("frames") or []
    if not remote and not dead and not spans:
        return events

    from .frames import frame_meta, is_frame

    roots = _store_text_roots(store.traces_dir)
    dead_turns = [
        (
            str(d.get("ts") or ""),
            str(d.get("role") or "user").lower(),
            _norm_store_text(str(d.get("text") or ""), roots),
        )
        for d in dead
    ]

    out: list[TraceEvent] = []
    local_turns: list[tuple[str, str, str]] = []
    for e in events:
        ets = e.ts or ""
        if is_frame(e):
            # An active remote span over this frame's ts means a newer
            # synced frame retired it — its summary must not replay.
            if any(first <= ets <= last for first, last in spans):
                continue
            out.append(e)
            content = str(frame_meta(e).get("content") or "")
            local_turns.append(
                (ets, "system", _norm_store_text(content, roots)))
            continue
        if e.type == "message":
            role = str(_meta(e).get("role") or "user").lower()
            body = _norm_store_text(_message_body(e, store), roots)
            if any(first <= ets <= last for first, last in spans):
                continue  # covered by a synced frame the local log lacks
            if any(_same_turn(ets, role, body, d) for d in dead_turns):
                continue  # tombstoned by a peer's /undo
            local_turns.append((ets, role, body))
        out.append(e)

    for m in remote:
        mts = str(m.get("ts") or "")
        mrole = str(m.get("role") or "user").lower()
        mtext = str(m.get("text") or "")
        mnorm = _norm_store_text(mtext, roots)
        if any(_same_turn(mts, mrole, mnorm, t) for t in local_turns):
            continue
        md: dict[str, Any] = {
            "role": m["role"],
            "content": mtext,
            "sha256": content_sha256(mtext),
            "chars": len(mtext),
            "source": "store",
        }
        if m.get("persona"):
            md["persona"] = m["persona"]
        if m.get("user"):
            md["user"] = m["user"]
        ev = TraceEvent(session=session, type="message", origin="store",
                        metadata=md)
        if mts:
            ev.ts = mts
        out.append(ev)
        local_turns.append((mts, mrole, mnorm))
    return out


def assemble_messages(
    session: str,
    *,
    store: Optional[TraceStore] = None,
    limit: int = 24,
    days: int = 30,
    include_tools: bool = False,
    include_prev_session: bool = True,
) -> list[dict[str, Any]]:
    """Rebuild a prompt projection, not the log.

    Traces stay append-only and complete. Each turn in the prompt is a frozen
    preview; overflow is a <ref digest=…> into this session, not an LLM summary.
    """
    target = store or TraceStore()
    files = target.list_trace_files(days=days)
    wanted = {"message", "frame"} | ({"tool_call"} if include_tools else set())
    all_events = [
        e
        for e in target.iter_events(files=files, session_id=session)
        if e.type in wanted
    ]
    if len(all_events) < limit and days is not None:
        # Sparse recent history: widen only across files not yet scanned.
        scanned = set(files)
        all_files = target.list_trace_files()
        extra_files = [f for f in all_files if f not in scanned]
        if extra_files:
            all_events.extend(
                e
                for e in target.iter_events(files=extra_files, session_id=session)
                if e.type in wanted
            )
        files = all_files
    if include_prev_session and len(all_events) < limit and store is None:
        try:
            prev_sid = _prev_session_from_traces(session, target)
            if prev_sid:
                prev_events = [
                    e
                    for e in target.iter_events(files=files, session_id=prev_sid)
                    if e.type == "message" or e.type == "frame" or (include_tools and e.type == "tool_call")
                ]
                all_events = prev_events + all_events
        except Exception:
            pass
    all_events = _merge_store_msgs(all_events, session, target)
    all_events.sort(key=lambda e: e.ts or "")

    # Compaction frames replace the events they cover (see frames.py).
    from .frames import frame_meta, is_frame

    covered: list[tuple[str, str, int]] = []  # (first_ts, last_ts, covering frame)
    for e in all_events:
        if is_frame(e):
            rng = frame_meta(e).get("summarized_from") or {}
            if rng.get("first_ts") and rng.get("last_ts"):
                covered.append((str(rng["first_ts"]), str(rng["last_ts"]), id(e)))

    events = []
    for e in all_events:
        ts = e.ts or ""
        if any(first <= ts <= last and id(e) != cover for first, last, cover in covered):
            continue
        events.append(e)

    out: list[dict[str, Any]] = []
    for event in events:
        if is_frame(event):
            content = str(frame_meta(event).get("content") or "")
            if content:
                out.append({"role": "system", "content": stamp_content(event.ts or "", content)})
            continue
        if event.type == "message":
            role = str(_meta(event).get("role") or "user").lower()
            if role not in ("user", "assistant", "system", "tool"):
                role = "user"
            body = _message_body(event, target)
            suffix = _binary_suffix(event)
            if not body.strip() and not suffix:
                continue
            content = (
                preview_text(
                    body,
                    session=session,
                    ts=event.ts or "",
                    max_chars=PREVIEW_CHARS,
                )
                if body
                else ""
            )
            if suffix:
                content = f"{content}\n{suffix}" if content else suffix
            content = stamp_content(event.ts or "", content)
            out.append({"role": role, "content": content})
        elif include_tools and event.type == "tool_call":
            name = event.tool or "tool"
            args = event.args if isinstance(event.args, dict) else {}
            result = event.result
            out.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {"name": name, "arguments": args},
                        }
                    ],
                }
            )
            if result is not None:
                inline = result if isinstance(result, str) else str(result)
                full_result = load_retained(target.traces_dir, inline, _meta(event), "result_")
                out.append(
                    {
                        "role": "tool",
                        "content": preview_text(
                            full_result,
                            session=session,
                            ts=event.ts or "",
                            max_chars=PREVIEW_CHARS,
                        ),
                    }
                )
    out = collapse_old_stdio(out)
    return _fit_newest(out, max_n=limit, max_chars=HISTORY_CHARS)


def record_message(
    session: str,
    role: str,
    content: str,
    *,
    store: Optional[TraceStore] = None,
    channel: str = "",
    user: str = "",
    user_id: str = "",
    project: str = "",
    workspace: str = "",
    persona: str = "",
    binary_refs: Optional[list[dict[str, str]]] = None,
    origin: str = "live",
) -> TraceEvent:
    """Append one chat turn. Provenance of who engaged lives on the event.

    The full text is kept. Bodies over the inline cap leave a prefix on the
    event plus digest, sha256, chars, and truncated; the tail is an artifact.
    ``digest`` is always the hash of the full text.
    """
    target = store or TraceStore()
    body = content or ""
    sha = content_sha256(body)
    metadata: dict[str, Any] = {
        "role": role,
        "content": body,
        "digest": sha[:12],
        "sha256": sha,
        "chars": len(body),
        "source": channel or "local",
    }
    if project:
        metadata["project"] = project
    if channel:
        metadata["channel"] = channel
        if user:
            metadata["alias"] = alias_id(channel, user)
    if user:
        metadata["user"] = str(user)
    if user_id:
        metadata["user_id"] = user_id
    ws_ref = workspace or os.environ.get("AGENTS_WORKSPACE", "")
    persona_ref = persona or os.environ.get("AGENTS_PERSONA", "")
    if ws_ref:
        metadata["workspace"] = ws_ref
    if persona_ref:
        metadata["persona"] = persona_ref
    if project:
        metadata["project"] = project
    if binary_refs:
        metadata["binary_refs"] = binary_refs
    event = TraceEvent(
        session=session,
        type="message",
        origin=origin,
        metadata=metadata,
    )
    target.append(event)
    return event
