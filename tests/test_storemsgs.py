"""Synced store.db msg:* readers + assemble merge + identity resume."""
import json
import os
from pathlib import Path

import pytest

from agents_traces.assemble import assemble_messages
from agents_traces.models import TraceEvent
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


def _msg(conn, sid, ts, role, text, *, seq, user="", host="hostA"):
    e = f"msg:{sid}:{role}:{ts}"
    datoms = [
        (e, "msg:session", sid, True),
        (e, "msg:role", role, True),
        (e, "msg:text", text, True),
        (e, "msg:ts", ts, True),
        (e, "msg:host", host, True),
    ]
    if user:
        datoms.append((e, "msg:user", user, True))
    ingest(conn, f"proj@{host}:sid", seq, _hlc(ts), datoms)
    return e


def _local_msg(traces, sid, ts, role, text):
    """Append a trace message with a fixed ts (record_message stamps now)."""
    traces.append(TraceEvent(session=sid, type="message", ts=ts,
                             metadata={"role": role, "content": text}))


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
    _local_msg(traces, "s1", "2026-11-22T11:00:00Z", "user",
               "local turn two")
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
    """Same turn held locally AND projected: one turn is one (ts, role) —
    the projection carries the trace event's ts verbatim."""
    path, conn = store_db
    ts = "2026-11-22T10:00:00Z"
    _msg(conn, "s1", ts, "user", "same body", seq=1)
    traces = TraceStore(traces_dir=tmp_path / "traces")
    _local_msg(traces, "s1", ts, "user", "same body")
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


def test_session_events_order_across_days(tmp_path):
    """Multi-day session: ``get_events_for_session`` (production code,
    not an in-test ``reversed()``) replays the oldest day first — the
    ordering a title/first-prompt read depends on."""
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
    events = [
        e for e in traces.get_events_for_session("s1")
        if e.type == "message"
        and str((e.metadata or {}).get("role") or "") == "user"
    ]
    assert [e.metadata["content"] for e in events] == [
        "first day prompt", "second day prompt"]
    # and the assemble projection leads with the same oldest-day turn
    msgs = assemble_messages("s1", store=traces)
    assert "first day prompt" in msgs[0]["content"]


def test_assemble_undo_not_resurrected(store_db, tmp_path, monkeypatch):
    """Oracle: Host A holds 4 local msgs; Host B /undo leaves 2 live
    (msg:deleted='true' in store.db) -> assemble returns the 2 live
    turns. The old local_n>=len(remote) bail resurrected the rest."""
    path, conn = store_db
    traces = TraceStore(traces_dir=tmp_path / "traces")
    turns = [
        ("user", "turn one"),
        ("assistant", "answer one"),
        ("user", "turn two"),
        ("assistant", "answer two"),
    ]
    for i, (role, text) in enumerate(turns):
        ts = f"2026-11-22T10:0{i}:00Z"
        _msg(conn, "s1", ts, role, text, seq=i + 1)
        _local_msg(traces, "s1", ts, role, text)
    # Host B's /undo tombstones the last two turns (kind=undo frame +
    # msg:deleted + msg:frame_id, the msgsync project_frame shape).
    fe = "frame:s1:undo1"
    ingest(conn, "proj@hostB:sid", 1, _hlc("2026-11-22T10:04:00Z"), [
        (fe, "frame:session", "s1", True),
        (fe, "frame:kind", "undo", True),
        (fe, "frame:first_ts", "2026-11-22T10:02:00Z", True),
        (fe, "frame:last_ts", "2026-11-22T10:03:00Z", True),
        (fe, "frame:ts", "2026-11-22T10:04:00Z", True),
        ("msg:s1:user:2026-11-22T10:02:00Z", "msg:deleted", "true", True),
        ("msg:s1:user:2026-11-22T10:02:00Z", "msg:frame_id", fe, True),
        ("msg:s1:assistant:2026-11-22T10:03:00Z", "msg:deleted", "true", True),
        ("msg:s1:assistant:2026-11-22T10:03:00Z", "msg:frame_id", fe, True),
    ])
    monkeypatch.setenv("AGENTS_STORE_DB", str(path))
    msgs = assemble_messages("s1", store=traces)
    texts = [m["content"] for m in msgs]
    assert any("turn one" in t for t in texts)
    assert any("answer one" in t for t in texts)
    assert not any("turn two" in t for t in texts)
    assert not any("answer two" in t for t in texts)
    assert len(msgs) == 2


