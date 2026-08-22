"""
Automatic zero-overhead trace interceptors and decorators for FastMCP servers and agent tools.
Strictly guarded: only logs if ~/.agents/traces/ exists or AGENTS_TRACES_DIR is set.
"""

from __future__ import annotations

import functools
import inspect
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .models import TraceEvent
from .store import TraceStore, get_default_traces_dir

_global_store: Optional[TraceStore] = None
_process_session_id: Optional[str] = None


def _process_session() -> str:
    """Stable per-process session id — every live event from this process
    shares it, and different processes never collide."""
    global _process_session_id
    if _process_session_id is None:
        import uuid

        _process_session_id = f"proc-{uuid.uuid4().hex[:8]}"
    return _process_session_id


def is_tracing_enabled() -> bool:
    """Check if traces directory exists or explicit environment variable is set."""
    if os.environ.get("AGENTS_TRACES_DIR"):
        return True
    return get_default_traces_dir().is_dir()


def get_global_store() -> TraceStore:
    global _global_store
    if _global_store is None:
        _global_store = TraceStore()
    return _global_store


def _summarize(res: Any, limit: int = 300) -> Optional[str]:
    """Truncated result summary — enough to verify hits offline without
    storing full payloads."""
    if res is None:
        return None
    try:
        text = res if isinstance(res, str) else json.dumps(res, default=str)
    except Exception:
        text = str(res)
    return text[:limit]


def append_trace(
    type: str = "tool_call",
    tool: Optional[str] = None,
    args: Optional[Dict[str, Any]] = None,
    status: str = "ok",
    error: Optional[str] = None,
    duration_ms: Optional[float] = None,
    session: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Instantly append a trace event to the daily JSONL file in <0.05ms if tracing is enabled."""
    if not is_tracing_enabled():
        return

    sid = session or os.environ.get("AGENTS_SESSION_ID") or _process_session()
    event = TraceEvent(
        session=sid,
        type=type,
        origin="live",
        tool=tool,
        args=args,
        status=status,
        error=error,
        duration_ms=round(duration_ms, 1) if duration_ms is not None else None,
        **kwargs,
    )
    get_global_store().append(event)


def trace_call(func: Optional[Callable] = None, *, name: Optional[str] = None):
    """
    Decorator for tool functions. Automatically measures duration, catches errors,
    and appends every execution to ~/.agents/traces/YYYY-MM-DD.jsonl with zero overhead.
    """

    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if not is_tracing_enabled():
                    return await fn(*args, **kwargs)
                t0 = time.perf_counter()
                try:
                    res = await fn(*args, **kwargs)
                    dur = (time.perf_counter() - t0) * 1000.0
                    append_trace(
                        type="tool_call",
                        tool=tool_name,
                        args=kwargs,
                        status="ok",
                        duration_ms=dur,
                        result=_summarize(res),
                    )
                    return res
                except Exception as e:
                    dur = (time.perf_counter() - t0) * 1000.0
                    append_trace(
                        type="tool_call",
                        tool=tool_name,
                        args=kwargs,
                        status="error",
                        error=str(e),
                        duration_ms=dur,
                    )
                    raise

            return async_wrapper
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                if not is_tracing_enabled():
                    return fn(*args, **kwargs)
                t0 = time.perf_counter()
                try:
                    res = fn(*args, **kwargs)
                    dur = (time.perf_counter() - t0) * 1000.0
                    append_trace(
                        type="tool_call",
                        tool=tool_name,
                        args=kwargs,
                        status="ok",
                        duration_ms=dur,
                        result=_summarize(res),
                    )
                    return res
                except Exception as e:
                    dur = (time.perf_counter() - t0) * 1000.0
                    append_trace(
                        type="tool_call",
                        tool=tool_name,
                        args=kwargs,
                        status="error",
                        error=str(e),
                        duration_ms=dur,
                    )
                    raise

            return sync_wrapper

    if func is not None:
        return decorator(func)
    return decorator


def auto_trace_mcp(mcp_instance: Any) -> None:
    """
    Auto-wraps all FastMCP tools registered on an MCP instance so that every tool call
    is automatically traced into ~/.agents/traces/ without altering tool code.
    Only active if ~/.agents/traces/ exists.
    """
    if not is_tracing_enabled():
        return

    try:
        tool_manager = getattr(mcp_instance, "_tool_manager", None)
        if tool_manager and hasattr(tool_manager, "_tools"):
            for t_name, tool_obj in tool_manager._tools.items():
                original_fn = getattr(tool_obj, "fn", None)
                if original_fn and not getattr(original_fn, "_is_traced", False):
                    wrapped = trace_call(original_fn, name=t_name)
                    wrapped._is_traced = True  # type: ignore
                    tool_obj.fn = wrapped
    except Exception:
        pass
