"""Cached system prefix + per-turn clock.

Prefix caching (OpenAI, Anthropic, Gemini) matches the longest identical
byte prefix. `start_date` belongs in that prefix because it is frozen for
the session. Wall-clock `now` does not: it would rewrite the whole system
block every turn. Clock is a trailing message after history.

Order: system_prompt (stable) → history (append-only) → clock → new user turn.
Never put clock between system and history; that busts the history prefix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass
class PromptParts:
    """Stable prefix. No wall-clock here."""

    instructions: str = ""
    role: str = ""
    workflow: str = ""
    guardrails: str = ""
    tool_rules: str = ""
    skills: list[tuple[str, str]] = field(default_factory=list)
    user_id: str = ""
    user_display: str = ""
    work: str = ""
    project: str = ""
    timezone: str = ""
    aliases: list[str] = field(default_factory=list)
    session_id: str = ""
    start_date: str = ""
    memory_root: str = ""


DEFAULT_ROLE = ""
DEFAULT_WORKFLOW = ""
DEFAULT_GUARDRAILS = ""
DEFAULT_TOOL_RULES = ""


def _e(value: str) -> str:
    return escape(value or "", {'"': "&quot;", "'": "&apos;"})


def _block(tag: str, body: str, indent: str = "    ") -> str:
    text = (body or "").strip()
    if not text:
        return ""
    return f"{indent}<{tag}>{_e(text)}</{tag}>"


def render_system_prompt(parts: PromptParts) -> str:
    """XML system prefix. Identical bytes across turns of the same session."""
    inner: list[str] = []
    if parts.instructions.strip():
        inner.append(_block("text", parts.instructions))
    inner.append(_block("role", parts.role or (DEFAULT_ROLE if not parts.instructions else "")))
    inner.append(_block("workflow", parts.workflow or (DEFAULT_WORKFLOW if not parts.instructions else "")))
    inner.append(_block("guardrails", parts.guardrails or (DEFAULT_GUARDRAILS if not parts.instructions else "")))
    inner.append(_block("tool_rules", parts.tool_rules or (DEFAULT_TOOL_RULES if not parts.instructions else "")))
    instructions = "\n".join(x for x in inner if x)

    skills_xml = []
    for name, summary in parts.skills:
        n = _e(name)
        s = _e(summary)
        skills_xml.append(f'    <skill name="{n}">{s}</skill>')
    catalog = "\n".join(skills_xml) if skills_xml else "    <!-- none loaded this request -->"

    aliases = "\n".join(
        f"      <alias>{_e(a)}</alias>" for a in parts.aliases if a
    )
    user_attrs = [f'id="{_e(parts.user_id)}"']
    if parts.user_display:
        user_attrs.append(f'display="{_e(parts.user_display)}"')
    user_open = " ".join(user_attrs)
    if aliases:
        user_xml = f"    <user {user_open}>\n{aliases}\n    </user>"
    else:
        user_xml = f"    <user {user_open}/>"

    runtime = [user_xml]
    runtime.append(_block("work", parts.work))
    runtime.append(_block("project", parts.project))
    if parts.timezone:
        runtime.append(_block("timezone", parts.timezone))
    if parts.memory_root:
        runtime.append(_block("memory_root", parts.memory_root))
    sess_attrs = [f'id="{_e(parts.session_id)}"']
    if parts.start_date:
        sess_attrs.append(f'start_date="{_e(parts.start_date)}"')
    runtime.append(f'    <session {" ".join(sess_attrs)}/>')
    runtime_body = "\n".join(x for x in runtime if x)

    return (
        "<system_prompt>\n"
        "  <instructions>\n"
        f"{instructions}\n"
        "  </instructions>\n"
        "  <skill_catalog>\n"
        f"{catalog}\n"
        "  </skill_catalog>\n"
        "  <runtime_context>\n"
        f"{runtime_body}\n"
        "  </runtime_context>\n"
        "</system_prompt>"
    )


def clock_message(
    now: datetime | None = None,
    *,
    timezone_name: str = "",
) -> dict[str, str]:
    """Ephemeral clock. Assemble after history so the system prefix stays cacheable.

    If timezone_name is an IANA zone, convert the instant into that zone so the
    label matches the offset. Invalid names fall back to UTC. Body is weekday
    plus local ISO with offset (not a UTC stamp wearing a foreign label).
    """
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    tz_label = (timezone_name or "").strip()
    if tz_label:
        try:
            instant = instant.astimezone(ZoneInfo(tz_label))
        except (ZoneInfoNotFoundError, ValueError, OSError, KeyError):
            instant = instant.astimezone(timezone.utc)
            tz_label = "UTC"
    else:
        instant = instant.astimezone()
        tz_label = str(instant.tzinfo or "UTC")
    weekday = instant.strftime("%A")
    iso = instant.isoformat(timespec="seconds")
    return {
        "role": "system",
        "content": f'<clock timezone="{_e(tz_label)}">{_e(f"{weekday} {iso}")}</clock>',
    }


PREVIEW_CHARS = 768
HISTORY_CHARS = 24000
# Last tool *round* (consecutive role=tool after one assistant) keeps a preview.
# Earlier large stdio is stubbed in place. Small results stay (errors, "ok").
KEEP_RECENT_STDIO_ROUNDS = 1
STDIO_KEEP_CHARS = 256
_DIGEST_RE = re.compile(r'digest="([a-f0-9]+)"')
_CHARS_RE = re.compile(r'chars="(\d+)"')


def content_digest(text: str) -> str:
    return sha256((text or "").encode("utf-8")).hexdigest()[:12]


def preview_text(
    text: str,
    *,
    session: str = "",
    ts: str = "",
    max_chars: int = PREVIEW_CHARS,
) -> str:
    """Bounded prompt projection. Full body stays in traces. Not a summary.

    The <ref> is a frozen address (digest of the stored bytes). It does not
    change on later turns, so it does not rewrite that message.
    """
    raw = text or ""
    digest = content_digest(raw)
    if len(raw) <= max_chars:
        return raw
    head = raw[:max_chars].rstrip()
    attrs = [f'digest="{digest}"', f'chars="{len(raw)}"']
    if session:
        attrs.append(f'session="{_e(session)}"')
    if ts:
        attrs.append(f'ts="{_e(ts)}"')
    return f"{head}\n<ref {' '.join(attrs)}/>"


def stdio_stub(text: str) -> str:
    """Replace large tool/stdio in the prompt with a frozen address. Not a summary.

    Idempotent: a stub is left byte-identical on later collapses so it does not
    keep rewriting the prefix of that message.
    """
    raw = text or ""
    if raw.lstrip().startswith("<stdio"):
        return raw
    found = _DIGEST_RE.search(raw)
    digest = found.group(1) if found else content_digest(raw)
    found_n = _CHARS_RE.search(raw)
    n = int(found_n.group(1)) if found_n else len(raw)
    return f'<stdio evicted="1" digest="{digest}" chars="{n}"/>'


def _tool_round_ranges(messages: list[dict[str, Any]]) -> list[list[int]]:
    """Consecutive role=tool messages are one round (parallel calls in one step)."""
    rounds: list[list[int]] = []
    i = 0
    n = len(messages)
    while i < n:
        if messages[i].get("role") == "tool":
            group: list[int] = []
            while i < n and messages[i].get("role") == "tool":
                group.append(i)
                i += 1
            rounds.append(group)
        else:
            i += 1
    return rounds


def collapse_old_stdio(
    messages: list[dict[str, Any]],
    *,
    keep_rounds: int = KEEP_RECENT_STDIO_ROUNDS,
    keep_chars: int = STDIO_KEEP_CHARS,
) -> list[dict[str, Any]]:
    """Evict early large tool/command output from the prompt.

    User/assistant text is not touched. Tool messages stay (API pairing).
    Only role=tool bodies shrink. Traces are not rewritten.
    """
    live: set[int] = set()
    rounds = _tool_round_ranges(messages)
    if keep_rounds > 0:
        for group in rounds[-keep_rounds:]:
            live.update(group)
    out: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "tool" or i in live:
            out.append(msg)
            continue
        body = str(msg.get("content") or "")
        if len(body) <= keep_chars:
            out.append(msg)
            continue
        new = dict(msg)
        new["content"] = stdio_stub(body)
        out.append(new)
    return out


def stamp_content(ts: str, content: str) -> str:
    """Frozen event time on a history turn. Does not include wall-clock now."""
    text = content or ""
    if not ts or text.startswith("<ts>"):
        return text
    return f"<ts>{ts}</ts>\n{text}"


def system_messages(parts: PromptParts) -> list[dict[str, str]]:
    return [{"role": "system", "content": render_system_prompt(parts)}]
