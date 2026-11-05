"""Retained bodies: artifacts, ranged reads, digest checks, old JSONL."""

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from agents_traces.assemble import assemble_messages, record_message
from agents_traces.content import (
    INLINE_CONTENT_CHARS,
    ContentNotFoundError,
    DigestMismatchError,
    blob_path,
    content_digest,
    content_sha256,
    read_content,
)
from agents_traces.models import TraceEvent
from agents_traces.session_view import session_grep
from agents_traces.store import TraceStore


def _big_body() -> str:
    # One non-ASCII code point on each end so the slice is not a byte slice.
    return "ü" + ("x" * INLINE_CONTENT_CHARS) + "é"


def test_over_20k_round_trip_and_ref_resolves():
    body = _big_body()
    assert len(body) > 20000
    with tempfile_store() as store:
        sid = "ses_over"
        event = record_message(sid, "user", body, store=store)
        assert event.metadata["truncated"] is True
        assert event.metadata["chars"] == len(body)
        assert event.metadata["digest"] == content_digest(body)
        assert event.metadata["sha256"] == content_sha256(body)
        assert event.metadata["content"].startswith("ü")
        assert not event.metadata["content"].endswith("é")
        assert len(event.metadata["content"]) == INLINE_CONTENT_CHARS

        raw = store.list_trace_files(days=1)[0].read_text(encoding="utf-8")
        assert "é" not in raw
        saved = json.loads(raw)
        assert content_sha256(saved["metadata"]["content"]) != saved["metadata"]["sha256"]

        full = read_content(sid, event.metadata["digest"], store=store)
        assert full.text == body
        assert full.truncated is False
        assert full.stored_truncated is True
        assert full.total_chars == len(body)
        assert full.digest == content_sha256(body)

        window = read_content(sid, full.digest, offset=0, limit=1, store=store)
        assert window.text == "ü"
        assert window.truncated is True
        assert window.total_chars == len(body)

        tail = read_content(sid, content_digest(body), offset=len(body) - 1, limit=4, store=store)
        assert tail.text == "é"
        assert tail.truncated is True

        empty = read_content(sid, full.digest, offset=0, limit=0, store=store)
        assert empty.text == ""
        assert empty.truncated is True

        past = read_content(sid, full.digest, offset=10**6, limit=5, store=store)
        assert past.text == ""
        assert past.truncated is True

        traced = read_content(f"trace:{sid}", full.digest_short, store=store)
        assert traced.session == sid
        assert traced.text == body

        msgs = assemble_messages(sid, store=store, limit=5)
        shown = msgs[0]["content"]
        assert content_digest(body) in shown
        assert f'chars="{len(body)}"' in shown
        assert 'truncated="1"' in shown
        assert "é" not in shown
        import re

        found = re.search(r'digest="([a-f0-9]+)"', shown)
        assert found is not None
        assert read_content(sid, found.group(1), store=store).text == body

        grep = session_grep("é", store=store)
        assert "Found 1" in grep
        assert content_digest(body) in grep


def test_ranged_tool_result_and_interceptor(monkeypatch, tmp_path):
    body = "HEAD" + ("u" * INLINE_CONTENT_CHARS) + "TAIL"
    with tempfile_store() as store:
        from agents_traces import SessionTracer

        sid = "ses_tool"
        SessionTracer(sid, store=store).record_tool("shell", result=body)
        piece = read_content(sid, content_sha256(body), offset=0, limit=4, store=store)
        assert piece.text == "HEAD"
        assert piece.stored_truncated is True
        assert piece.truncated is True
        whole = read_content(sid, content_digest(body), store=store)
        assert whole.text == body

        msgs = assemble_messages(sid, store=store, include_tools=True, limit=10)
        tool_text = [m["content"] for m in msgs if m.get("role") == "tool"]
        assert tool_text
        assert content_digest(body) in tool_text[-1]
        assert f'chars="{len(body)}"' in tool_text[-1]

    monkeypatch.setenv("AGENTS_TRACES_DIR", str(tmp_path))
    import agents_traces.interceptor as interceptor

    interceptor._global_store = None
    interceptor._process_session_id = None
    try:
        @interceptor.trace_call
        def echo_tool():
            return body

        assert echo_tool().endswith("TAIL")
        store = TraceStore(tmp_path)
        events = [e for e in store.iter_events() if e.type == "tool_call"]
        assert len(events) == 1
        assert "TAIL" not in (events[0].result or "")
        assert events[0].metadata["result_truncated"] is True
        assert events[0].metadata["result_chars"] == len(body)
        loaded = read_content(events[0].session, events[0].metadata["result_digest"], store=store)
        assert loaded.text == body
    finally:
        interceptor._global_store = None
        interceptor._process_session_id = None


