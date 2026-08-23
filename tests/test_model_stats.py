from agents_traces.models import TraceEvent
from agents_traces.stats import compute_model_breakdown, format_model_breakdown_text


def test_compute_model_breakdown():
    events = [
        TraceEvent(session="s-1", type="llm_call", model="gemini-3.7-flash", tokens_in=2000, tokens_out=1000, cost_usd=0.003),
        TraceEvent(session="s-1", type="tool_call", tool="view_file", duration_ms=10.0, status="ok"),
        TraceEvent(session="s-1", type="tool_call", tool="replace_file_content", duration_ms=30.0, status="error", error="Indent error"),
        TraceEvent(session="s-1", type="tool_call", tool="replace_file_content", duration_ms=25.0, status="error", error="Target not found"),
        TraceEvent(session="s-2", type="llm_call", model="claude-3.7-sonnet", tokens_in=5000, tokens_out=2000, cost_usd=0.025),
        TraceEvent(session="s-2", type="tool_call", tool="search_docs", duration_ms=15.0, status="ok"),
        TraceEvent(session="s-2", type="tool_call", tool="run_command", duration_ms=120.0, status="ok"),
    ]

    breakdown = compute_model_breakdown(events)
    assert breakdown["models_count"] == 2
    assert breakdown["total_events"] == 7

    models = breakdown["models"]
    assert "gemini-3.7-flash" in models
    assert "claude-3.7-sonnet" in models

    g_info = models["gemini-3.7-flash"]
    assert g_info["stats"]["tool_calls_total"] == 3
    assert g_info["stats"]["tool_calls_error"] == 2
    assert len(g_info["empirical_insights"]) > 0
    assert any("replace_file_content" in ins for ins in g_info["empirical_insights"])

    c_info = models["claude-3.7-sonnet"]
    assert c_info["stats"]["tool_calls_total"] == 2
    assert c_info["stats"]["tool_calls_error"] == 0
    assert c_info["stats"]["tool_success_rate"] == 100.0

    text = format_model_breakdown_text(breakdown)
    assert "Model: gemini-3.7-flash" in text
    assert "Model: claude-3.7-sonnet" in text
    assert "Empirical Traps Detected" in text