def test_assemble_merges_peer_turns_despite_count(store_db, tmp_path,
                                                  monkeypatch):
    """Oracle: local_n >= len(remote) must never short-circuit — a peer's
    new turns merge even when remote live count shrank (their /undo)."""
    path, conn = store_db
    traces = TraceStore(traces_dir=tmp_path / "traces")
    turns = [
        ("user", "turn one"),
        ("assistant", "answer one"),
        ("user", "turn two"),
        ("assistant", "answer two"),
    ]
    for i, (role, text) in enumerate(turns):
        ts = f"2026-11-22T10:0{i}:00Z"
        _msg(conn, "s1", ts, role, text, seq=i + 1)
        _local_msg(traces, "s1", ts, role, text)
    # B /undo-s the last two AND adds a new turn: 3 live < 4 local.
    fe = "frame:s1:undo1"
    ingest(conn, "proj@hostB:sid", 1, _hlc("2026-11-22T10:04:00Z"), [
        (fe, "frame:session", "s1", True),
        (fe, "frame:kind", "undo", True),
        (fe, "frame:first_ts", "2026-11-22T10:02:00Z", True),
        (fe, "frame:last_ts", "2026-11-22T10:03:00Z", True),
        (fe, "frame:ts", "2026-11-22T10:04:00Z", True),
        ("msg:s1:user:2026-11-22T10:02:00Z", "msg:deleted", "true", True),
        ("msg:s1:user:2026-11-22T10:02:00Z", "msg:frame_id", fe, True),
        ("msg:s1:assistant:2026-11-22T10:03:00Z", "msg:deleted", "true", True),
        ("msg:s1:assistant:2026-11-22T10:03:00Z", "msg:frame_id", fe, True),
    ])
    _msg(conn, "s1", "2026-11-22T10:05:00Z", "user", "peer new turn",
         seq=5, host="hostB")
    monkeypatch.setenv("AGENTS_STORE_DB", str(path))
    msgs = assemble_messages("s1", store=traces)
    texts = [m["content"] for m in msgs]
    assert any("turn one" in t for t in texts)
    assert any("answer one" in t for t in texts)
    assert any("peer new turn" in t for t in texts)
    assert not any("turn two" in t for t in texts)
    assert not any("answer two" in t for t in texts)
    assert len(msgs) == 3


def test_assemble_repeated_ok_and_redacted_paths(store_db, tmp_path,
                                                 monkeypatch):
    """Oracle: two "ok" turns at distinct ts both survive the merge;
    a body stored ~-redacted (klanker strips host roots before
    projecting) does NOT duplicate its unredacted local twin."""
    path, conn = store_db
    traces = TraceStore(traces_dir=tmp_path / "traces")
    _local_msg(traces, "s1", "2026-11-22T10:00:00Z", "user", "ok")
    _msg(conn, "s1", "2026-11-22T10:00:00Z", "user", "ok", seq=1)
    # repeated identical body, new turn -> must merge in
    _msg(conn, "s1", "2026-11-22T10:05:00Z", "user", "ok", seq=2)
    # klanker redacted $HOME -> ~ in the projected text (minor ts skew
    # from the peer clock): same turn, never a second copy. The stored
    # body keeps the source text's separators past the root.
    _local_msg(traces, "s1", "2026-11-22T10:10:00Z", "user",
               f"editing {Path.home()}{os.sep}repo{os.sep}app.py")
    _msg(conn, "s1", "2026-11-22T10:10:02Z", "user",
         f"editing ~{os.sep}repo{os.sep}app.py", seq=3)
    monkeypatch.setenv("AGENTS_STORE_DB", str(path))
    msgs = assemble_messages("s1", store=traces)
    bodies = [m["content"].split("\n")[-1] for m in msgs]
    assert bodies.count("ok") == 2
    assert sum(1 for b in bodies if "repo" in b and "app.py" in b) == 1


def test_explicit_traces_dir_reads_sibling_store_db(store_db, tmp_path,
                                                    monkeypatch):
    """Oracle: assemble_messages(sid, store=TraceStore(custom/"traces"))
    reads custom/"store.db" even when Workspace.from_env() points at a
    different workspace entirely (no cross-workspace leak)."""
    path, conn = store_db
    _msg(conn, "s1", "2026-11-22T10:00:00Z", "user", "custom ws turn",
         seq=1)
    monkeypatch.delenv("AGENTS_STORE_DB", raising=False)
    # env workspace resolves to a different host+workspace than tmp_path
    env_host = tmp_path / "envhost"
    env_host.mkdir()
    monkeypatch.setenv("AGENTS_HOST_DIR", str(env_host))
    monkeypatch.setenv("AGENTS_WORKSPACE", "elsewhere")
    from agents_workspace import Host, Workspace

    Host.from_env().create("elsewhere")
    assert Workspace.from_env().dir != tmp_path
    traces = TraceStore(traces_dir=tmp_path / "traces")
    msgs = assemble_messages("s1", store=traces)
    assert [m["content"] for m in msgs] == [
        "<ts>2026-11-22T10:00:00Z</ts>\ncustom ws turn"]


def test_find_latest_session_skips_retired_frame_msgs(store_db,
                                                      monkeypatch):
    """A msg whose msg:frame_id names a frame:retired entity is
    superseded (retired compaction summary) — it must not make its
    session the resumable one."""
    path, conn = store_db
    _msg(conn, "s_old", "2026-11-20T10:00:00Z", "user", "old turn",
         seq=1)
    se = _msg(conn, "s_new", "2026-11-22T10:00:00Z", "system",
              "stale compaction summary", seq=2)
    fe = "frame:s_new:f9"
    ingest(conn, "proj@hostA:sid", 3, _hlc("2026-11-22T11:00:00Z"), [
        (fe, "frame:session", "s_new", True),
        (fe, "frame:kind", "compaction", True),
        (fe, "frame:ts", "2026-11-22T10:30:00Z", True),
        (fe, "frame:retired", "true", True),
        (se, "msg:frame_id", fe, True),
    ])
    monkeypatch.setenv("AGENTS_STORE_DB", str(path))
    from agents_traces import storemsgs

    assert storemsgs.find_latest_session() == "s_old"
