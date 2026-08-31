"""Read conversation lines from the traces store.

Product jsonl (Cursor, Antigravity, …) stays in the vendor folder.
Ingest copies tool/message events into ~/.agents/traces. Memory keeps
pointers only (chats-index). These helpers are the traces-side snap/grep/tail.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Iterator, Optional

from .models import TraceEvent
from .store import TraceStore


def _meta(event: TraceEvent) -> dict:
    return event.metadata if isinstance(event.metadata, dict) else {}


def _message_text(event: TraceEvent) -> str:
    md = _meta(event)
    text = md.get("content") or md.get("prompt") or ""
    return str(text).strip()


def iter_session_lines(
    store: Optional[TraceStore] = None,
    session_id: str = "",
    since: str = "",
    roles: Optional[Iterable[str]] = ("user",),
    days: Optional[int] = None,
) -> Iterator[dict[str, Any]]:
    target = store or TraceStore()
    want_roles = {r.lower() for r in roles} if roles is not None else None
    needle = session_id.strip().lower()
    files = target.list_trace_files(days=days) if days else None
    for event in target.iter_events(files=files):
        if event.type != "message":
            continue
        if needle and needle not in (event.session or "").lower():
            continue
        if since and (event.ts or "") < since:
            continue
        role = str(_meta(event).get("role") or "").lower()
        if want_roles is not None and role and role not in want_roles:
            continue
        text = _message_text(event)
        if not text:
            continue
        yield {
            "source": str(_meta(event).get("source") or event.origin or "trace"),
            "title": (event.session or "")[:16],
            "text": text,
            "file": event.session or "",
            "ts": event.ts or "",
            "session": event.session or "",
            "role": role or "user",
        }


def format_snap(lines: list[dict[str, Any]], limit: int = 20) -> str:
    limit = max(1, limit)
    recent = lines[-limit:] if len(lines) > limit else lines
    if not recent:
        return "No session user lines in traces. Run `python -m agents_traces ingest`."
    parts = [f"--- Recent Session User Lines ({len(recent)}) ---"]
    for item in reversed(recent):
        snippet = item["text"]
        if len(snippet) > 300:
            snippet = snippet[:300] + "..."
        parts.append(f"[{item['source']} | {item['title']}] {snippet}")
    return "\n".join(parts)


def format_grep(pattern: str, lines: list[dict[str, Any]], since: str = "") -> str:
    if not pattern:
        return "Error: pattern is required for session_grep"
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Error invalid regex pattern: {e}"
    hits = []
    for item in lines:
        if since and item.get("ts", "") < since:
            continue
        if rx.search(item["text"]) or rx.search(item["title"]):
            hits.append(item)
    if not hits:
        return f"No session lines matching '{pattern}'."
    out = [f"Found {len(hits)} matching session lines:"]
    for item in hits[-50:]:
        snippet = item["text"].replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        out.append(f"- [{item['source']} | {item['title']}] {snippet}")
    return "\n".join(out)


def format_tail(lines: list[dict[str, Any]], session_id: str = "", limit: int = 10) -> str:
    limit = max(1, limit)
    if session_id:
        filtered = [
            item
            for item in lines
            if session_id.lower() in item["session"].lower()
            or session_id.lower() in item["title"].lower()
        ]
    else:
        filtered = lines
    if not filtered:
        if session_id:
            return f"Session tail (0 lines). No session lines found matching '{session_id}'."
        return "Session tail (0 lines). Run `python -m agents_traces ingest`."
    tail = filtered[-limit:]
    out = [f"Session tail ({len(tail)} lines):"]
    for item in tail:
        snippet = item["text"].replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        out.append(f"[{item['ts']}] [{item['session']}] {snippet}")
    return "\n".join(out)


def session_snap(limit: int = 20, store: Optional[TraceStore] = None, days: int = 14) -> str:
    lines = list(iter_session_lines(store=store, days=days))
    return format_snap(lines, limit=limit)


def session_grep(pattern: str, since: str = "", store: Optional[TraceStore] = None, days: Optional[int] = 30) -> str:
    lines = list(iter_session_lines(store=store, since=since, roles=None, days=days))
    return format_grep(pattern, lines, since=since)


def session_tail(session_id: str = "", limit: int = 10, store: Optional[TraceStore] = None, days: int = 14) -> str:
    lines = list(iter_session_lines(store=store, session_id=session_id, roles=None, days=days))
    return format_tail(lines, session_id=session_id, limit=limit)
