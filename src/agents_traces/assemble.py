"""The agent is a trace: reconstruct chat-completions messages per request.

Conversation bodies live in ~/.agents/traces. Product jsonl stays in the
vendor folder. Memory holds pointers only. Binaries are referenced, not inlined
(multimodal / TTS / STT later).
"""

from __future__ import annotations

from typing import Any, Optional

from .identity import alias_id, legacy_session_id
from .models import TraceEvent
from .prompt import HISTORY_CHARS, PREVIEW_CHARS, collapse_old_stdio, content_digest, preview_text, stamp_content
from .store import TraceStore


def session_id_for(channel: str, user: str | int) -> str:
    """Legacy session key (`telegram-42`). Prefer IdentityStore.resolve()."""
    return legacy_session_id(channel, user)


def _meta(event: TraceEvent) -> dict[str, Any]:
    return event.metadata if isinstance(event.metadata, dict) else {}


def _render_content(event: TraceEvent) -> str:
    md = _meta(event)
    text = str(md.get("content") or md.get("prompt") or "").strip()
    refs = md.get("binary_refs") or []
    if not isinstance(refs, list):
        refs = []
    bits = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        path = ref.get("path") or ref.get("uri") or ""
        media = ref.get("media_type") or ref.get("type") or "application/octet-stream"
        if path:
            bits.append(f"[binary path={path} type={media}]")
    if bits:
        extra = " ".join(bits)
        return f"{text}\n{extra}".strip() if text else extra
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


def assemble_messages(
    session: str,
    *,
    store: Optional[TraceStore] = None,
    limit: int = 24,
    days: int = 30,
    include_tools: bool = False,
) -> list[dict[str, Any]]:
    """Rebuild a prompt projection, not the log.

    Traces stay append-only and complete. Each turn in the prompt is a frozen
    preview; overflow is a <ref digest=…> into this session, not an LLM summary.
    """
    target = store or TraceStore()
    files = target.list_trace_files(days=days)
    events = [
        e
        for e in target.iter_events(files=files, session_id=session)
        if e.type == "message" or (include_tools and e.type == "tool_call")
    ]
    events.sort(key=lambda e: e.ts or "")
    out: list[dict[str, Any]] = []
    for event in events:
        if event.type == "message":
            role = str(_meta(event).get("role") or "user").lower()
            if role not in ("user", "assistant", "system", "tool"):
                role = "user"
            content = _render_content(event)
            if not content:
                continue
            content = preview_text(
                content,
                session=session,
                ts=event.ts or "",
                max_chars=PREVIEW_CHARS,
            )
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
                out.append(
                    {
                        "role": "tool",
                        "content": preview_text(
                            str(result),
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
    binary_refs: Optional[list[dict[str, str]]] = None,
    origin: str = "live",
) -> TraceEvent:
    """Append one chat turn. Provenance of who engaged lives on the event."""
    target = store or TraceStore()
    metadata: dict[str, Any] = {
        "role": role,
        "content": content[:20000],
        "digest": content_digest(content[:20000]),
        "source": channel or "local",
    }
    if channel:
        metadata["channel"] = channel
        if user:
            metadata["alias"] = alias_id(channel, user)
    if user:
        metadata["user"] = str(user)
    if user_id:
        metadata["user_id"] = user_id
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
