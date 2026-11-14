"""Export one session's chat from the trace store.

The source is the daily JSONL (and retained bodies), not a vendor transcript.
Telemetry (token totals, file edits, session markers) stays out of the chat.
Messages, tool calls, and compaction frames are included in time order.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .content import ContentError, load_retained
from .models import TraceEvent
from .store import TraceStore

CHAT_FORMAT = "agents-traces.chat/v1"
_FORMATS = {
    "markdown": "markdown",
    "md": "markdown",
    "json": "json",
}


class ExportError(ValueError):
    """The session could not be exported."""

    def __init__(self, message: str, *, code: str = "export_error") -> None:
        super().__init__(message)
        self.code = code


def _clean_session(session: str) -> str:
    cleaned = (session or "").strip()
    if cleaned.startswith("trace:"):
        cleaned = cleaned[len("trace:") :].strip()
    return cleaned


def _meta(event: TraceEvent) -> dict[str, Any]:
    return event.metadata if isinstance(event.metadata, dict) else {}


def _retained(store: TraceStore, inline: str, meta: dict[str, Any], prefix: str) -> str:
    try:
        return load_retained(store.traces_dir, inline, meta, prefix)
    except ContentError:
        return inline


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _binary_refs(meta: dict[str, Any]) -> list[dict[str, Any]]:
    refs = meta.get("binary_refs")
    if not isinstance(refs, list):
        return []
    return [ref for ref in refs if isinstance(ref, dict)]


def _binary_lines(refs: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for ref in refs:
        path = ref.get("path") or ref.get("uri") or ""
        media = ref.get("media_type") or ref.get("type") or "application/octet-stream"
        if path:
            lines.append(f"[binary path={path} type={media}]")
    return lines


def _omit_none(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def _events(store: TraceStore, session: str, days: Optional[int]) -> list[TraceEvent]:
    if days is None:
        return store.get_events_for_session(session)
    files = store.list_trace_files(days=days)
    events = [event for event in store.iter_events(files=files, session_id=session)]
    events.sort(key=lambda event: event.ts or "")
    return events


def session_chat(
    session: str,
    *,
    store: Optional[TraceStore] = None,
    include_tools: bool = True,
    include_frames: bool = True,
    days: Optional[int] = None,
) -> dict[str, Any]:
    """Structured chat for one session.

    ``turns`` are chronological. Message and tool text is the retained body,
    not the inline prefix. Raises ``ExportError`` when the session has no events.
    """
    if days is not None and days < 0:
        raise ExportError("days must be >= 0", code="bad_request")
    sid = _clean_session(session)
    if not sid:
        raise ExportError("session id is required", code="bad_request")
    target = store or TraceStore()
    events = _events(target, sid, days)
    if not events:
        raise ExportError(f"no events for session {sid!r}", code="not_found")

    model: Optional[str] = None
    turns: list[dict[str, Any]] = []
    for event in events:
        if model is None and event.model:
            model = event.model
        meta = _meta(event)
        if event.type == "message":
            role = str(meta.get("role") or "user").lower()
            if role not in ("user", "assistant", "system", "tool"):
                role = "user"
            inline = meta.get("content")
            if not isinstance(inline, str):
                inline = meta.get("prompt") if isinstance(meta.get("prompt"), str) else ""
            body = _retained(target, inline or "", meta, "")
            refs = _binary_refs(meta)
            turn: dict[str, Any] = {
                "ts": event.ts,
                "kind": "message",
                "role": role,
                "content": body,
            }
            if refs:
                turn["binary_refs"] = refs
            turns.append(turn)
        elif include_tools and event.type == "tool_call":
            result = event.result
            if isinstance(result, str):
                result = _retained(target, result, meta, "result_")
            error = event.error
            if isinstance(error, str):
                error = _retained(target, error, meta, "error_")
            turns.append(
                _omit_none(
                    {
                        "ts": event.ts,
                        "kind": "tool_call",
                        "role": "tool",
                        "tool": event.tool or "",
                        "args": event.args,
                        "status": event.status,
                        "result": result,
                        "error": error,
                    }
                )
            )
        elif include_frames and event.type == "frame":
            inline = meta.get("content") if isinstance(meta.get("content"), str) else ""
            turns.append(
                {
                    "ts": event.ts,
                    "kind": "frame",
                    "role": "system",
                    "content": _retained(target, inline, meta, ""),
                }
            )

    document: dict[str, Any] = {"session": sid, "format": CHAT_FORMAT}
    if model:
        document["model"] = model
    document["turns"] = turns
    return document


def _fence(text: str) -> str:
    ticks = "```"
    while ticks in text:
        ticks += "`"
    return ticks


def _fenced(text: str) -> list[str]:
    fence = _fence(text)
    return [fence, text, fence]


def render_markdown(chat: dict[str, Any]) -> str:
    """Render ``session_chat`` output as Markdown."""
    lines = [f"# {_one_line(chat.get('session'))}", ""]
    model = chat.get("model")
    if model:
        lines.append(f"model: {_one_line(model)}")
        lines.append("")
    for turn in chat.get("turns") or []:
        if not isinstance(turn, dict):
            continue
        ts = _one_line(turn.get("ts"))
        kind = turn.get("kind")
        if kind == "message":
            lines.append(f"## {_one_line(turn.get('role') or 'user')} · {ts}")
            lines.append("")
            lines.append(str(turn.get("content") or ""))
            for bit in _binary_lines(turn.get("binary_refs") or []):
                lines.append(bit)
            lines.append("")
        elif kind == "tool_call":
            name = _one_line(turn.get("tool") or "tool")
            lines.append(f"## tool · {name} · {ts}")
            lines.append("")
            status = turn.get("status")
            if status:
                lines.append(f"status: {_one_line(status)}")
                lines.append("")
            args = turn.get("args")
            if args is not None:
                rendered = json.dumps(args, ensure_ascii=False, indent=2, default=str)
                lines.append("### args")
                lines.append("")
                lines.extend(_fenced(rendered))
                lines.append("")
            error = turn.get("error")
            if error:
                lines.append("### error")
                lines.append("")
                lines.append(str(error))
                lines.append("")
            if "result" in turn and turn.get("result") is not None:
                result = turn.get("result")
                rendered = result if isinstance(result, str) else json.dumps(
                    result, ensure_ascii=False, indent=2, default=str
                )
                lines.append("### result")
                lines.append("")
                lines.extend(_fenced(str(rendered)))
                lines.append("")
        elif kind == "frame":
            lines.append(f"## system · frame · {ts}")
            lines.append("")
            lines.append(str(turn.get("content") or ""))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_session(
    session: str,
    *,
    store: Optional[TraceStore] = None,
    fmt: str = "markdown",
    include_tools: bool = True,
    include_frames: bool = True,
    days: Optional[int] = None,
) -> str:
    """Serialize one session's chat as Markdown or JSON."""
    kind = _FORMATS.get((fmt or "").strip().lower())
    if kind is None:
        raise ExportError(
            "format must be markdown, md, or json",
            code="bad_format",
        )
    chat = session_chat(
        session,
        store=store,
        include_tools=include_tools,
        include_frames=include_frames,
        days=days,
    )
    if kind == "json":
        return json.dumps(chat, ensure_ascii=False, indent=2) + "\n"
    return render_markdown(chat)
