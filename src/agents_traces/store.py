from __future__ import annotations

import gzip
import json
import os
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Iterable, List, Optional, Set

from .models import TraceEvent


def get_default_traces_dir() -> Path:
    """Get the traces directory, checking environment variable or defaulting to ~/.agents/traces."""
    env_dir = os.environ.get("AGENTS_TRACES_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return Path.home() / ".agents" / "traces"


class TraceStore:
    def __init__(self, traces_dir: Optional[Path | str] = None):
        if traces_dir is not None:
            self.traces_dir = Path(traces_dir).expanduser().resolve()
        else:
            self.traces_dir = get_default_traces_dir()
        self.traces_dir.mkdir(parents=True, exist_ok=True)

    def get_date_file_path(self, target_date: Optional[date] = None) -> Path:
        """Return the .jsonl file path for a given date (defaults to UTC today)."""
        d = target_date or datetime.now(timezone.utc).date()
        return self.traces_dir / f"{d.strftime('%Y-%m-%d')}.jsonl"

    def append(self, event: TraceEvent) -> None:
        """Ultra-fast atomic-like append to daily trace file."""
        # Extract target date from event timestamp if possible, fallback to today
        target_date: Optional[date] = None
        if event.ts:
            try:
                dt = datetime.fromisoformat(event.ts.replace("Z", "+00:00"))
                target_date = dt.date()
            except Exception:
                pass

        file_path = self.get_date_file_path(target_date)
        line = event.to_json() + "\n"
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(line)

    def list_trace_files(self, days: Optional[int] = None) -> List[Path]:
        """Return sorted list of jsonl trace files (newest first)."""
        if not self.traces_dir.exists():
            return []
        
        files = sorted(
            [p for p in self.traces_dir.glob("*.jsonl") if p.is_file()],
            key=lambda p: p.name,
            reverse=True
        )
        if days is not None and days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            files = [p for p in files if p.stem >= cutoff]
        return files

    def iter_events(
        self,
        files: Optional[Iterable[Path]] = None,
        session_id: Optional[str] = None,
        event_type: Optional[str] = None,
        status: Optional[str] = None,
        only_errors: bool = False,
    ) -> Generator[TraceEvent, None, None]:
        """Stream events generator with zero memory overhead."""
        target_files = list(files) if files is not None else self.list_trace_files()
        
        for file_path in target_files:
            if not file_path.exists():
                continue
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    event = TraceEvent.from_json(line)
                    if event is None:
                        continue
                    if session_id and event.session != session_id:
                        continue
                    if event_type and event.type != event_type:
                        continue
                    if status and event.status != status:
                        continue
                    if only_errors and event.type != "error" and event.status != "error" and not event.error:
                        continue
                    yield event

    def get_events_for_session(self, session_id: str) -> List[TraceEvent]:
        """Return all events for a given session, ordered chronologically."""
        events = list(self.iter_events(session_id=session_id))
        events.sort(key=lambda e: e.ts)
        return events

    def get_last_session_id(self) -> Optional[str]:
        """Find the most recent active session ID from the latest traces."""
        for event in self.iter_events():
            if event.session:
                return event.session
        return None

    def get_recent_errors(self, limit: int = 10, session_id: Optional[str] = None) -> List[TraceEvent]:
        """Get the most recent N errors (newest first)."""
        errors: List[TraceEvent] = []
        for event in self.iter_events(session_id=session_id, only_errors=True):
            errors.append(event)
            if len(errors) >= limit:
                break
        return errors

    def list_sessions(self, days: int = 1, limit: int = 50) -> List[dict]:
        """Summarize recent sessions."""
        target_files = self.list_trace_files(days=days)
        sessions_map: dict[str, dict] = {}
        
        for event in self.iter_events(files=target_files):
            sid = event.session
            if not sid:
                continue
            if sid not in sessions_map:
                sessions_map[sid] = {
                    "session": sid,
                    "first_ts": event.ts,
                    "last_ts": event.ts,
                    "event_count": 0,
                    "tool_calls": 0,
                    "errors": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost_usd": 0.0,
                    "model": None,
                }
            s = sessions_map[sid]
            s["event_count"] += 1
            s["first_ts"] = min(s["first_ts"], event.ts)
            s["last_ts"] = max(s["last_ts"], event.ts)
            
            if event.type == "tool_call":
                s["tool_calls"] += 1
            if event.type == "error" or event.status == "error" or event.error:
                s["errors"] += 1
            if event.tokens_in:
                s["tokens_in"] += event.tokens_in
            if event.tokens_out:
                s["tokens_out"] += event.tokens_out
            if event.cost_usd:
                s["cost_usd"] += event.cost_usd
            if event.model and not s["model"]:
                s["model"] = event.model
                
            if len(sessions_map) >= limit * 2:
                # Early stop once we've seen plenty of distinct sessions
                pass

        results = list(sessions_map.values())
        # Sort by last_ts descending
        results.sort(key=lambda x: x["last_ts"], reverse=True)
        return results[:limit]

    def drop_sessions(self, sessions: Set[str]) -> int:
        """Rewrite daily files without these session ids. Used to drop ingested
        twins of live tool logs — not a general mutate path."""
        if not sessions:
            return 0
        dropped = 0
        for path in self.list_trace_files():
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            kept: list[str] = []
            changed = False
            for line in raw.splitlines(True):
                if not line.strip():
                    kept.append(line)
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    kept.append(line)
                    continue
                if obj.get("session") in sessions:
                    dropped += 1
                    changed = True
                    continue
                kept.append(line)
            if changed:
                tmp = path.with_suffix(".jsonl.tmp")
                tmp.write_text("".join(kept), encoding="utf-8")
                tmp.replace(path)
        return dropped

    def cleanup_old_traces(self, keep_days: int = 30, compress: bool = False) -> int:
        """Delete or compress trace files older than keep_days. Returns count of affected files."""
        if not self.traces_dir.exists():
            return 0
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        count = 0
        
        for p in self.traces_dir.glob("*.jsonl"):
            if p.is_file() and p.stem < cutoff_date:
                if compress:
                    gz_path = p.with_suffix(".jsonl.gz")
                    with open(p, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    p.unlink()
                else:
                    p.unlink()
                count += 1
        return count
