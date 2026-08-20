from agents_traces.models import TraceEvent
from agents_traces.stats import compute_stats, format_stats_text


def test_compute_stats_aggregation():
    events = [
        TraceEvent(session="s-1", type="tool_call", tool="search_docs", duration_ms=10.0, status="ok"),
        TraceEvent(session="s-1", type="tool_call", tool="run_command", duration_ms=50.0, status="error", error="Exit 1"),
        TraceEvent(session="s-1", type="llm_call", model="gemini-3.7-flash", tokens_in=1000, tokens_out=500, cost_usd=0.005),
        TraceEvent(session="s-1", type="file_edit", file="main.py", lines_added=10, lines_removed=2),
        TraceEvent(session="s-2", type="tool_call", tool="search_docs", duration_ms=20.0, status="ok"),
    ]
    
    stats = compute_stats(events)
    
    assert stats["sessions_count"] == 2
    assert stats["total_tokens"] == 1500
    assert stats["est_cost_usd"] == 0.005
    assert stats["tool_calls_total"] == 3
    assert stats["tool_calls_ok"] == 2
    assert stats["tool_calls_error"] == 1
    assert stats["tool_success_rate"] == 66.7
    assert stats["file_edits_count"] == 1
    assert stats["lines_added"] == 10
    assert stats["lines_removed"] == 2
    assert len(stats["top_tools"]) == 2
    assert len(stats["top_errors"]) == 1
    
    text = format_stats_text(stats)
    assert "Sessions:" in text
    assert "search_docs" in text
