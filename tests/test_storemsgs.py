"""Synced store.db msg:* readers + assemble merge + identity resume."""
import json
from pathlib import Path

import pytest

from agents_traces.assemble import assemble_messages, record_message
from agents_traces.store import TraceStore

agents_store = pytest.importorskip("agents_store")
from agents_store.db import connect  # noqa: E402
from agents_store.ingest import ingest  # noqa: E402

ATTRS = {
    "msg:session": ("text", "one"),
    "msg:role": ("text", "one"),
    "msg:text": ("text", "one"),
    "msg:ts": ("instant", "one"),
    "msg:host": ("text", "one"),
    "msg:persona": ("text", "one"),
    "msg:user": ("text", "one"),
    "msg:frame_id": ("text", "one"),
    "msg:deleted": ("bool", "one"),
    "session:title": ("text", "one"),
    "session:last_active": ("instant", "one"),
    "frame:session": ("text", "one"),
    "frame:kind": ("text", "one"),
    "frame:first_ts": ("instant", "one"),
    "frame:last_ts": ("instant", "one"),
    "frame:ts": ("instant", "one"),
    "frame:host": ("text", "one"),
    "frame:retired": ("bool", "one"),
}


def _domain(conn):
    for name, (vt, card) in ATTRS.items():
        conn.execute(
            "INSERT OR IGNORE INTO attr (name, value_type, cardinality, doc)"
            " VALUES (?, ?, ?, '')", (name, vt, card))


def _hlc(ts):
    from datetime import datetime

    ms = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    return f"{ms:013d}.00000.proj"


def _msg(conn, sid, ts, role, text, *, seq, user=""):
    e = f"msg:{sid}:{role}:{ts}"
    datoms = [
        (e, "msg:session", sid, True),
        (e, "msg:role", role, True),
        (e, "msg:text", text, True),
        (e, "msg:ts", ts, True),
        (e, "msg:host", "hostA", True),
    ]
    if user:
        datoms.append((e, "msg:user", user, True))
    ingest(conn, "proj@hostA:sid", seq, _hlc(ts), datoms)
    return e


@pytest.fixture
def store_db(tmp_path):
    path = tmp_path / "store.db"
    conn = connect(str(path))
    _domain(conn)
    yield path, conn
    conn.close()


def test_no_store_db_returns_empty(tmp_path, monkeypatch):
    from agents_traces import storemsgs

    monkeypatch.setenv("AGENTS_STORE_DB", str(tmp_path / "none.db"))
    assert storemsgs.session_messages("s1") == []
    assert storemsgs.find_latest_session(user="u") is None


def test_live_msgs_read(store_db, monkeypatch):
    path, conn = store_db
    _msg(conn, "s1", "2026-11-22T10:00:00Z", "user", "hi there",
         seq=1, user="telegram:42")
    _msg(conn, "s1", "2026-11-22T10:00:05Z", "assistant", "hello", seq=2)
    _msg(conn, "s2", "2026-11-22T10:00:01Z", "user", "other session", seq=3)
    monkeypatch.setenv("AGENTS_STORE_DB", str(path))
    from agents_traces import storemsgs

    msgs = storemsgs.session_messages("s1")
    assert [m["text"] for m in msgs] == ["hi there", "hello"]
    assert msgs[0]["role"] == "user"
    assert msgs[0]["ts"] == "2026-11-22T10:00:00Z"
    assert msgs[0]["user"] == "telegram:42"


def test_deleted_and_retired_hidden(store_db, monkeypatch):
    path, conn = store_db
    _msg(conn, "s1", "2026-11-22T10:00:00Z", "user", "turn one", seq=1)
    gone = _msg(conn, "s1", "2026-11-22T10:01:00Z", "user", "turn two", seq=2)
    fe = "frame:s1:f1"
    ingest(conn, "proj@hostA:sid", 3, _hlc("2026-11-22T10:01:05Z"), [
        (fe, "frame:session", "s1", True),
        (fe, "frame:kind", "undo", True),
        (fe, "frame:ts", "2026-11-22T10:01:05Z", True),
        (gone, "msg:deleted", "true", True),
        (gone, "msg:frame_id", fe, True),
    ])
    monkeypatch.setenv("AGENTS_STORE_DB", str(path))
    from agents_traces import storemsgs

    assert [m["text"] for m in storemsgs.session_messages("s1")] == ["turn one"]


