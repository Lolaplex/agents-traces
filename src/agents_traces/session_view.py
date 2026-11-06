"""Read conversation lines from the traces store.

Product jsonl (Cursor, Antigravity, …) stays in the vendor folder.
Ingest copies tool/message events into ~/.agents/traces. Memory keeps
pointers only (chats-index). These helpers are the traces-side snap/grep/tail.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Iterator, Optional

from .content import ContentError, read_blob
from .models import TraceEvent
from .prompt import content_digest, truncation_ref
from .store import TraceStore

_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _meta(event: TraceEvent) -> dict:
    return event.metadata if isinstance(event.metadata, dict) else {}


def _message_text(event: TraceEvent) -> str:
    md = _meta(event)
    text = md.get("content") or md.get("prompt") or ""
    return str(text).strip()


def _date_prefix(value: str) -> str:
    matched = _DATE.match((value or "").strip())
    return matched.group(1) if matched else ""


def _ts_in_range(ts: str, since: str, until: str) -> bool:
    """Inclusive window. A YYYY-MM-DD bound covers that whole UTC day."""
    stamp = ts or ""
    if since:
        if len(since) == 10 and _DATE.fullmatch(since):
            if stamp[:10] < since:
                return False
        elif stamp < since:
            return False
    if until:
        if len(until) == 10 and _DATE.fullmatch(until):
            if stamp[:10] > until:
                return False
        elif stamp > until:
            return False
    return True


def iter_session_lines(
    store: Optional[TraceStore] = None,
    session_id: str = "",
    since: str = "",
    roles: Optional[Iterable[str]] = ("user",),
    days: Optional[int] = None,
    until: str = "",
) -> Iterator[dict[str, Any]]:
    target = store or TraceStore()
    want_roles = {r.lower() for r in roles} if roles is not None else None
    needle = session_id.strip().lower()
    if needle.startswith("trace:"):
        needle = needle[len("trace:") :].strip()
    start = _date_prefix(since)
    end = _date_prefix(until)
    if start or end:
        files = target.list_trace_files(start=start or None, end=end or None)
    elif days:
        files = target.list_trace_files(days=days)
    else:
        files = None
    for event in target.iter_events(files=files):
        if event.type != "message":
            continue
        if needle and needle not in (event.session or "").lower():
            continue
        if not _ts_in_range(event.ts or "", since, until):
            continue
        md = _meta(event)
        role = str(md.get("role") or "").lower()
        if want_roles is not None and role and role not in want_roles:
            continue
        text = _message_text(event)
        if md.get("truncated") and md.get("sha256"):
            try:
                text = read_blob(target.traces_dir, str(md["sha256"])).strip()
            except ContentError:
                pass
        if not text:
            continue
        chars = md.get("chars")
        yield {
            "source": str(md.get("source") or event.origin or "trace"),
            "title": (event.session or "")[:16],
            "text": text,
            "file": event.session or "",
            "ts": event.ts or "",
            "session": event.session or "",
            "role": role or "user",
            "digest": str(md.get("digest") or ""),
            "sha256": str(md.get("sha256") or ""),
            "chars": chars if isinstance(chars, int) else len(text),
            "truncated": bool(md.get("truncated")),
        }


def _bound_preview(text: str, limit: int, item: dict[str, Any], *, flatten: bool = False) -> str:
    """Clip a search hit. Carry digest, original length, and truncated=1."""
    shown = text.replace("\n", " ") if flatten else text
    stored = bool(item.get("truncated"))
    total = item.get("chars")
    if not isinstance(total, int):
        total = len(text)
    if not stored and len(shown) <= limit:
        return shown
    digest = str(item.get("digest") or "")
    sha = str(item.get("sha256") or "")
    if not digest:
        digest = content_digest(text)
        if not stored:
            total = len(text)
    head = shown[:limit].rstrip()
    ref = truncation_ref(digest, total, sha256=sha)
    return f"{head}\n{ref}"


def format_snap(lines: list[dict[str, Any]], limit: int = 20) -> str:
    limit = max(1, limit)
    recent = lines[-limit:] if len(lines) > limit else lines
    if not recent:
        return "No session user lines in traces. Run `python -m agents_traces ingest`."
    parts = [f"--- Recent Session User Lines ({len(recent)}) ---"]
    for item in reversed(recent):
        snippet = _bound_preview(item["text"], 300, item)
        parts.append(f"[{item['source']} | {item['title']}] {snippet}")
    return "\n".join(parts)


def format_grep(pattern: str, lines: list[dict[str, Any]], since: str = "", until: str = "") -> str:
    if not pattern:
        return "Error: pattern is required for session_grep"
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Error invalid regex pattern: {e}"
    hits = []
    for item in lines:
        if not _ts_in_range(str(item.get("ts") or ""), since, until):
            continue
        if rx.search(item["text"]) or rx.search(item["title"]):
            hits.append(item)
    if not hits:
        return f"No session lines matching '{pattern}'."
    out = [f"Found {len(hits)} matching session lines:"]
    for item in hits[-50:]:
        snippet = _bound_preview(item["text"], 200, item, flatten=True)
        out.append(f"- [{item['source']} | {item['title']}] {snippet}")
    return "\n".join(out)


def format_tail(lines: list[dict[str, Any]], session_id: str = "", limit: int = 10) -> str:
    limit = max(1, limit)
    clean_sid = session_id.strip()
    if clean_sid.startswith("trace:"):
        clean_sid = clean_sid[len("trace:") :].strip()
    if clean_sid:
        filtered = [
            item
            for item in lines
            if clean_sid.lower() in item["session"].lower()
            or clean_sid.lower() in item["title"].lower()
        ]
    else:
        filtered = lines
    if not filtered:
        if clean_sid:
            return f"Session tail (0 lines). No session lines found matching '{session_id}'."
        return "Session tail (0 lines). Run `python -m agents_traces ingest`."
    tail = filtered[-limit:]
    out = [f"Session tail ({len(tail)} lines):"]
    for item in tail:
        snippet = _bound_preview(item["text"], 200, item, flatten=True)
        out.append(f"[{item['ts']}] [{item['session']}] {snippet}")
    return "\n".join(out)


def session_snap(limit: int = 20, store: Optional[TraceStore] = None, days: int = 14) -> str:
    lines = list(iter_session_lines(store=store, days=days))
    return format_snap(lines, limit=limit)


def session_grep(
    pattern: str,
    since: str = "",
    store: Optional[TraceStore] = None,
    days: Optional[int] = 30,
    until: str = "",
) -> str:
    """Search message text. ``since`` / ``until`` are ISO timestamps or YYYY-MM-DD.

    A date-only bound covers that UTC day and limits which daily files are opened.
    When either bound is set, the ``days`` lookback is not applied.
    """
    window = bool(since or until)
    lines = list(
        iter_session_lines(
            store=store,
            since=since,
            until=until,
            roles=None,
            days=None if window else days,
        )
    )
    return format_grep(pattern, lines, since=since, until=until)


def session_tail(session_id: str = "", limit: int = 10, store: Optional[TraceStore] = None, days: int = 14) -> str:
    lines = list(iter_session_lines(store=store, session_id=session_id, roles=None, days=days))
    return format_tail(lines, session_id=session_id, limit=limit)
