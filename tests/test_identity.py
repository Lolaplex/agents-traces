import tempfile
from pathlib import Path

from agents_traces.identity import IdentityStore, alias_id, legacy_session_id
from agents_traces.assemble import session_id_for


def test_alias_is_not_session():
    assert alias_id("telegram", 42) == "telegram:42"
    assert alias_id("http", "fabian") == "http:fabian"
    assert session_id_for("telegram", 42) == "telegram-42"
    assert legacy_session_id("telegram", 42) == "telegram-42"


def test_same_user_across_devices_resumes_session():
    with tempfile.TemporaryDirectory() as tmp:
        store = IdentityStore(Path(tmp) / "identity.json")
        first = store.resolve(channel="telegram", user="5712")
        second = store.resolve(channel="http", user="laptop", user_id=first.user.id)
        assert second.user.id == first.user.id
        assert second.session.id == first.session.id
        assert "telegram:5712" in second.user.aliases
        assert "http:laptop" in second.user.aliases


def test_explicit_session_resumes_from_other_alias():
    with tempfile.TemporaryDirectory() as tmp:
        store = IdentityStore(Path(tmp) / "identity.json")
        first = store.resolve(channel="telegram", user="1")
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


def test_stale_session_auto_rollover_on_new_day():
    with tempfile.TemporaryDirectory() as tmp:
        store = IdentityStore(Path(tmp) / "identity.json")
        first = store.resolve(channel="telegram", user="5712")
        # Simulate old session from 3 days ago with last_active 8 hours ago
        first.session.start_date = "2026-09-01"
        first.session.last_active = "2026-09-01T12:00:00Z"
        store.save()

        # Resolving now on a new day should roll over to a fresh session
        second = store.resolve(channel="telegram", user="5712")
        assert second.user.id == first.user.id
        assert second.session.id != first.session.id
        assert second.session.last_active


def test_recent_session_across_midnight_does_not_rollover():
    from datetime import datetime, timezone, timedelta
    with tempfile.TemporaryDirectory() as tmp:
        store = IdentityStore(Path(tmp) / "identity.json")
        first = store.resolve(channel="telegram", user="5712")
        # Simulate yesterday's date, but active 15 minutes ago
        first.session.start_date = "2026-09-01"
        recent = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
        first.session.last_active = recent
        store.save()

        # Should NOT roll over because user was active recently (< 4h)
        second = store.resolve(channel="telegram", user="5712")
        assert second.session.id == first.session.id

