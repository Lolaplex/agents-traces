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


def test_stale_traced_session_rolls_over_to_fresh():
    # A traced session idle >max_idle_hours across a day boundary is not resumed.
    from datetime import datetime, timezone, timedelta
    from agents_traces.models import TraceEvent

    with tempfile.TemporaryDirectory() as tmp:
        traces = Path(tmp) / "traces"
        traces.mkdir()
        store = TraceStore(traces_dir=traces)
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat().replace("+00:00", "Z")
        store.append(
            TraceEvent(
                session="ses_old",
                type="message",
                ts=old,
                metadata={"role": "user", "content": "hi", "channel": "repl", "user": "fabian"},
            )
        )
        ident = IdentityStore(Path(tmp) / "identity.json")
        resolved = ident.resolve(
            channel="repl",
            user="fabian",
            trace_lookup=store.find_latest_session,
        )
        assert resolved.session.id != "ses_old"
        assert resolved.session.id.startswith("ses_")


def test_recent_traced_session_across_midnight_does_not_rollover():
    from datetime import datetime, timezone, timedelta
    from agents_traces.models import TraceEvent

    with tempfile.TemporaryDirectory() as tmp:
        traces = Path(tmp) / "traces"
        traces.mkdir()
        store = TraceStore(traces_dir=traces)
        recent = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
        store.append(
            TraceEvent(
                session="ses_recent",
                type="message",
                ts=recent,
                metadata={"role": "user", "content": "hi", "channel": "repl", "user": "fabian"},
            )
        )
        ident = IdentityStore(Path(tmp) / "identity.json")
        resolved = ident.resolve(
            channel="repl",
            user="fabian",
            trace_lookup=store.find_latest_session,
        )
        assert resolved.session.id == "ses_recent"


def test_stale_rollover_records_trace_link_and_assemble_continues():
    # End-to-end: stale traced session → fresh mint with prev_session +
    # rollover event → assemble pulls the previous session's messages.
    import os
    from datetime import datetime, timezone, timedelta
    from unittest.mock import patch
    from agents_traces.assemble import assemble_messages
    from agents_traces.models import TraceEvent

    with tempfile.TemporaryDirectory() as tmp:
        live = Path(tmp) / "live"
        live.mkdir()
        store = TraceStore(traces_dir=live)
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat().replace("+00:00", "Z")
        store.append(
            TraceEvent(
                session="ses_old",
                type="message",
                ts=old,
                metadata={
                    "role": "user",
                    "content": "earlier context",
                    "channel": "repl",
                    "user": "fabian",
                },
            )
        )
        ident = IdentityStore(Path(tmp) / "identity.json")
        with patch.dict(os.environ, {"AGENTS_TRACES_DIR": str(live)}):
            resolved = ident.resolve(
                channel="repl",
                user="fabian",
                trace_lookup=store.find_latest_session,
            )
            assert resolved.session.id != "ses_old"
            assert resolved.session.prev_session == "ses_old"

            msgs = assemble_messages(resolved.session.id, limit=24)
            assert any("earlier context" in (m["content"] or "") for m in msgs)


def test_default_identity_path_standalone_fallback(tmp_path, monkeypatch):
    import sys
    from agents_traces.identity import default_identity_path

    host = tmp_path / "host"
    monkeypatch.setenv("AGENTS_HOST_DIR", str(host))
    monkeypatch.delenv("AGENTS_USERS_FILE", raising=False)
    monkeypatch.delenv("AGENTS_IDENTITY_PATH", raising=False)
    monkeypatch.setitem(sys.modules, "agents_workspace", None)

    assert default_identity_path() == host.resolve() / "users.json"

    custom = tmp_path / "custom_users.json"
    monkeypatch.setenv("AGENTS_USERS_FILE", str(custom))
    assert default_identity_path() == custom.resolve()


def test_load_normalizes_legacy_alias_keyed_rows(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("AGENTS_HOST_DIR", str(tmp_path / "host"))
    users_path = tmp_path / "users.json"
    users_path.write_text(
        json.dumps(
            {
                "users": {
                    "felix": {"did": "did:key:zABC"},
                    "telegram:42": {"did": "did:key:zDEF"},
                }
            }
        ),
        encoding="utf-8",
    )
    store = IdentityStore(users_path)
    u_felix = store.find_by_alias("felix")
    u_tg = store.find_by_alias("telegram:42")
    assert u_felix is not None
    assert u_felix.id.startswith("u_")
    assert u_felix.aliases == ["felix"]
    assert u_felix.did == "did:key:zABC"
    assert u_tg is not None
    assert u_tg.id.startswith("u_")
    assert u_tg.aliases == ["telegram:42"]
    assert u_tg.did == "did:key:zDEF"


def test_bind_did_shaped_users_json_roundtrip(tmp_path, monkeypatch):
    import json
    from agents_workspace import Host

    host = tmp_path / "host"
    monkeypatch.setenv("AGENTS_HOST_DIR", str(host))
    ws = Host.open(host).create("demo")
    users_path = host / "users.json"
    users_path.write_text(
        json.dumps(
            {
                "users": {
                    "u_112233445566": {
                        "id": "u_112233445566",
                        "aliases": ["telegram:42"],
                        "did": "did:key:zDEF",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    store = IdentityStore(workspace=ws)
    resolved = store.resolve(channel="telegram", user="42")
    assert resolved.user.id == "u_112233445566"
    assert resolved.user.did == "did:key:zDEF"
    assert "telegram:42" in resolved.user.aliases

    reloaded = IdentityStore(workspace=ws)
    user = reloaded.get_user("u_112233445566")
    assert user is not None
    assert user.did == "did:key:zDEF"
    assert user.aliases == ["telegram:42"]


def test_identity_and_session_store_standalone_without_agents_workspace(tmp_path, monkeypatch):
    import agents_traces.identity as mod

    host = tmp_path / "host"
    monkeypatch.setenv("AGENTS_HOST_DIR", str(host))
    monkeypatch.delenv("AGENTS_USERS_FILE", raising=False)
    monkeypatch.delenv("AGENTS_IDENTITY_PATH", raising=False)
    monkeypatch.delenv("AGENTS_WORKSPACE", raising=False)
    monkeypatch.delenv("AGENTS_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(mod, "Workspace", None)

    store = IdentityStore()
    resolved = store.resolve(channel="cli", user="alice")
    assert resolved.user.id.startswith("u_")
    assert resolved.session.persona == "default"
    assert (host / "personas" / "default" / "sessions" / f"{resolved.session.id}.json").is_file()

