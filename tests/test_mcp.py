import json
import tempfile
from pathlib import Path
from agents_traces.models import TraceEvent
from agents_traces.store import TraceStore
import agents_traces.mcp_server as mcp_mod


def test_mcp_tools(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_store = TraceStore(traces_dir=tmpdir)
        monkeypatch.setattr(mcp_mod, "store", fake_store)
        
        # Test record_trace
        res = mcp_mod.record_trace(
            session="s-mcp",
            type="tool_call",
            tool="run_command",
            args={"cmd": "ls"},
            status="error",
            error="NotFound",
            duration_ms=45.0,
        )
        data = json.loads(res)
        assert data["status"] == "recorded"
        assert data["session"] == "s-mcp"
        
        # Test get_last_session_trace
        trace_str = mcp_mod.get_last_session_trace(session_id="s-mcp")
        assert "run_command" in trace_str
        assert "FAILED" in trace_str
        
        # Test get_recent_errors
        errors_str = mcp_mod.get_recent_errors()
        errors = json.loads(errors_str)
        assert len(errors) == 1
        assert errors[0]["error"] == "NotFound"
        
        # Test get_session_stats
        stats_str = mcp_mod.get_session_stats(days=1)
        stats = json.loads(stats_str)
        assert stats["tool_calls_total"] == 1
        assert stats["tool_calls_error"] == 1

        fake_store.append(
            TraceEvent(
                session="s-mcp",
                type="message",
                origin="ingested",
                metadata={"source": "cursor", "role": "user", "content": "hello traces"},
            )
        )
        snap = mcp_mod.session_snap(limit=5)
        assert "hello traces" in snap
        grep = mcp_mod.session_grep("hello traces")
        assert "hello traces" in grep
        tail = mcp_mod.session_tail(session_id="s-mcp", limit=5)
        assert "Session" in tail
