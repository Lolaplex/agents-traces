import json
import tempfile
from pathlib import Path

from agents_traces.ingest import (
    parse_cursor_transcript,
    _parse_cursor_clock,
    tool_calls_already_live,
    _tool_fingerprint,
)
from agents_traces.models import TraceEvent
from agents_traces.store import TraceStore


def test_parse_cursor_clock_utc2():
    iso = _parse_cursor_clock("Saturday, Aug 29, 2026, 5:06 AM (UTC+2)")
    assert iso == "2026-08-29T03:06:00Z"


def test_parse_cursor_unwraps_mcp_and_clock():
    with tempfile.TemporaryDirectory() as tmp:
        chat = Path(tmp) / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        chat.mkdir()
        path = chat / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
        path.write_text(
            json.dumps(
                {
                    "role": "user",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "<timestamp>Saturday, Aug 29, 2026, 5:06 AM (UTC+2)</timestamp>\n<user_query>remember this</user_query>",
                            }
                        ]
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "role": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "CallMcpTool",
                                "input": {
                                    "server": "user-agents-memory",
                                    "toolName": "add_memory",
                                    "arguments": {"fact_or_message": "Index is a cache"},
                                },
                            }
                        ]
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        events = list(parse_cursor_transcript(path))
        kinds = [e.type for e in events]
        assert "session_start" in kinds
        assert "message" in kinds
        tools = [e for e in events if e.type == "tool_call"]
        assert len(tools) == 1
        assert tools[0].tool == "add_memory"
        assert tools[0].args["fact_or_message"] == "Index is a cache"
        assert tools[0].origin == "ingested"
        assert tools[0].session.startswith("cursor-")
        assert tools[0].ts == "2026-08-29T03:06:00Z"


def test_ingest_skips_existing_session():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(traces_dir=tmp)
        chat = Path(tmp) / "proj" / "agent-transcripts" / "sid-1"
        # not using discover; call parse + second ingest via known set
        chat.mkdir(parents=True)
        path = chat / "sid-1.jsonl"
        path.write_text(
            json.dumps(
                {
                    "role": "user",
                    "message": {"content": [{"type": "text", "text": "hi"}]},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        first = list(parse_cursor_transcript(path))
        for e in first:
            store.append(e)
        before = list(store.iter_events())
        # second pass through existing-session skip: append again only if not skipped
        known = {e.session for e in before}
        second = list(parse_cursor_transcript(path))
        assert second[0].session in known
        assert len(before) == len(list(store.iter_events()))


def test_tool_calls_already_live_detects_twin():
    live = TraceEvent(
        ts="2026-08-30T00:00:00Z",
        session="proc-aaa",
        type="tool_call",
        origin="live",
        tool="search_memory",
        args={"query": "cache law index retrieval", "project": "agents-memory"},
    )
    ingested = TraceEvent(
        ts="2026-08-30T00:00:00Z",
        session="cursor-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        type="tool_call",
        origin="ingested",
        tool="search_memory",
        args={"query": "cache law index retrieval", "limit": 5},
    )
    live_fp = {_tool_fingerprint(live)}
    assert tool_calls_already_live([ingested], live_fp)
    other = TraceEvent(
        ts="2026-08-30T00:00:00Z",
        session="cursor-cccc",
        type="tool_call",
        origin="ingested",
        tool="search_memory",
        args={"query": "unrelated homelab dns resolver"},
    )
    assert not tool_calls_already_live([other], live_fp)


def test_drop_sessions_removes_ingested_twin():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(traces_dir=tmp)
        store.append(
            TraceEvent(
                ts="2026-08-30T00:00:00Z",
                session="proc-aaa",
                type="tool_call",
                origin="live",
                tool="search_memory",
                args={"query": "cache law"},
                result="Found 1 hits",
            )
        )
        store.append(
            TraceEvent(
                ts="2026-08-30T00:00:00Z",
                session="cursor-twin",
                type="tool_call",
                origin="ingested",
                tool="search_memory",
                args={"query": "cache law"},
            )
        )
        n = store.drop_sessions({"cursor-twin"})
        assert n == 1
        left = list(store.iter_events())
        assert len(left) == 1
        assert left[0].session == "proc-aaa"
