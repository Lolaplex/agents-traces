import tempfile
from pathlib import Path

from agents_traces.assemble import assemble_messages, record_message
from agents_traces.frames import compact_session
from agents_traces.store import TraceStore


def _seed(store, n=10):
    for i in range(n):
        record_message("ses_t", "user" if i % 2 == 0 else "assistant", f"turn {i}", store=store)


def test_compact_replaces_covered_events_in_assemble():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(traces_dir=tmp)
        _seed(store)
        frame = compact_session("ses_t", store=store, keep_last=2)
        assert frame is not None
        rng = frame.metadata["summarized_from"]
        assert rng["count"] == 8

        msgs = assemble_messages("ses_t", store=store, limit=50)
        roles = [m["role"] for m in msgs]
        contents = [m["content"] or "" for m in msgs]
        assert "system" in roles
        assert any("[compacted 8 events" in c for c in contents)
        assert not any("turn 0" in c for c in contents)  # covered, not replayed
        assert not any("turn 7" in c for c in contents)  # boundary event covered too
        assert any("turn 9" in c for c in contents)      # tail survives
        assert frame.metadata["tokens_preceding"] > 0


def test_compact_is_idempotent_until_new_events():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(traces_dir=tmp)
        _seed(store)
        assert compact_session("ses_t", store=store, keep_last=2) is not None
        assert compact_session("ses_t", store=store, keep_last=2) is None

        record_message("ses_t", "user", "fresh turn", store=store)
        second = compact_session("ses_t", store=store, keep_last=2)
        # the boundary rolls forward: the new turn pushes turn 8 out of
        # keep_last, and only not-yet-covered events enter the new frame
        assert second is not None
        assert second.metadata["summarized_from"]["count"] == 1

        msgs = assemble_messages("ses_t", store=store, limit=50)
        contents = [m["content"] or "" for m in msgs]
        assert sum("[compacted" in c for c in contents) == 2
        assert any("fresh turn" in c for c in contents)


def test_compact_respects_custom_content():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(traces_dir=tmp)
        _seed(store)
        frame = compact_session("ses_t", store=store, keep_last=2, content="LLM wrote this")
        assert frame.metadata["content"] == "LLM wrote this"
        msgs = assemble_messages("ses_t", store=store, limit=50)
        assert any("LLM wrote this" in (m["content"] or "") for m in msgs)


def test_short_session_is_noop():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(traces_dir=tmp)
        record_message("ses_t", "user", "only one", store=store)
        assert compact_session("ses_t", store=store, keep_last=6) is None
