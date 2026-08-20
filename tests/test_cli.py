import io
import json
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
import pytest
from agents_traces.__main__ import main
from agents_traces.models import TraceEvent
from agents_traces.store import TraceStore


def test_cli_record_and_inspect(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("AGENTS_TRACES_DIR", tmpdir)
        
        # 1. Record session_start
        monkeypatch.setattr("sys.argv", [
            "agents-traces", "record",
            "--session", "sess-abc",
            "--type", "session_start",
            "--data", json.dumps({"model": "gemini-3.7-flash"}),
        ])
        f = io.StringIO()
        with redirect_stdout(f):
            main()
        assert "Recorded event" in f.getvalue()
        
        # 2. Record tool_call
        monkeypatch.setattr("sys.argv", [
            "agents-traces", "record",
            "--session", "sess-abc",
            "--type", "tool_call",
            "--tool", "search_docs",
            "--status", "ok",
            "--duration", "14.2",
        ])
        f = io.StringIO()
        with redirect_stdout(f):
            main()
            
        # 3. Record tool error
        monkeypatch.setattr("sys.argv", [
            "agents-traces", "record",
            "--session", "sess-abc",
            "--type", "tool_call",
            "--tool", "run_command",
            "--status", "error",
            "--error", "UnicodeDecodeError line 4",
            "--duration", "35.0",
        ])
        f = io.StringIO()
        with redirect_stdout(f):
            main()

        # 4. Inspect session
        monkeypatch.setattr("sys.argv", ["agents-traces", "inspect", "sess-abc"])
        f = io.StringIO()
        with redirect_stdout(f):
            main()
        output = f.getvalue()
        assert "Timeline for Session: sess-abc" in output
        assert "search_docs" in output
        assert "UnicodeDecodeError" in output

        # 5. Stats
        monkeypatch.setattr("sys.argv", ["agents-traces", "stats", "--days", "1"])
        f = io.StringIO()
        with redirect_stdout(f):
            main()
        stats_out = f.getvalue()
        assert "agents-traces stats" in stats_out
        assert "search_docs" in stats_out
        assert "UnicodeDecodeError" in stats_out

        # 6. Sessions list
        monkeypatch.setattr("sys.argv", ["agents-traces", "sessions"])
        f = io.StringIO()
        with redirect_stdout(f):
            main()
        sessions_out = f.getvalue()
        assert "sess-abc" in sessions_out