def test_assemble_merges_store_only_turns(store_db, tmp_path, monkeypatch):
    path, conn = store_db
    _msg(conn, "s1", "2026-11-22T10:00:00Z", "user", "remote turn one",
         seq=1, user="telegram:42")
    _msg(conn, "s1", "2026-11-22T10:00:05Z", "assistant", "remote answer", seq=2)
    # local trace holds only a later turn; the earlier synced turns merge in
    traces = TraceStore(traces_dir=tmp_path / "traces")
    record_message("s1", "user", "local turn two", store=traces,
                   channel="telegram", user="42")
    monkeypatch.setenv("AGENTS_STORE_DB", str(path))
    msgs = assemble_messages("s1", store=traces)
    texts = [m["content"] for m in msgs]
    assert any("remote turn one" in t for t in texts)
    assert any("remote answer" in t for t in texts)
    assert any("local turn two" in t for t in texts)
    # merged ordering respects ts: remote turn one precedes local turn two
    i_remote = next(i for i, t in enumerate(texts) if "remote turn one" in t)
    i_local = next(i for i, t in enumerate(texts) if "local turn two" in t)
    assert i_remote < i_local


def test_assemble_empty_local_reads_store(store_db, tmp_path, monkeypatch):
    path, conn = store_db
    _msg(conn, "s1", "2026-11-22T10:00:00Z", "user", "synced only turn", seq=1)
    traces = TraceStore(traces_dir=tmp_path / "traces")
    monkeypatch.setenv("AGENTS_STORE_DB", str(path))
    msgs = assemble_messages("s1", store=traces)
    assert [m["content"] for m in msgs] == [
        "<ts>2026-11-22T10:00:00Z</ts>\nsynced only turn"]


def test_assemble_no_dup_when_both_sides_have_msg(store_db, tmp_path,
                                                monkeypatch):
    path, conn = store_db
    _msg(conn, "s1", "2026-11-22T10:00:00Z", "user", "same body", seq=1)
    traces = TraceStore(traces_dir=tmp_path / "traces")
    record_message("s1", "user", "same body", store=traces)
    monkeypatch.setenv("AGENTS_STORE_DB", str(path))
    msgs = assemble_messages("s1", store=traces)
    assert sum(1 for m in msgs if "same body" in m["content"]) == 1


def test_find_latest_session_matches_alias(store_db, monkeypatch):
    path, conn = store_db
    _msg(conn, "s_old", "2026-11-20T10:00:00Z", "user", "old", seq=1,
         user="telegram:42")
    _msg(conn, "s_new", "2026-11-22T10:00:00Z", "user", "new", seq=2,
         user="telegram:42")
    _msg(conn, "s_other", "2026-11-23T10:00:00Z", "user", "someone else",
         seq=3, user="telegram:99")
    monkeypatch.setenv("AGENTS_STORE_DB", str(path))
    from agents_traces import storemsgs

    assert storemsgs.find_latest_session(
        user="42", alias="telegram:42") == "s_new"


def test_identity_resumes_via_store(store_db, tmp_path, monkeypatch):
    """Host B: empty traces, empty users.json -> still resumes the synced
    session for the same channel+user tuple."""
    path, conn = store_db
    _msg(conn, "ses_synced", "2026-11-22T10:00:00Z", "user", "hi", seq=1,
         user="telegram:42")
    monkeypatch.setenv("AGENTS_STORE_DB", str(path))
    from agents_traces.identity import IdentityStore

    store = IdentityStore(tmp_path / "users.json")
    resolved = store.resolve(channel="telegram", user="42",
                             trace_lookup=lambda **kw: None,
                             max_idle_hours=0.0)
    assert resolved.session.id == "ses_synced"


def test_title_from_oldest_trace_day(tmp_path):
    """Multi-day title: first user message of the OLDEST day file wins."""
    from agents_traces.store import TraceStore

    traces = TraceStore(traces_dir=tmp_path / "traces")
    day1 = tmp_path / "traces" / "2026-11-20.jsonl"
    day2 = tmp_path / "traces" / "2026-11-22.jsonl"
    for path, line in (
        (day1, {"ts": "2026-11-20T09:00:00Z", "session": "s1",
                "type": "message",
                "metadata": {"role": "user", "content": "first day prompt"}}),
        (day2, {"ts": "2026-11-22T09:00:00Z", "session": "s1",
                "type": "message",
                "metadata": {"role": "user", "content": "second day prompt"}}),
    ):
        path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    files = traces.list_trace_files()
    # newest-first contract (days window, resume scans) unchanged
    assert files[0].name == "2026-11-22.jsonl"
    oldest_first = list(reversed(files))
    first_user = next(
        e for e in traces.iter_events(files=oldest_first)
        if e.type == "message"
        and str((e.metadata or {}).get("role") or "") == "user")
    assert first_user.metadata["content"] == "first day prompt"
