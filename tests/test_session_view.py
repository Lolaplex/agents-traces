import tempfile

from agents_traces.models import TraceEvent
from agents_traces.session_view import session_grep, session_snap, session_tail
from agents_traces.store import TraceStore


def test_session_view_reads_messages_not_product_jsonl():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = TraceStore(traces_dir=tmpdir)
        store.append(
            TraceEvent(
                session="cursor-abc",
                type="message",
                origin="ingested",
                metadata={"source": "cursor", "role": "user", "content": "pin vand export"},
            )
        )
        store.append(
            TraceEvent(
                session="cursor-abc",
                type="message",
                origin="ingested",
                metadata={
                    "source": "cursor",
                    "role": "assistant",
                    "content": "running pin --export",
                },
            )
        )
        store.append(
            TraceEvent(
                session="cursor-abc",
                type="tool_call",
                origin="ingested",
                tool="search_memory",
                args={"query": "vand"},
            )
        )

        snap = session_snap(limit=10, store=store)
        assert "pin vand export" in snap
        assert "running pin --export" not in snap

        grep = session_grep("pin --export", store=store)
        assert "running pin --export" in grep

        tail = session_tail(session_id="cursor-abc", limit=5, store=store)
        assert "Session tail" in tail
        assert "pin vand export" in tail


def test_session_view_empty_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = TraceStore(traces_dir=tmpdir)
        assert "traces" in session_snap(store=store).lower()
        assert "No session lines matching" in session_grep("xyz", store=store)
        assert "Session" in session_tail(store=store)
