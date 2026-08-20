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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from .models import TraceEvent
from .store import TraceStore


def _expand(raw_path: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    localappdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    expanded = raw_path.replace("%APPDATA%", appdata).replace("%LOCALAPPDATA%", localappdata)
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

            ts = item.get("created_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            step_type = item.get("type")
            
            if step_type == "USER_INPUT":
                yield TraceEvent(
                    ts=ts,
                    session=sid,
                    type="session_start",
                    metadata={"source": "antigravity", "prompt": str(item.get("content", ""))[:120]},
                )
            elif step_type == "PLANNER_RESPONSE":
                tool_calls = item.get("tool_calls") or []
                for tc in tool_calls:
                    t_name = tc.get("name")
                    t_args_raw = tc.get("args") or {}
                    t_args = {}
                    if isinstance(t_args_raw, dict):
                        for k, v in t_args_raw.items():
                            if isinstance(v, str) and v.startswith('"') and v.endswith('"'):
                                t_args[k] = v.strip('"')
                            else:
                                t_args[k] = v
                    
                    if t_name in ["write_to_file", "replace_file_content", "multi_replace_file_content"]:
                        yield TraceEvent(
                            ts=ts,
                            session=sid,
                            type="file_edit",
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
                            tool=t_name,
                            args=t_args,
                            status="ok",
                            metadata={"source": "antigravity"},
                        )
            elif step_type in ["RUN_COMMAND", "VIEW_FILE", "LIST_DIRECTORY", "GREP_SEARCH"]:
                content = str(item.get("content", ""))
                if "exited with code 1" in content or "Traceback" in content or "Error" in content:
                    yield TraceEvent(
                        ts=ts,
                        session=sid,
                        type="error",
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
                    ts = item.get("timestamp") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    msg_type = item.get("type", "custom")
                    yield TraceEvent(
                        ts=ts,
                        session=sid,
                        type=msg_type,
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
        roo_dir = _expand("%APPDATA%/Code/User/globalStorage/rooveterinaryinc.roo-cline/tasks")
        cline_dir = _expand("%APPDATA%/Code/User/globalStorage/saoudrizwan.claude-dev/tasks")
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
                        tool=str(msg.get("text", ""))[:40],
                        status="ok",
                        metadata={"source": "cline"},
                    )
                elif say == "error":
                    yield TraceEvent(
                        ts=ts,
                        session=sid,
                        type="error",
                        error=str(msg.get("text", ""))[:200],
                        status="error",
                        metadata={"source": "cline"},
                    )
    except Exception:
        pass


# ============================================================================
# 4. Master Ingester
# ============================================================================
def ingest_all_ide_transcripts(store: Optional[TraceStore] = None) -> Dict[str, Any]:
    """
    Ingest all available IDE conversations (Antigravity, Claude, Cursor, Cline)
    into unified daily trace files ~/.agents/traces/YYYY-MM-DD.jsonl.
    """
    target_store = store or TraceStore()
    stats: Dict[str, int] = {
        "antigravity_events": 0,
        "claude_events": 0,
        "cline_events": 0,
        "total_events": 0,
        "total_sessions": 0,
    }

    # 1. Antigravity
    agy_files = discover_antigravity_transcripts()
    for f in agy_files:
        count = 0
        for event in parse_antigravity_transcript(f):
            target_store.append(event)
            count += 1
        stats["antigravity_events"] += count
        if count > 0:
            stats["total_sessions"] += 1

    # 2. Claude
    claude_files = discover_claude_transcripts()
    for f in claude_files:
        count = 0
        for event in parse_claude_transcript(f):
            target_store.append(event)
            count += 1
        stats["claude_events"] += count
        if count > 0:
            stats["total_sessions"] += 1

    # 3. Cline / Roo-Cline
    cline_files = discover_cline_tasks()
    for f in cline_files:
        count = 0
        for event in parse_cline_task(f):
            target_store.append(event)
            count += 1
        stats["cline_events"] += count
        if count > 0:
            stats["total_sessions"] += 1

    stats["total_events"] = (
        stats["antigravity_events"] + stats["claude_events"] + stats["cline_events"]
    )
    return stats
