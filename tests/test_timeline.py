from agents_traces.models import TraceEvent
from agents_traces.timeline import render_timeline


def test_render_timeline_format():
    events = [
        TraceEvent(session="s-test", type="session_start", model="gemini-3.7-flash"),
        TraceEvent(session="s-test", type="tool_call", tool="search_docs", args={"query": "FastMCP"}, duration_ms=15.0, status="ok"),
        TraceEvent(session="s-test", type="tool_call", tool="run_command", args={"cmd": "pytest"}, duration_ms=120.0, status="error", error="AssertionError on line 42"),
        TraceEvent(session="s-test", type="file_edit", file="src/main.py", lines_added=5, lines_removed=1),
        TraceEvent(session="s-test", type="llm_call", model="gemini-3.7-flash", tokens_in=2000, tokens_out=400, cost_usd=0.002),
        TraceEvent(session="s-test", type="session_end"),
    ]
    
    output = render_timeline(events, session_id="s-test")
    assert "Timeline for Session: s-test" in output
    assert "Session started" in output
    assert "search_docs" in output
    assert "FAILED" in output
    assert "AssertionError on line 42" in output
    assert "src/main.py" in output
    assert "Session completed" in output
