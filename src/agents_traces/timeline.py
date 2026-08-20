from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional
from .models import TraceEvent


def format_ts_time(ts_str: str) -> str:
    """Extract HH:MM:SS from ISO timestamp string."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%H:%M:%S")
    except Exception:
        return ts_str[:8] if len(ts_str) >= 8 else ts_str


def format_args_short(args: Optional[dict]) -> str:
    """Format tool arguments into a compact single-line string."""
    if not args:
        return ""
    items = []
    for k, v in args.items():
        if isinstance(v, str):
            val_str = f'"{v[:30]}..."' if len(v) > 30 else f'"{v}"'
        elif isinstance(v, (int, float, bool)):
            val_str = str(v)
        else:
            val_str = "{...}" if isinstance(v, dict) else "[...]"
        items.append(f"{k}={val_str}")
    return ", ".join(items)


def render_timeline(events: List[TraceEvent], session_id: Optional[str] = None) -> str:
    """Render a colored LangSmith-like timeline in terminal."""
    if not events:
        return f"\033[33mNo trace events found for session: {session_id or 'unknown'}\033[0m"

    lines: List[str] = []
    sid = session_id or events[0].session
    
    # Calculate overall session stats
    total_tokens = 0
    total_cost = 0.0
    models_used = set()
    start_dt = None
    end_dt = None
    
    for e in events:
        if e.tokens_in:
            total_tokens += e.tokens_in
        if e.tokens_out:
            total_tokens += e.tokens_out
        if e.cost_usd:
            total_cost += e.cost_usd
        if e.model:
            models_used.add(e.model)
        try:
            dt = datetime.fromisoformat(e.ts.replace("Z", "+00:00"))
            if start_dt is None or dt < start_dt:
                start_dt = dt
            if end_dt is None or dt > end_dt:
                end_dt = dt
        except Exception:
            pass

    duration_str = ""
    if start_dt and end_dt:
        dur_sec = (end_dt - start_dt).total_seconds()
        duration_str = f" in {dur_sec:.1f}s"

    model_str = f" (Model: {', '.join(models_used)})" if models_used else ""
    
    lines.append(f"\033[1;36m=== Timeline for Session: {sid}{model_str} ===\033[0m\n")

    for e in events:
        t_str = format_ts_time(e.ts)
        
        if e.type == "session_start":
            mod = f" (Model: {e.model})" if e.model else ""
            lines.append(f"\033[90m[{t_str}]\033[0m \033[1;34m▶ Session started{mod}\033[0m")
            
        elif e.type == "tool_call":
            tool_name = e.tool or "unknown_tool"
            args_str = format_args_short(e.args)
            call_sig = f"{tool_name}({args_str})" if args_str else tool_name
            dur_str = f" - {e.duration_ms:.0f}ms" if e.duration_ms is not None else ""
            
            if e.status == "error" or e.error:
                lines.append(
                    f"\033[90m[{t_str}]\033[0m   \033[31m⚙ Tool: {call_sig} (FAILED{dur_str})\033[0m"
                )
                if e.error:
                    first_err_line = e.error.strip().split("\n")[0]
                    lines.append(f"             \033[31m└─ Error: {first_err_line}\033[0m")
            else:
                lines.append(
                    f"\033[90m[{t_str}]\033[0m   \033[32m⚙ Tool: \033[0m{call_sig} \033[90m(OK{dur_str})\033[0m"
                )
                
        elif e.type == "file_edit":
            f_path = e.file or "unknown_file"
            added = f"+\033[32m{e.lines_added}\033[0m" if e.lines_added is not None else "+0"
            removed = f"-\033[31m{e.lines_removed}\033[0m" if e.lines_removed is not None else "-0"
            lines.append(
                f"\033[90m[{t_str}]\033[0m   \033[35m✍ File Edit: \033[0m{f_path} ({added} / {removed} lines)"
            )
            
        elif e.type == "llm_call":
            mod = e.model or "llm"
            tin = e.tokens_in or 0
            tout = e.tokens_out or 0
            dur_str = f" - {e.duration_ms:.0f}ms" if e.duration_ms is not None else ""
            lines.append(
                f"\033[90m[{t_str}]\033[0m   \033[36m✦ LLM Call: \033[0m{mod} \033[90m(In: {tin}, Out: {tout}{dur_str})\033[0m"
            )
            
        elif e.type == "error":
            err_msg = (e.error or "Error occurred").strip().split("\n")[0]
            lines.append(f"\033[90m[{t_str}]\033[0m   \033[1;31m⚠ Error: {err_msg}\033[0m")
            
        elif e.type == "session_end":
            lines.append(f"\033[90m[{t_str}]\033[0m \033[1;32m■ Session ended\033[0m")
            
        else:
            # Custom event
            lines.append(f"\033[90m[{t_str}]\033[0m   \033[94m• [{e.type}]\033[0m {e.to_json()}")

    # Summary footer
    tok_str = f"{total_tokens:,}"
    cost_str = f"${total_cost:.4f}"
    lines.append(
        f"\n\033[90m[{format_ts_time(events[-1].ts)}]\033[0m \033[1m■ Session completed{duration_str}\033[0m "
        f"(Tokens: \033[1m{tok_str}\033[0m, Cost: \033[1;32m{cost_str}\033[0m)"
    )
    return "\n".join(lines)
