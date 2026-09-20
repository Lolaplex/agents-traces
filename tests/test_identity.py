import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from agents_traces.assemble import record_message, session_id_for
from agents_traces.identity import IdentityStore, alias_id, legacy_session_id
from agents_traces.store import TraceStore


def test_alias_is_not_session():
    assert alias_id("telegram", 42) == "telegram:42"
    assert alias_id("http", "fabian") == "http:fabian"
    assert session_id_for("telegram", 42) == "telegram-42"
    assert legacy_session_id("telegram", 42) == "telegram-42"


def test_same_user_across_devices_binds_aliases():
    with tempfile.TemporaryDirectory() as tmp:
        store = IdentityStore(Path(tmp) / "identity.json")
        first = store.resolve(channel="telegram", user="5712")
        second = store.resolve(channel="http", user="laptop", user_id=first.user.id)
        assert second.user.id == first.user.id
        assert "telegram:5712" in second.user.aliases
        assert "http:laptop" in second.user.aliases


def test_explicit_session_resumes_from_other_alias():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        traces = root / "traces"
        traces.mkdir()
        tstore = TraceStore(traces_dir=traces)
        store = IdentityStore(root / "identity.json")
        first = store.resolve(channel="telegram", user="1")
        record_message(
            first.session.id,
            "user",
            "hi",
            store=tstore,
            channel="telegram",
            user="1",
            user_id=first.user.id,
        )
        with patch.dict(os.environ, {"AGENTS_TRACES_DIR": str(traces)}):
            other = store.resolve(channel="http", user="phone", session=first.session.id)
        assert other.session.id == first.session.id
        assert other.user.id == first.user.id


def test_new_session_is_a_new_thread():
    with tempfile.TemporaryDirectory() as tmp:
        store = IdentityStore(Path(tmp) / "identity.json")
        a = store.resolve(channel="http", user="fabian")
        b = store.resolve(channel="http", user="fabian", new_session=True)
        assert a.user.id == b.user.id
        assert a.session.id != b.session.id
        assert b.session.start_date
        assert b.session.id.startswith("ses_")


def test_legacy_trace_adopted_when_no_active_session():
    with tempfile.TemporaryDirectory() as tmp:
        store = IdentityStore(Path(tmp) / "identity.json")
        resolved = store.resolve(
            channel="telegram",
            user="42",
            legacy_exists=lambda sid: sid == "telegram-42",
        )
        assert resolved.session.id == "telegram-42"
        assert resolved.legacy is True


def test_resume_session_from_traces_by_channel_user_project():
    with tempfile.TemporaryDirectory() as tmp:
        traces = Path(tmp) / "traces"
        traces.mkdir()
        store = TraceStore(traces_dir=traces)
        record_message(
            "ses_alpha",
            "user",
            "hello",
            store=store,
            channel="repl",
            user="fabian",
            project="isa-physics",
        )
        ident = IdentityStore(Path(tmp) / "identity.json")
        resolved = ident.resolve(
            channel="repl",
            user="fabian",
            project="isa-physics",
            trace_lookup=store.find_latest_session,
        )
        assert resolved.session.id == "ses_alpha"
