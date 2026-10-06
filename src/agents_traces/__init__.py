"""
agents-traces: Ultra-fast, zero-bloat local JSONL observability for AI coding agents.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional

from .audit import (
    GENESIS,
    AuditError,
    AuditRecord,
    AuditResult,
    Finding,
    Scope,
    SealedLink,
    VerifyResult,
    audit_replay,
    load_scope,
    load_sealed_jsonl,
    parse_transcript_text,
    seal_records,
    verify_chain,
)
from .interceptor import append_trace, auto_trace_mcp, trace_call
from .models import TraceEvent
from .stats import compute_stats
from .store import TraceStore, get_default_traces_dir
from .timeline import render_timeline

__version__ = "0.1.0"


class SessionTracer:
    """Convenience tracer for Python agent scripts and loops."""
    def __init__(self, session_id: str, store: Optional[TraceStore] = None):
        self.session_id = session_id
        self.store = store or TraceStore()

    def start(self, model: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        self.store.append(TraceEvent(
            session=self.session_id,
            type="session_start",
            model=model,
            metadata=metadata,
        ))

    def end(self) -> None:
        self.store.append(TraceEvent(
            session=self.session_id,
            type="session_end",
        ))

    def record_tool(
        self,
        tool: str,
        args: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
        status: str = "ok",
        error: Optional[str] = None,
        result: Optional[Any] = None,
    ) -> None:
        self.store.append(TraceEvent(
            session=self.session_id,
            type="tool_call",
            tool=tool,
            args=args,
            duration_ms=duration_ms,
            status=status,
            error=error,
            result=result,
        ))

    def record_llm(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: Optional[float] = None,
        duration_ms: Optional[float] = None,
    ) -> None:
        self.store.append(TraceEvent(
            session=self.session_id,
            type="llm_call",
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
        ))

    def record_file_edit(self, file: str, lines_added: int = 0, lines_removed: int = 0) -> None:
        self.store.append(TraceEvent(
            session=self.session_id,
            type="file_edit",
            file=file,
            lines_added=lines_added,
            lines_removed=lines_removed,
        ))

    def record_error(self, error: str, tool: Optional[str] = None, stack: Optional[str] = None) -> None:
        self.store.append(TraceEvent(
            session=self.session_id,
            type="error",
            error=error,
            tool=tool,
            stack=stack,
        ))

    @contextmanager
    def span_tool(self, tool: str, args: Optional[Dict[str, Any]] = None) -> Generator[None, None, None]:
        """Context manager to measure tool execution duration and catch exceptions automatically."""
        t0 = time.perf_counter()
        try:
            yield
            dur = (time.perf_counter() - t0) * 1000.0
            self.record_tool(tool=tool, args=args, duration_ms=dur, status="ok")
        except Exception as e:
            dur = (time.perf_counter() - t0) * 1000.0
            self.record_tool(tool=tool, args=args, duration_ms=dur, status="error", error=str(e))
            raise


__all__ = [
    "TraceEvent",
    "TraceStore",
    "SessionTracer",
    "trace_call",
    "auto_trace_mcp",
    "append_trace",
    "compute_stats",
    "render_timeline",
    "get_default_traces_dir",
    "GENESIS",
    "AuditError",
    "AuditRecord",
    "AuditResult",
    "Finding",
    "Scope",
    "SealedLink",
    "VerifyResult",
    "audit_replay",
    "load_scope",
    "load_sealed_jsonl",
    "parse_transcript_text",
    "seal_records",
    "verify_chain",
]
