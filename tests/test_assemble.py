import tempfile

from agents_traces.assemble import assemble_messages, record_message, session_id_for
from agents_traces.store import TraceStore


def test_session_id_is_channel_plus_user():
    assert session_id_for("telegram", 5712508215) == "telegram-5712508215"


def test_assemble_rebuilds_chat_completions_payload():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(traces_dir=tmp)
        sid = session_id_for("telegram", "42")
        record_message(sid, "user", "ping", store=store, channel="telegram", user="42")
        record_message(
            sid,
            "assistant",
            "pong",
            store=store,
            channel="telegram",
            user="42",
        )
        record_message(
            sid,
            "user",
            "see this",
            store=store,
            channel="telegram",
            user="42",
            binary_refs=[{"path": "/data/inbox/shot.png", "media_type": "image/png"}],
        )
        msgs = assemble_messages(sid, store=store, limit=10)
        assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
        assert "ping" in msgs[0]["content"]
        assert msgs[0]["content"].startswith("<ts>")
        assert "shot.png" in msgs[2]["content"]
        assert "image/png" in msgs[2]["content"]


def test_assemble_projects_preview_not_full_body():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(traces_dir=tmp)
        sid = session_id_for("http", "fabian")
        blob = "keep-me " + ("n" * 2000)
        record_message(sid, "user", blob, store=store, channel="http", user="fabian")
        msgs = assemble_messages(sid, store=store, limit=10)
        shown = msgs[0]["content"]
        assert "keep-me" in shown
        assert "<ref " in shown
        assert "n" * 2000 not in shown
        from agents_traces.prompt import content_digest

        assert content_digest("keep-me " + ("n" * 2000)) in shown
        stored = store.list_trace_files(days=1)
        raw = stored[0].read_text(encoding="utf-8")
        assert "n" * 2000 in raw


def test_assemble_evicts_old_tool_stdio_keeps_user():
    from agents_traces.models import TraceEvent

    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(traces_dir=tmp)
        sid = session_id_for("http", "fabian")
        store.append(
            TraceEvent(
                session=sid,
                type="message",
                ts="2026-08-31T00:00:00Z",
                metadata={"role": "user", "content": "run it"},
            )
        )
        store.append(
            TraceEvent(
                session=sid,
                type="tool_call",
                tool="shell",
                result="early\n" + ("a" * 900),
                ts="2026-08-31T00:00:01Z",
            )
        )
        store.append(
            TraceEvent(
                session=sid,
                type="tool_call",
                tool="shell",
                result="late\n" + ("b" * 900),
                ts="2026-08-31T00:00:02Z",
            )
        )
        msgs = assemble_messages(sid, store=store, limit=20, include_tools=True)
        roles = [m["role"] for m in msgs]
        assert roles[0] == "user"
        assert "run it" in msgs[0]["content"]
        tool_bodies = [m["content"] for m in msgs if m["role"] == "tool"]
        assert len(tool_bodies) == 2
        assert tool_bodies[0].startswith("<stdio")
        assert "early" not in tool_bodies[0]
        assert "late" in tool_bodies[1]
