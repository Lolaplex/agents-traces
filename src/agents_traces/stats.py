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


def compute_model_breakdown(events: Iterable[TraceEvent]) -> Dict[str, Any]:
    """Compute per-model aggregated metrics and empirical failure insights."""
    event_list = list(events)
    
    # 1. Map sessions to active model if known
    session_to_model: Dict[str, str] = {}
    for e in event_list:
        if e.model and e.session:
            session_to_model[e.session] = e.model

    # 2. Group events by model
    model_events: Dict[str, List[TraceEvent]] = defaultdict(list)
    for e in event_list:
        m = e.model or session_to_model.get(e.session or "", "unspecified")
        model_events[m].append(e)

    breakdown: Dict[str, Any] = {}
    for model_name, m_events in sorted(model_events.items(), key=lambda x: len(x[1]), reverse=True):
        m_stats = compute_stats(m_events)
        
        # Derive empirical traps & insights
        insights = []
        for t in m_stats.get("top_tools", []):
            t_name = t["tool"]
            t_count = t["count"]
            t_errs = t["errors"]
            if t_count >= 2 and (t_errs / t_count) >= 0.15:
                rate_pct = round(t_errs / t_count * 100, 1)
                insights.append(f"High failure rate on `{t_name}` ({rate_pct}% errors in {t_count} calls).")
        
        if m_stats.get("tool_calls_total", 0) > 0 and m_stats.get("tool_success_rate", 100) < 80.0:
            insights.append("Overall tool success rate under 80% — recommend stricter step verification.")
            
        breakdown[model_name] = {
            "model": model_name,
            "stats": m_stats,
            "empirical_insights": insights,
        }

    return {
        "models_count": len(breakdown),
        "total_events": len(event_list),
        "models": breakdown,
    }


def format_model_breakdown_text(data: Dict[str, Any], label: str = "7 Tage") -> str:
    """Format per-model telemetry and insights for terminal display."""
    lines = []
    lines.append(f"\033[1;36m=== agents-traces model analysis ({label}) ===\033[0m")
    lines.append(f"• Active Models: \033[1m{data['models_count']}\033[0m | Total Events: {data['total_events']}")
    lines.append("")

    for m_name, m_info in data.get("models", {}).items():
        st = m_info["stats"]
        lines.append(f"\033[1;33m► Model: {m_name}\033[0m")
        lines.append(f"  • Tokens:      {st['total_tokens']:,} (Cost: ${st['est_cost_usd']:.4f})")
        lines.append(f"  • Tool Calls:  {st['tool_calls_total']} (Success: \033[32m{st['tool_success_rate']:.1f}%\033[0m | Errors: {st['tool_calls_error']})")
        
        if st["top_tools"]:
            tool_parts = []
            for t in st["top_tools"][:4]:
                err_note = f" (\033[31m{t['errors']} err\033[0m)" if t["errors"] > 0 else ""
                tool_parts.append(f"{t['tool']}: {t['count']}x{err_note}")
            lines.append(f"  • Top Tools:   {', '.join(tool_parts)}")

        insights = m_info.get("empirical_insights", [])
        if insights:
            lines.append("  • \033[1;35mEmpirical Traps Detected:\033[0m")
            for ins in insights:
                lines.append(f"    ⚠️  {ins}")
        lines.append("")

    return "\n".join(lines)

