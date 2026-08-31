"""
Unified multi-IDE transcript ingester for agents-traces.
Parses traces from:
- Google Antigravity IDE
- Cursor (workspaceStorage & transcripts)
- Claude Code & Claude Desktop
- VS Code & Roo-Cline / Cline tasks
- Zed IDE
- OpenAI GDPR exports
"""

from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from .models import TraceEvent
from .store import TraceStore

_TS_TAG = re.compile(
    r"<timestamp>\s*(.*?)\s*</timestamp>",
    re.I | re.S,
)
_TS_CLOCK = re.compile(
    r"(\w+),\s+(\w+)\s+(\d+),\s+(\d+),\s+(\d+):(\d+)\s*(AM|PM)\s*\(UTC([+-]\d+)(?::(\d+))?\)",
    re.I,
)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _parse_cursor_clock(raw: str) -> Optional[str]:
    """Parse Cursor chat_selection clocks into ISO UTC. No timestamp on jsonl rows."""
    m = _TS_CLOCK.search(raw.replace("\n", " "))
    if not m:
        return None
    month = _MONTHS.get(m.group(2)[:3].lower())
    if not month:
        return None
    day = int(m.group(3))
    year = int(m.group(4))
    hour = int(m.group(5))
    minute = int(m.group(6))
    ampm = m.group(7).upper()
    if ampm == "PM" and hour != 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0
    offset_h = int(m.group(8))
    offset_m = int(m.group(9) or 0)
    tz = timezone(timedelta(hours=offset_h, minutes=offset_m if offset_h >= 0 else -offset_m))
    dt = datetime(year, month, day, hour, minute, tzinfo=tz)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cursor_text_blob(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    bits: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            bits.append(str(part.get("text") or ""))
    return "\n".join(bits)


def _unwrap_mcp_tool(name: str, inp: dict) -> tuple[str, dict]:
    if name in ("CallMcpTool", "call_mcp_tool"):
        inner = inp.get("arguments") or inp.get("Arguments") or {}
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except json.JSONDecodeError:
                inner = {"_raw": inner}
        if not isinstance(inner, dict):
            inner = {"value": inner}
        tool = str(inp.get("toolName") or inp.get("ToolName") or name)
        return tool, inner
    return name, inp


def _existing_sessions(store: TraceStore) -> set[str]:
    seen: set[str] = set()
    for event in store.iter_events():
        if event.session:
            seen.add(event.session)
    return seen


def _canon_arg(event: TraceEvent) -> Optional[str]:
    """Identity for a tool call: the verb plus the argument that is the payload.

    Extra keys (project, limit) differ between live MCP and product jsonl;
    they must not create a second copy of the same write or search.
    """
    if event.type != "tool_call" or not event.tool:
        return None
    args = event.args if isinstance(event.args, dict) else {}
    tool = event.tool
    if tool in ("search_memory", "search_hybrid"):
        q = re.sub(r"\s+", " ", str(args.get("query") or "")).strip().lower()
        return f"search|{q}" if len(q) > 8 else None
    if tool == "add_memory":
        body = re.sub(
            r"\s+",
            " ",
            str(args.get("fact_or_message") or args.get("content") or ""),
        ).strip().lower()
        return f"write|{body}" if len(body) > 20 else None
    if tool == "write_memory_file":
        fid = str(args.get("file_id") or "").strip()
        return f"wfile|{fid.lower()}" if fid else None
    body = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    return f"{tool}|{body}"


def _tool_fingerprint(event: TraceEvent) -> Optional[str]:
    return _canon_arg(event)


def _live_tool_fingerprints(store: TraceStore) -> set[str]:
    fps: set[str] = set()
    for event in store.iter_events():
        if event.origin != "live":
            continue
        fp = _tool_fingerprint(event)
        if fp:
            fps.add(fp)
    return fps


def tool_calls_already_live(events: list[TraceEvent], live_fps: set[str]) -> bool:
    """True when this ingested session's tools are already on a live session.

    Live interceptor and product-jsonl ingest are two pipes for the same work.
    Conversation bodies stay in the product folder; traces keep one copy.
    """
    fps = []
    for event in events:
        fp = _tool_fingerprint(event)
        if fp:
            fps.append(fp)
    if not fps:
        return False
    unique = set(fps)
    hits = unique & live_fps
    if not hits:
        return False
    if hits == unique:
        return True
    return len(hits) >= 2 and (len(hits) / len(unique)) >= 0.5


def ingested_sessions_shadowed_by_live(store: TraceStore) -> set[str]:
    live_fps = _live_tool_fingerprints(store)
    if not live_fps:
        return set()
    by_sid: dict[str, list[TraceEvent]] = {}
    origins: dict[str, set[str]] = {}
    for event in store.iter_events():
        sid = event.session
        if not sid:
            continue
        by_sid.setdefault(sid, []).append(event)
        origins.setdefault(sid, set()).add(event.origin or "unknown")
    drop: set[str] = set()
    for sid, evs in by_sid.items():
        if "live" in origins.get(sid, ()):
            continue
        if tool_calls_already_live(evs, live_fps):
            drop.add(sid)
    return drop


def _expand(raw_path: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    localappdata = os.environ.get("LOCALAPPDATA") or str(
        Path.home() / "AppData" / "Local"
    )
    expanded = raw_path.replace("%APPDATA%", appdata).replace(
        "%LOCALAPPDATA%", localappdata
    )
    return Path(os.path.expanduser(os.path.expandvars(expanded)))


# ============================================================================
# 1. Antigravity IDE Parser
# ============================================================================
def discover_antigravity_transcripts() -> List[Path]:
    brain_dir = Path.home() / ".gemini" / "antigravity-ide" / "brain"
    if not brain_dir.exists():
        return []
    return list(brain_dir.glob("*/.system_generated/logs/transcript.jsonl"))


def parse_antigravity_transcript(path: Path) -> Generator[TraceEvent, None, None]:
    conv_id = path.parent.parent.parent.name
    sid = f"agy-{conv_id[:8]}"

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue

            ts = item.get("created_at") or datetime.now(
                timezone.utc
            ).isoformat().replace("+00:00", "Z")
            step_type = item.get("type")

            if step_type == "USER_INPUT":
                yield TraceEvent(
                    ts=ts,
                    session=sid,
                    type="message",
                    origin="ingested",
                    metadata={
                        "source": "antigravity",
                        "role": "user",
                        "content": str(item.get("content", ""))[:2000],
                    },
                )
                yield TraceEvent(
                    ts=ts,
                    session=sid,
                    type="session_start",
                    origin="ingested",
                    metadata={
                        "source": "antigravity",
                        "prompt": str(item.get("content", ""))[:120],
                    },
                )
            elif step_type == "PLANNER_RESPONSE":
                content = str(item.get("content", ""))
                if content.strip():
                    yield TraceEvent(
                        ts=ts,
                        session=sid,
                        type="message",
                        origin="ingested",
                        metadata={
                            "source": "antigravity",
                            "role": "assistant",
                            "content": content[:2000],
                        },
                    )
                tool_calls = item.get("tool_calls") or []
                for tc in tool_calls:
                    t_name = tc.get("name")
                    t_args_raw = tc.get("args") or {}
                    t_args = {}
                    if isinstance(t_args_raw, dict):
                        for k, v in t_args_raw.items():
                            if (
                                isinstance(v, str)
                                and v.startswith('"')
                                and v.endswith('"')
                            ):
                                t_args[k] = v.strip('"')
                            else:
                                t_args[k] = v

                    if t_name in [
                        "write_to_file",
                        "replace_file_content",
                        "multi_replace_file_content",
                    ]:
                        yield TraceEvent(
                            ts=ts,
                            session=sid,
                            type="file_edit",
                            origin="ingested",
                            file=t_args.get("TargetFile"),
                            tool=t_name,
                            status="ok",
                            metadata={"source": "antigravity"},
                        )
                    else:
                        yield TraceEvent(
                            ts=ts,
                            session=sid,
                            type="tool_call",
                            origin="ingested",
                            tool=t_name,
                            args=t_args,
                            status="ok",
                            metadata={"source": "antigravity"},
                        )
            elif step_type in [
                "RUN_COMMAND",
                "VIEW_FILE",
                "LIST_DIRECTORY",
                "GREP_SEARCH",
            ]:
                content = str(item.get("content", ""))
                if (
                    "exited with code 1" in content
                    or "Traceback" in content
                    or "Error" in content
                ):
                    yield TraceEvent(
                        ts=ts,
                        session=sid,
                        type="error",
                        origin="ingested",
                        error=content[:200],
                        status="error",
                        metadata={"source": "antigravity"},
                    )


# ============================================================================
# 2. Claude Code & Claude Desktop Parser
# ============================================================================
def discover_claude_transcripts() -> List[Path]:
    paths = []
    # Claude Code CLI sessions (~/.claude/projects/*/sessions/*.json or ~/.claude/transcripts/)
    claude_home = Path.home() / ".claude"
    if claude_home.exists():
        paths.extend(claude_home.glob("projects/**/session*.json"))
        paths.extend(claude_home.glob("transcripts/**/*.json*"))
    return paths


def parse_claude_transcript(path: Path) -> Generator[TraceEvent, None, None]:
    sid = f"claude-{path.stem[:8]}"
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    ts = item.get("timestamp") or datetime.now(
                        timezone.utc
                    ).isoformat().replace("+00:00", "Z")
                    msg_type = item.get("type", "custom")
                    yield TraceEvent(
                        ts=ts,
                        session=sid,
                        type=msg_type,
                        origin="ingested",
                        tool=item.get("tool"),
                        status=item.get("status", "ok"),
                        metadata={"source": "claude"},
                    )
    except Exception:
        pass


# ============================================================================
# 3. Roo-Cline & VS Code Tasks Parser
# ============================================================================
def discover_cline_tasks() -> List[Path]:
    paths = []
    if sys.platform == "win32":
        roo_dir = _expand(
            "%APPDATA%/Code/User/globalStorage/rooveterinaryinc.roo-cline/tasks"
        )
        cline_dir = _expand(
            "%APPDATA%/Code/User/globalStorage/saoudrizwan.claude-dev/tasks"
        )
        for d in [roo_dir, cline_dir]:
            if d.exists():
                paths.extend(d.glob("*/api_conversation_history.json"))
                paths.extend(d.glob("*/ui_messages.json"))
    return paths


def parse_cline_task(path: Path) -> Generator[TraceEvent, None, None]:
    task_id = path.parent.name
    sid = f"cline-{task_id[:8]}"
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, list):
            for msg in data:
                ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                say = msg.get("say") or msg.get("type")
                if say in ["tool", "command"]:
                    yield TraceEvent(
                        ts=ts,
                        session=sid,
                        type="tool_call",
                        origin="ingested",
                        tool=str(msg.get("text", ""))[:40],
                        status="ok",
                        metadata={"source": "cline"},
                    )
                elif say == "error":
                    yield TraceEvent(
                        ts=ts,
                        session=sid,
                        type="error",
                        origin="ingested",
                        error=str(msg.get("text", ""))[:200],
                        status="error",
                        metadata={"source": "cline"},
                    )
    except Exception:
        pass


# ============================================================================
# 4. Cursor agent-transcripts
# ============================================================================
def discover_cursor_transcripts() -> List[Path]:
    root = Path.home() / ".cursor" / "projects"
    if not root.is_dir():
        return []
    best: dict[str, Path] = {}
    for path in root.rglob("*.jsonl"):
        if "agent-transcripts" not in path.parts:
            continue
        if "subagents" in path.parts:
            continue
        uid = path.parent.name
        prev = best.get(uid)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if prev is None or mtime > prev.stat().st_mtime:
            best[uid] = path
    return list(best.values())


def parse_cursor_transcript(path: Path) -> Generator[TraceEvent, None, None]:
# Cursor copies the same conversation uuid under every workspace folder.
# Ingest once, newest file wins. Session id is cursor-<uuid>.
    sid = f"cursor-{path.parent.name}"
    fallback = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    ts = fallback.strftime("%Y-%m-%dT%H:%M:%SZ")
    started = False
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = obj.get("role")
            msg = obj.get("message") or {}
            content = msg.get("content")
            blob = _cursor_text_blob(content)
            clock = _parse_cursor_clock(blob)
            if not clock:
                tag = _TS_TAG.search(blob)
                if tag:
                    clock = _parse_cursor_clock(tag.group(1))
            if clock:
                ts = clock
            if role == "user" and blob.strip():
                query = blob
                m = re.search(r"<user_query>\s*(.*?)\s*</user_query>", blob, re.S)
                if m:
                    query = m.group(1)
                query = query.strip()[:2000]
                if not started:
                    started = True
                    yield TraceEvent(
                        ts=ts,
                        session=sid,
                        type="session_start",
                        origin="ingested",
                        metadata={"source": "cursor", "prompt": query[:120], "path": str(path)},
                    )
                yield TraceEvent(
                    ts=ts,
                    session=sid,
                    type="message",
                    origin="ingested",
                    metadata={"source": "cursor", "role": "user", "content": query},
                )
                continue
            if role != "assistant" or not isinstance(content, list):
                continue
            texts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text" and part.get("text"):
                    texts.append(str(part.get("text"))[:2000])
                elif ptype == "tool_use":
                    raw_name = str(part.get("name") or "tool")
                    inp = part.get("input") if isinstance(part.get("input"), dict) else {}
                    tool, args = _unwrap_mcp_tool(raw_name, inp)
                    yield TraceEvent(
                        ts=ts,
                        session=sid,
                        type="tool_call",
                        origin="ingested",
                        tool=tool,
                        args=args,
                        status="ok",
                        metadata={"source": "cursor", "mcp_wrapper": raw_name},
                    )
            if texts:
                yield TraceEvent(
                    ts=ts,
                    session=sid,
                    type="message",
                    origin="ingested",
                    metadata={"source": "cursor", "role": "assistant", "content": texts[0]},
                )


# ============================================================================
# 5. Master Ingester
# ============================================================================
def ingest_all_ide_transcripts(store: Optional[TraceStore] = None) -> Dict[str, Any]:
    """
    Ingest all available IDE conversations (Antigravity, Claude, Cursor, Cline)
    into unified daily trace files ~/.agents/traces/YYYY-MM-DD.jsonl.
    Sessions already present are skipped (re-ingest is not a second copy).
    Cursor/Antigravity jsonl that duplicates live MCP tool calls is skipped
    (and dropped if already written): one session in traces, bodies stay in
    the product folder.
    """
    target_store = store or TraceStore()
    twins = ingested_sessions_shadowed_by_live(target_store)
    purged = target_store.drop_sessions(twins) if twins else 0
    known = _existing_sessions(target_store)
    live_fps = _live_tool_fingerprints(target_store)
    stats: Dict[str, Any] = {
        "antigravity_events": 0,
        "claude_events": 0,
        "cline_events": 0,
        "cursor_events": 0,
        "skipped_sessions": 0,
        "skipped_live_twins": 0,
        "purged_twin_events": purged,
        "purged_twin_sessions": len(twins),
        "total_events": 0,
        "total_sessions": 0,
    }

    def _ingest(files, parser, key: str) -> None:
        for f in files:
            events = list(parser(f))
            if not events:
                continue
            sid = events[0].session
            if sid in known:
                stats["skipped_sessions"] += 1
                continue
            if tool_calls_already_live(events, live_fps):
                stats["skipped_live_twins"] += 1
                known.add(sid)
                continue
            for event in events:
                target_store.append(event)
            stats[key] += len(events)
            stats["total_sessions"] += 1
            known.add(sid)

    _ingest(discover_antigravity_transcripts(), parse_antigravity_transcript, "antigravity_events")
    _ingest(discover_claude_transcripts(), parse_claude_transcript, "claude_events")
    _ingest(discover_cline_tasks(), parse_cline_task, "cline_events")
    _ingest(discover_cursor_transcripts(), parse_cursor_transcript, "cursor_events")

    stats["total_events"] = (
        stats["antigravity_events"]
        + stats["claude_events"]
        + stats["cline_events"]
        + stats["cursor_events"]
    )
    return stats