def test_digest_mismatch_and_not_found():
    with tempfile_store() as store:
        body = _big_body()
        sid = "ses_bad"
        event = record_message(sid, "user", body, store=store)
        sha = event.metadata["sha256"]
        blob_path(store.traces_dir, sha).write_bytes(b"tampered")
        with pytest.raises(DigestMismatchError):
            read_content(sid, sha, store=store)

        lied = content_digest("other-text")
        path = store.get_date_file_path()
        path.write_text(
            path.read_text(encoding="utf-8")
            + json.dumps(
                {
                    "ts": "2026-11-05T00:00:00Z",
                    "session": "ses_lied",
                    "type": "message",
                    "metadata": {"role": "user", "content": "hello", "digest": lied},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with pytest.raises(DigestMismatchError):
            read_content("ses_lied", lied, store=store)
        honest = read_content("ses_lied", content_digest("hello"), store=store)
        assert honest.text == "hello"
        assert honest.stored_truncated is False

        with pytest.raises(ContentNotFoundError):
            read_content(sid, "ab" * 6, store=store)
        with pytest.raises(ValueError):
            read_content(sid, "not-hex", store=store)


def test_backward_compat_existing_trace_file():
    with tempfile_store() as store:
        legacy_body = "legacy body stays inline"
        historical = "H" * 20000
        path = store.get_date_file_path()
        lines = [
            {
                "ts": "2026-11-05T01:00:00Z",
                "session": "legacy",
                "type": "message",
                "metadata": {
                    "role": "user",
                    "content": legacy_body,
                    "digest": content_digest(legacy_body),
                },
            },
            {
                "ts": "2026-11-05T01:00:01Z",
                "session": "legacy",
                "type": "message",
                "metadata": {"role": "assistant", "content": "plain old line"},
            },
            {
                "ts": "2026-11-05T01:00:02Z",
                "session": "hist",
                "type": "message",
                "metadata": {
                    "role": "user",
                    "content": historical,
                    "digest": content_digest(historical),
                },
            },
            {
                "ts": "2026-11-05T01:00:03Z",
                "session": "hist",
                "type": "tool_call",
                "tool": "shell",
                "result": "tool says hello",
            },
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in lines), encoding="utf-8")
        assert not (store.traces_dir / "content").exists()

        msgs = assemble_messages("legacy", store=store, limit=10)
        joined = "\n".join(m["content"] for m in msgs)
        assert "legacy body stays inline" in joined
        assert "plain old line" in joined

        loaded = read_content("legacy", content_digest(legacy_body), store=store)
        assert loaded.text == legacy_body
        assert loaded.stored_truncated is False

        plain = read_content("legacy", content_digest("plain old line"), store=store)
        assert plain.text == "plain old line"

        old_cut = read_content("hist", content_digest(historical), store=store)
        assert old_cut.text == historical
        assert old_cut.stored_truncated is False
        assert old_cut.total_chars == 20000

        tool = read_content("hist", content_digest("tool says hello"), store=store)
        assert tool.text == "tool says hello"


def test_inline_boundary_and_cleanup_prunes_blobs():
    with tempfile_store() as store:
        exact = "e" * INLINE_CONTENT_CHARS
        record_message("fit", "user", exact, store=store)
        assert not (store.traces_dir / "content").exists()
        fit = read_content("fit", content_digest(exact), store=store)
        assert fit.text == exact
        assert fit.stored_truncated is False

        old_body = "Q" + ("w" * INLINE_CONTENT_CHARS) + "E"
        new_body = "A" + ("b" * INLINE_CONTENT_CHARS) + "Z"
        store.append(
            TraceEvent(
                ts="2020-01-01T00:00:00Z",
                session="old",
                type="message",
                metadata={"role": "user", "content": old_body},
            )
        )
        record_message("new", "user", new_body, store=store)
        old_sha = content_sha256(old_body)
        new_sha = content_sha256(new_body)
        assert blob_path(store.traces_dir, old_sha).is_file()
        assert blob_path(store.traces_dir, new_sha).is_file()

        removed = store.cleanup_old_traces(keep_days=30, compress=False)
        assert removed == 1
        assert not blob_path(store.traces_dir, old_sha).exists()
        assert blob_path(store.traces_dir, new_sha).is_file()
        with pytest.raises(ContentNotFoundError):
            read_content("old", old_sha, store=store)
        assert read_content("new", new_sha, store=store).text == new_body


def test_session_grep_date_window():
    with tempfile_store() as store:
        store.append(
            TraceEvent(
                ts="1999-01-01T08:00:00Z",
                session="span",
                type="message",
                metadata={"role": "user", "content": "alpha day"},
            )
        )
        store.append(
            TraceEvent(
                ts="1999-01-03T18:00:00Z",
                session="span",
                type="message",
                metadata={"role": "user", "content": "gamma day"},
            )
        )
        assert "No session lines" in session_grep("day", store=store)
        only = session_grep("day", since="1999-01-01", until="1999-01-01", store=store)
        assert "alpha day" in only
        assert "gamma day" not in only
        later = session_grep(
            "day",
            since="1999-01-03T12:00:00Z",
            until="1999-01-03T23:00:00Z",
            store=store,
        )
        assert "gamma day" in later
        assert "alpha day" not in later


def test_cli_read_json(monkeypatch):
    with tempfile_store() as store:
        monkeypatch.setenv("AGENTS_TRACES_DIR", str(store.traces_dir))
        body = _big_body()
        sid = "ses_cli"
        record_message(sid, "user", body, store=store)
        from agents_traces.__main__ import main

        monkeypatch.setattr(
            "sys.argv",
            [
                "agents-traces",
                "read",
                "--session",
                sid,
                "--digest",
                content_digest(body),
                "--offset",
                "0",
                "--limit",
                "1",
                "--json",
            ],
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main()
        assert code == 0
        payload = json.loads(buf.getvalue())
        assert payload["text"] == "ü"
        assert payload["total_chars"] == len(body)
        assert payload["truncated"] is True
        assert payload["stored_truncated"] is True

        blob_path(store.traces_dir, content_sha256(body)).write_bytes(b"nope")
        monkeypatch.setattr(
            "sys.argv",
            ["agents-traces", "read", "--session", sid, "--digest", content_sha256(body), "--json"],
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main()
        assert code == 1
        err = json.loads(buf.getvalue())
        assert err["error"] == "digest_mismatch"


def test_mcp_read_content(monkeypatch):
    with tempfile_store() as store:
        import agents_traces.mcp_server as mcp_mod

        monkeypatch.setattr(mcp_mod, "store", store)
        body = _big_body()
        sid = "ses_mcp"
        record_message(sid, "user", body, store=store)
        raw = mcp_mod.read_content(sid, content_digest(body), offset=len(body) - 1, limit=1)
        payload = json.loads(raw)
        assert payload["text"] == "é"
        assert payload["truncated"] is True
        missing = json.loads(mcp_mod.read_content(sid, "cd" * 6))
        assert missing["error"] == "not_found"


class tempfile_store:
    def __enter__(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.store = TraceStore(traces_dir=self._tmp.name)
        return self.store

    def __exit__(self, exc_type, exc, tb):
        self._tmp.cleanup()
        return False
