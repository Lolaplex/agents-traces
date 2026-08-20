from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List
from .models import TraceEvent


def compute_stats(events: Iterable[TraceEvent]) -> Dict[str, Any]:
    """Compute aggregated metrics in a single streaming pass."""
    sessions = set()
    total_events = 0
    tokens_in = 0
    tokens_out = 0
    cost_usd = 0.0
    
    tool_calls_total = 0
    tool_calls_ok = 0
    tool_calls_error = 0
    
    tool_counts: Counter[str] = Counter()
    tool_durations: Dict[str, List[float]] = defaultdict(list)
    tool_errors: Counter[str] = Counter()
    
    file_edits_count = 0
    lines_added = 0
    lines_removed = 0
    
    error_counter: Counter[str] = Counter()
    error_examples: Dict[str, str] = {}

    for e in events:
        total_events += 1
        if e.session:
            sessions.add(e.session)
            
        if e.tokens_in:
            tokens_in += e.tokens_in
        if e.tokens_out:
            tokens_out += e.tokens_out
        if e.cost_usd:
            cost_usd += e.cost_usd
            
        if e.type == "tool_call":
            tool_calls_total += 1
            tool_name = e.tool or "unknown"
            tool_counts[tool_name] += 1
            if e.duration_ms is not None:
                tool_durations[tool_name].append(e.duration_ms)
                
            if e.status == "error" or e.error:
                tool_calls_error += 1
                tool_errors[tool_name] += 1
            else:
                tool_calls_ok += 1
                
        if e.type == "file_edit":
            file_edits_count += 1
            if e.lines_added:
                lines_added += e.lines_added
            if e.lines_removed:
                lines_removed += e.lines_removed
                
        if e.type == "error" or e.status == "error" or e.error:
            err_msg = (e.error or "Unknown error").strip()
            # Truncate for categorization
            short_err = err_msg.split("\n")[0][:100]
            error_counter[short_err] += 1
            if short_err not in error_examples and e.tool:
                error_examples[short_err] = f"in {e.tool}"

    total_tokens = tokens_in + tokens_out
    success_rate = (tool_calls_ok / tool_calls_total * 100) if tool_calls_total > 0 else 100.0

    top_tools = []
    for tool_name, count in tool_counts.most_common(10):
        durs = tool_durations.get(tool_name, [])
        avg_dur = sum(durs) / len(durs) if durs else 0.0
        top_tools.append({
            "tool": tool_name,
            "count": count,
            "errors": tool_errors.get(tool_name, 0),
            "avg_duration_ms": round(avg_dur, 1),
        })

    top_errors = []
    for err_text, count in error_counter.most_common(5):
        context = error_examples.get(err_text, "")
        top_errors.append({
            "error": err_text,
            "count": count,
            "context": context,
        })

    return {
        "sessions_count": len(sessions),
        "total_events": total_events,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "total_tokens": total_tokens,
        "est_cost_usd": round(cost_usd, 5),
        "tool_calls_total": tool_calls_total,
        "tool_calls_ok": tool_calls_ok,
        "tool_calls_error": tool_calls_error,
        "tool_success_rate": round(success_rate, 1),
        "top_tools": top_tools,
        "file_edits_count": file_edits_count,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "top_errors": top_errors,
    }


def format_stats_text(stats: Dict[str, Any], label: str = "Heute") -> str:
    """Format stats into clean terminal text output."""
    lines = []
    lines.append(f"\033[1;36m=== agents-traces stats ({label}) ===\033[0m")
    lines.append(f"• Sessions:      \033[1m{stats['sessions_count']}\033[0m")
    
    # Tokens & Cost
    tin_k = f"{stats['tokens_in'] / 1000:.1f}k" if stats['tokens_in'] >= 1000 else str(stats['tokens_in'])
    tout_k = f"{stats['tokens_out'] / 1000:.1f}k" if stats['tokens_out'] >= 1000 else str(stats['tokens_out'])
    ttot_k = f"{stats['total_tokens'] / 1000:.1f}k" if stats['total_tokens'] >= 1000 else str(stats['total_tokens'])
    
    lines.append(f"• Total Tokens:  \033[1m{stats['total_tokens']:,}\033[0m  (In: {tin_k} | Out: {tout_k})")
    lines.append(f"• Est. Cost:     \033[1;32m${stats['est_cost_usd']:.4f}\033[0m")
    
    # Tool Calls
    err_rate = 100.0 - stats['tool_success_rate']
    err_color = "\033[32m" if stats['tool_calls_error'] == 0 else "\033[31m"
    lines.append(
        f"• Tool Calls:    \033[1m{stats['tool_calls_total']}\033[0m "
        f"(\033[32m{stats['tool_success_rate']:.1f}% OK\033[0m / {err_color}{err_rate:.1f}% Err\033[0m)"
    )
    
    # Top tools
    if stats["top_tools"]:
        tool_strs = [f"{t['tool']} ({t['count']}x)" for t in stats["top_tools"][:5]]
        lines.append(f"• Top Tools:     {', '.join(tool_strs)}")
        
    # File Edits
    if stats["file_edits_count"] > 0:
        lines.append(
            f"• File Edits:    \033[1m{stats['file_edits_count']}\033[0m "
            f"(+\033[32m{stats['lines_added']}\033[0m / -\033[31m{stats['lines_removed']}\033[0m lines)"
        )
        
    # Errors
    if stats["top_errors"]:
        lines.append("• Top Errors:")
        for err in stats["top_errors"]:
            ctx = f" ({err['context']})" if err['context'] else ""
            lines.append(f"   \033[31m✖\033[0m {err['count']}x {err['error']}{ctx}")
    else:
        lines.append("• Errors:        \033[32m0 errors (all green)\033[0m")
        
    return "\n".join(lines)
