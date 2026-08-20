from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def now_utc_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class TraceEvent:
    ts: str = field(default_factory=now_utc_iso)
    session: str = "default"
    type: str = "custom"  # tool_call, llm_call, file_edit, error, session_start, session_end, custom
    
    # Tool call fields
    tool: Optional[str] = None
    args: Optional[Dict[str, Any]] = None
    result: Optional[Any] = None
    duration_ms: Optional[float] = None
    status: Optional[str] = None  # ok, error
    
    # Error fields
    error: Optional[str] = None
    stack: Optional[str] = None
    
    # LLM fields
    model: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None
    
    # File edit fields
    file: Optional[str] = None
    lines_added: Optional[int] = None
    lines_removed: Optional[int] = None
    
    # Arbitrary metadata
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict, omitting None fields for compact JSONL."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}

    def to_json(self) -> str:
        """Serialize to a single JSON line without newlines."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TraceEvent:
        known_fields = {
            "ts", "session", "type", "tool", "args", "result", "duration_ms",
            "status", "error", "stack", "model", "tokens_in", "tokens_out",
            "cost_usd", "file", "lines_added", "lines_removed", "metadata"
        }
        kwargs: Dict[str, Any] = {}
        extra: Dict[str, Any] = {}
        
        for k, v in data.items():
            if k in known_fields:
                kwargs[k] = v
            else:
                extra[k] = v
        
        if extra:
            existing_meta = kwargs.get("metadata") or {}
            existing_meta.update(extra)
            kwargs["metadata"] = existing_meta

        return cls(**kwargs)

    @classmethod
    def from_json(cls, line: str) -> Optional[TraceEvent]:
        line = line.strip()
        if not line:
            return None
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return cls.from_dict(data)
        except Exception:
            return None
        return None
