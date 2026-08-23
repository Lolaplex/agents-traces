"""
FastMCP Server for agents-trace.
Enables agents to query execution traces, diagnose tool errors, inspect token/cost metrics, and record trace events.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from .models import TraceEvent
from .stats import compute_model_breakdown, compute_stats
from .store import TraceStore
from .timeline import render_timeline

mcp = FastMCP("agents-traces")
store = TraceStore()


@mcp.tool()
def get_last_session_trace(session_id: Optional[str] = None, limit: int = 40) -> str:
    """
    Get the execution trace and timeline of the current or most recent agent session.
    Useful for self-diagnosis when a loop fails or when inspecting recent tool calls.
    
    Args:
        session_id: Optional session identifier. If not provided, inspects the latest active session.
        limit: Max number of recent events to return (default 40).
    """
    target_sid = session_id or store.get_last_session_id()
    if not target_sid:
        return "No trace sessions recorded yet."

    events = store.get_events_for_session(target_sid)
    if not events:
        return f"No events found for session: {target_sid}"

    if len(events) > limit:
        events = events[-limit:]

    return render_timeline(events, session_id=target_sid)


@mcp.tool()
def get_recent_errors(limit: int = 10, session_id: Optional[str] = None) -> str:
    """
    Query recent tool failures and errors across sessions or in a specific session.
    Helps agents self-diagnose why specific tool calls or commands failed.
    
    Args:
        limit: Number of recent errors to retrieve (default 10).
        session_id: Optional session ID filter.
    """
    errors = store.get_recent_errors(limit=limit, session_id=session_id)
    if not errors:
        return "No errors recorded in recent traces. All systems green."

    results = []
    for e in errors:
        results.append({
            "ts": e.ts,
            "session": e.session,
            "tool": e.tool,
            "args": e.args,
            "error": e.error,
            "stack": e.stack,
            "status": e.status,
        })
    return json.dumps(results, indent=2)


@mcp.tool()
def get_session_stats(days: int = 1) -> str:
    """
    Get aggregated usage statistics (tokens, costs, tool success/error rate, top tools) for recent days.
    
    Args:
        days: Number of days to aggregate (default 1 = today).
    """
    files = store.list_trace_files(days=days)
    events = store.iter_events(files=files)
    stats = compute_stats(events)
    return json.dumps(stats, indent=2)


@mcp.tool()
def get_model_stats(model: Optional[str] = None, days: int = 7) -> str:
    """
    Get empirical performance metrics, tool failure rates, and detected operational traps broken down by model.
    Enables self-diagnosis and empirical adaptation per model family.
    
    Args:
        model: Optional filter for a specific model (e.g. 'gemini-3.7-flash', 'claude-3.7-sonnet'). If omitted, breaks down all active models.
        days: Number of recent days of trace logs to analyze (default 7).
    """
    files = store.list_trace_files(days=days)
    events = store.iter_events(files=files)
    breakdown = compute_model_breakdown(events)
    
    if model:
        q = model.strip().lower()
        matched = {m: info for m, info in breakdown.get("models", {}).items() if q in m.lower()}
        return json.dumps({"query": model, "matches": matched}, indent=2)
        
    return json.dumps(breakdown, indent=2)


@mcp.tool()
def record_trace(
    session: str,
    type: str,
    tool: Optional[str] = None,
    args: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
    error: Optional[str] = None,
    duration_ms: Optional[float] = None,
    model: Optional[str] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    cost_usd: Optional[float] = None,
    file: Optional[str] = None,
    lines_added: Optional[int] = None,
    lines_removed: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Record an observability trace event into the local daily append-only JSONL log.
    
    Args:
        session: Session identifier (e.g. 's-1234')
        type: Event type ('tool_call', 'llm_call', 'file_edit', 'error', 'session_start', 'session_end', 'custom')
        tool: Name of the executed tool (for tool_call / error)
        args: Tool arguments
        status: 'ok' or 'error'
        error: Error message if failed
        duration_ms: Execution duration in milliseconds
        model: LLM model name
        tokens_in: Input tokens consumed
        tokens_out: Output tokens generated
        cost_usd: Estimated cost in USD
        file: Modified file path
        lines_added: Lines added
        lines_removed: Lines removed
        metadata: Arbitrary additional data
    """
    event = TraceEvent(
        session=session,
        type=type,
        tool=tool,
        args=args,
        status=status,
        error=error,
        duration_ms=duration_ms,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        file=file,
        lines_added=lines_added,
        lines_removed=lines_removed,
        metadata=metadata,
    )
    store.append(event)
    return json.dumps({"status": "recorded", "ts": event.ts, "session": session})


@mcp.tool()
def ingest_traces() -> str:
    """
    Auto-ingest recent IDE transcripts from Antigravity, Claude, Cursor, and Roo-Cline into local daily traces.
    """
    from .ingest import ingest_all_ide_transcripts
    res = ingest_all_ide_transcripts(store=store)
    return json.dumps(res, indent=2)
