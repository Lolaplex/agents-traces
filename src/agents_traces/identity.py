"""Person vs thread vs channel handle.

An alias is how a device names you (`telegram:5712`, `http:fabian`).
A user is the person those aliases resolve to.
A session is a conversation thread the person can resume from any device.

DID / plex-net later. This file is the local directory (`~/.agents/identity.json`).
Conversation bodies stay in traces; this map is not markdown memory.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

def default_identity_path() -> Path:
    override = os.environ.get("AGENTS_IDENTITY_PATH", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".agents" / "identity.json"


DEFAULT_PATH = default_identity_path()


def alias_id(channel: str, user: str | int) -> str:
    """Channel-scoped handle. Not a session id."""
    ch = (channel or "local").strip().lower().replace(" ", "-")
    uid = str(user).strip()
    if not ch:
        raise ValueError("channel is required")
    if not uid:
        raise ValueError("user provenance is required")
    return f"{ch}:{uid}"


def legacy_session_id(channel: str, user: str | int) -> str:
    """Pre-identity session key (`telegram-42`). Still valid if that trace exists."""
    ch = (channel or "local").strip().lower().replace(" ", "-")
    uid = str(user).strip()
    if not uid:
        raise ValueError("user provenance is required")
    return f"{ch}-{uid}"


def mint_session_id() -> str:
    return "ses_" + uuid.uuid4().hex[:12]


def mint_user_id() -> str:
    return "u_" + uuid.uuid4().hex[:12]


def today_utc() -> str:
    return date.today().isoformat()


@dataclass
class UserRecord:
    id: str
    display: str = ""
    work: str = ""
    project: str = ""
    timezone: str = ""
    aliases: list[str] = field(default_factory=list)
    active_session: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display": self.display,
            "work": self.work,
            "project": self.project,
            "timezone": self.timezone,
            "aliases": list(self.aliases),
            "active_session": self.active_session,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserRecord":
        aliases = data.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        return cls(
            id=str(data["id"]),
            display=str(data.get("display") or ""),
            work=str(data.get("work") or ""),
            project=str(data.get("project") or ""),
            timezone=str(data.get("timezone") or ""),
            aliases=[str(a) for a in aliases],
            active_session=str(data.get("active_session") or ""),
        )


@dataclass
class SessionRecord:
    id: str
    user: str
    start_date: str
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user": self.user,
            "start_date": self.start_date,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionRecord":
        return cls(
            id=str(data["id"]),
            user=str(data.get("user") or ""),
            start_date=str(data.get("start_date") or today_utc()),
            created_at=str(data.get("created_at") or ""),
        )


@dataclass
class Resolved:
    user: UserRecord
    session: SessionRecord
    alias: str
    legacy: bool = False


class IdentityStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_identity_path()
        self._users: dict[str, UserRecord] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._loaded = False

    def load(self) -> None:
        self._loaded = True
        self._users = {}
        self._sessions = {}
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        users = data.get("users") or {}
        if isinstance(users, dict):
            for key, row in users.items():
                if isinstance(row, dict):
                    rec = UserRecord.from_dict({**row, "id": row.get("id") or key})
                    self._users[rec.id] = rec
        sessions = data.get("sessions") or {}
        if isinstance(sessions, dict):
            for key, row in sessions.items():
                if isinstance(row, dict):
                    rec = SessionRecord.from_dict({**row, "id": row.get("id") or key})
                    self._sessions[rec.id] = rec

    def _ensure(self) -> None:
        if not self._loaded:
            self.load()

    def save(self) -> None:
        self._ensure()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "users": {u.id: u.to_dict() for u in self._users.values()},
            "sessions": {s.id: s.to_dict() for s in self._sessions.values()},
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def find_by_alias(self, alias: str) -> Optional[UserRecord]:
        self._ensure()
        for user in self._users.values():
            if alias in user.aliases:
                return user
        return None

    def get_user(self, user_id: str) -> Optional[UserRecord]:
        self._ensure()
        return self._users.get(user_id)

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        self._ensure()
        return self._sessions.get(session_id)

    def bind_alias(self, user_id: str, alias: str) -> UserRecord:
        self._ensure()
        user = self._users.get(user_id)
        if user is None:
            raise KeyError(user_id)
        if alias and alias not in user.aliases:
            user.aliases.append(alias)
            self.save()
        return user

    def _new_session(self, user: UserRecord, *, start_date: str | None = None) -> SessionRecord:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        rec = SessionRecord(
            id=mint_session_id(),
            user=user.id,
            start_date=start_date or today_utc(),
            created_at=now,
        )
        self._sessions[rec.id] = rec
        user.active_session = rec.id
        return rec

    def _ensure_user(
        self,
        *,
        alias: str,
        user_id: str = "",
        display: str = "",
        work: str = "",
        project: str = "",
    ) -> UserRecord:
        self._ensure()
        if user_id:
            user = self._users.get(user_id)
            if user is None:
                user = UserRecord(
                    id=user_id,
                    display=display,
                    work=work,
                    project=project,
                    aliases=[alias] if alias else [],
                )
                self._users[user.id] = user
            elif alias:
                if alias not in user.aliases:
                    user.aliases.append(alias)
            if display and not user.display:
                user.display = display
            if work and not user.work:
                user.work = work
            if project:
                user.project = project
            return user
        if alias:
            found = self.find_by_alias(alias)
            if found:
                if project:
                    found.project = project
                return found
        uid = mint_user_id()
        user = UserRecord(
            id=uid,
            display=display,
            work=work,
            project=project,
            aliases=[alias] if alias else [],
        )
        self._users[uid] = user
        return user

    def resolve(
        self,
        *,
        channel: str = "",
        user: str | int = "",
        user_id: str = "",
        session: str = "",
        new_session: bool = False,
        display: str = "",
        work: str = "",
        project: str = "",
        legacy_exists: Optional[Callable[[str], bool]] = None,
    ) -> Resolved:
        """Map a channel handle (and optional session/user_id) onto one person + thread."""
        alias = alias_id(channel, user) if (channel and str(user).strip()) else ""
        if not alias and not user_id and not session:
            raise ValueError("channel+user, user_id, or session is required")

        self._ensure()
        legacy = False

        if session and session.lower() not in ("new", "new_session"):
            rec = self._sessions.get(session)
            if rec is None:
                rec = SessionRecord(
                    id=session,
                    user="",
                    start_date=today_utc(),
                    created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                )
                self._sessions[session] = rec
            person = None
            if rec.user:
                person = self._users.get(rec.user)
            if person is None:
                person = self._ensure_user(
                    alias=alias,
                    user_id=user_id or rec.user,
                    display=display,
                    work=work,
                    project=project,
                )
                rec.user = person.id
            elif alias:
                if alias not in person.aliases:
                    person.aliases.append(alias)
            if project:
                person.project = project
            person.active_session = rec.id
            self.save()
            return Resolved(user=person, session=rec, alias=alias, legacy=not rec.id.startswith("ses_"))

        person = self._ensure_user(
            alias=alias,
            user_id=user_id,
            display=display,
            work=work,
            project=project,
        )

        if new_session or session.lower() in ("new", "new_session"):
            rec = self._new_session(person)
            self.save()
            return Resolved(user=person, session=rec, alias=alias)

        if person.active_session and person.active_session in self._sessions:
            rec = self._sessions[person.active_session]
            self.save()
            return Resolved(user=person, session=rec, alias=alias)

        if alias and channel and str(user).strip():
            old = legacy_session_id(channel, user)
            exists = False
            if legacy_exists is not None:
                exists = bool(legacy_exists(old))
            if exists:
                rec = SessionRecord(
                    id=old,
                    user=person.id,
                    start_date=today_utc(),
                    created_at="",
                )
                self._sessions[old] = rec
                person.active_session = old
                self.save()
                return Resolved(user=person, session=rec, alias=alias, legacy=True)

        rec = self._new_session(person)
        self.save()
        return Resolved(user=person, session=rec, alias=alias, legacy=legacy)


def session_has_events(session: str, store: Any | None = None) -> bool:
    """True if traces already contain this session id (legacy telegram-N keys)."""
    try:
        from .store import TraceStore
    except ImportError:
        return False
    target = store or TraceStore()
    for event in target.iter_events(session_id=session):
        return True
    return False
