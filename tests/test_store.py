import tempfile
from pathlib import Path
from agents_traces.models import TraceEvent
from agents_traces.store import TraceStore


def test_trace_store_append_and_iter():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = TraceStore(traces_dir=tmpdir)
        
        e1 = TraceEvent(session="s-1", type="tool_call", tool="search_docs", duration_ms=12.5, status="ok")
        e2 = TraceEvent(session="s-1", type="llm_call", model="gemini-3.7-flash", tokens_in=100, tokens_out=50, cost_usd=0.0001)
        e3 = TraceEvent(session="s-2", type="error", tool="run_command", error="Command failed")
        
        store.append(e1)
        store.append(e2)
        store.append(e3)
        
        # Test iterator
        events = list(store.iter_events())
        assert len(events) == 3
        
        # Test session filter
        s1_events = store.get_events_for_session("s-1")
        assert len(s1_events) == 2
        assert s1_events[0].type == "tool_call"
        assert s1_events[1].type == "llm_call"
        
        # Test last session ID
        assert store.get_last_session_id() in ["s-1", "s-2"]
        
        # Test recent errors
        errors = store.get_recent_errors()
        assert len(errors) == 1
        assert errors[0].error == "Command failed"


def test_trace_store_cleanup():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = TraceStore(traces_dir=tmpdir)
        
        # Create a fake old file
        old_file = Path(tmpdir) / "2020-01-01.jsonl"
        old_file.write_text('{"ts": "2020-01-01T00:00:00Z", "session": "old"}\n')
        
        # Create today's file
        today_file = store.get_date_file_path()
        today_file.write_text('{"ts": "2026-08-20T00:00:00Z", "session": "new"}\n')
        
        deleted = store.cleanup_old_traces(keep_days=30, compress=False)
        assert deleted == 1
        assert not old_file.exists()
        assert today_file.exists()
