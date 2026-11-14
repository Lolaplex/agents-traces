"""Session chat export from traces: API, CLI, MCP."""

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from agents_traces.assemble import record_message
from agents_traces.content import INLINE_CONTENT_CHARS
from agents_traces.export import CHAT_FORMAT, ExportError, export_session, session_chat
from agents_traces.models import TraceEvent
from agents_traces.store import TraceStore

SECRET = "sk-proj-ab12cd34ef56gh78zz90"


def _seed(store: TraceStore) -> str:
    sid = "ses_export"
    store.append(
        TraceEvent(session=sid, type="session_start", model="demo-model")
    )
    record_message(
        sid,
        "user",
        "hello café",
        store=store,
        binary_refs=[{"path": "/tmp/shot.png", "media_type": "image/png"}],
    )
    store.append(
        TraceEvent(
            session=sid,
            type="tool_call",
            tool="search",
            args={"q": "traces"},
            status="ok",
            result="found 2",
        )
    )
    record_message(sid, "assistant", f"use {SECRET} please", store=store)
    store.append(
        TraceEvent(
            session=sid,
            type="frame",
            metadata={"kind": "compaction", "content": "compacted earlier"},
        )
    )
    record_message("ses_other", "user", "other-session-marker", store=store)
    return sid


def test_export_markdown_and_json(tmp_path: Path):
    store = TraceStore(tmp_path)
    sid = _seed(store)
    chat = session_chat(sid, store=store)
    assert chat["format"] == CHAT_FORMAT
    assert chat["session"] == sid
    assert chat["model"] == "demo-model"
    kinds = [turn["kind"] for turn in chat["turns"]]
    assert kinds == ["message", "tool_call", "message", "frame"]
    assert chat["turns"][0]["role"] == "user"
    assert chat["turns"][0]["content"] == "hello café"
    assert chat["turns"][0]["binary_refs"][0]["path"] == "/tmp/shot.png"
    assert chat["turns"][1]["tool"] == "search"
    assert chat["turns"][1]["result"] == "found 2"
    assert SECRET not in chat["turns"][2]["content"]
    assert "please" in chat["turns"][2]["content"]
    assert chat["turns"][3]["content"] == "compacted earlier"

    markdown = export_session(sid, store=store, fmt="md")
    assert markdown.startswith(f"# {sid}\n")
    assert "model: demo-model" in markdown
    assert "## user · " in markdown
    assert "hello café" in markdown
    assert "shot.png" in markdown
    assert "## tool · search · " in markdown
    assert "found 2" in markdown
    assert "## system · frame · " in markdown
    assert "compacted earlier" in markdown
    assert "other-session-marker" not in markdown
    assert SECRET not in markdown

    payload = json.loads(export_session(f"trace:{sid}", store=store, fmt="JSON"))
    assert payload["session"] == sid
    assert payload["turns"][0]["content"] == "hello café"

    messages_only = session_chat(sid, store=store, include_tools=False, include_frames=False)
    assert [turn["kind"] for turn in messages_only["turns"]] == ["message", "message"]


def test_export_includes_spilled_tail(tmp_path: Path):
    store = TraceStore(tmp_path)
    sid = "ses_long"
    body = ("n" * INLINE_CONTENT_CHARS) + "TAILMARKER"
    record_message(sid, "user", body, store=store)
    chat = session_chat(sid, store=store)
    assert chat["turns"][0]["content"].endswith("TAILMARKER")
    assert "TAILMARKER" in export_session(sid, store=store, fmt="markdown")


def test_export_errors(tmp_path: Path):
    store = TraceStore(tmp_path)
    with pytest.raises(ExportError) as missing:
        export_session("ses_missing", store=store)
    assert missing.value.code == "not_found"
    with pytest.raises(ExportError) as bad:
        export_session("ses_missing", store=store, fmt="xml")
    assert bad.value.code == "bad_format"
    record_message("ses_old", "user", "old memory", store=store)
    daily = store.list_trace_files()[0]
    daily.rename(daily.with_name("2020-01-01.jsonl"))
    with pytest.raises(ExportError):
        export_session("ses_old", store=store, days=1)
    assert "old memory" in export_session("ses_old", store=store)


def test_cli_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENTS_TRACES_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTS_NO_UPDATE_CHECK", "1")
    from agents_traces.__main__ import main

    store = TraceStore(tmp_path)
    record_message("ses_cli", "assistant", "cli-body", store=store)
    store.append(
        TraceEvent(
            session="ses_cli",
            type="tool_call",
            tool="run",
            args={"cmd": "pwd"},
            status="ok",
            result="ok",
        )
    )

    monkeypatch.setattr(
        "sys.argv",
        ["agents-traces", "export", "--session", "ses_cli", "--format", "json"],
    )
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert main() == 0
    payload = json.loads(stdout.getvalue())
    assert payload["session"] == "ses_cli"
    assert payload["turns"][0]["content"] == "cli-body"
    assert payload["turns"][1]["tool"] == "run"

    out = tmp_path / "out" / "chat.md"
    monkeypatch.setattr(
        "sys.argv",
        [
            "agents-traces",
            "export",
            "--format",
            "markdown",
            "--no-tools",
            "-o",
            str(out),
        ],
    )
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert main() == 0
    text = out.read_text(encoding="utf-8")
    assert "cli-body" in text
    assert "run" not in text
    assert "Wrote" in stdout.getvalue()

    monkeypatch.setattr("sys.argv", ["agents-traces", "export", "--session", "missing"])
    stderr = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
        assert main() == 1
    assert "missing" in stderr.getvalue()


def test_mcp_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agents_traces.mcp_server as mcp_mod

    store = TraceStore(tmp_path)
    monkeypatch.setattr(mcp_mod, "store", store)
    record_message("ses_mcp", "user", "mcp-hello", store=store)
    raw = mcp_mod.export_session(session_id="ses_mcp", format="json", include_tools=False)
    payload = json.loads(raw[raw.find("{") :])
    assert payload["turns"][0]["content"] == "mcp-hello"
    missing = mcp_mod.export_session(session_id="nope")
    err = json.loads(missing[missing.find("{") :])
    assert err["error"] == "not_found"
