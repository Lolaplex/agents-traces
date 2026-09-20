"""Person vs thread vs channel handle.

An alias is how a device names you (`telegram:5712`, `repl:local`).
A user is the person those aliases resolve to (stable `u_…` until DID binds).
A session is a conversation thread (`ses_…`) — **resume from traces**, not identity.

Human profile (name, work, prefs) lives in `~/.agents/memory/USER.md`.
Crypto identity lives in `~/.agents/keys/` (`did:key`).
This file is alias bindings only (`~/.agents/identity.json`).
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
    aliases: list[str] = field(default_factory=list)
    did: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "aliases": list(self.aliases)}
        if self.did:
            out["did"] = self.did
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserRecord":
        aliases = data.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        return cls(
            id=str(data["id"]),
            aliases=[str(a) for a in aliases],
            did=str(data.get("did") or ""),
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
        self._loaded = False

    def load(self) -> None:
        self._loaded = True
        self._users = {}
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

    def _ensure(self) -> None:
        if not self._loaded:
            self.load()

    def save(self) -> None:
        self._ensure()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"users": {u.id: u.to_dict() for u in self._users.values()}}
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

    def bind_alias(self, user_id: str, alias: str) -> UserRecord:
        self._ensure()
        user = self._users.get(user_id)
        if user is None:
            raise KeyError(user_id)
        if alias and alias not in user.aliases:
            user.aliases.append(alias)
            self.save()
        return user

    def _mint_session(self, user: UserRecord) -> SessionRecord:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return SessionRecord(
            id=mint_session_id(),
            user=user.id,
            start_date=today_utc(),
            created_at=now,
        )

    def _ensure_user(self, *, alias: str, user_id: str = "", did: str = "") -> UserRecord:
        self._ensure()
        if user_id:
            user = self._users.get(user_id)
            if user is None:
                user = UserRecord(id=user_id, aliases=[alias] if alias else [], did=did)
                self._users[user.id] = user
            elif alias and alias not in user.aliases:
                user.aliases.append(alias)
            if did and not user.did:
                user.did = did
            return user
        if alias:
            found = self.find_by_alias(alias)
            if found:
                return found
        uid = mint_user_id()
        user = UserRecord(id=uid, aliases=[alias] if alias else [], did=did)
        self._users[uid] = user
        return user

    def _session_from_traces(
        self,
        *,
        channel: str,
        user: str | int,
        project: str,
        alias: str,
        trace_lookup: Optional[Callable[..., Optional[str]]] = None,
    ) -> Optional[SessionRecord]:
        if trace_lookup is None:
            try:
                from .store import TraceStore

                trace_lookup = TraceStore().find_latest_session
            except ImportError:
                return None
        sid = trace_lookup(channel=channel, user=str(user), project=project, alias=alias)
        if not sid:
            return None
        return SessionRecord(id=sid, user="", start_date=today_utc(), created_at="")

    def _user_id_from_traces(self, session_id: str) -> str:
        try:
            from .store import TraceStore

            for event in TraceStore().iter_events(session_id=session_id):
                md = event.metadata if isinstance(event.metadata, dict) else {}
                uid = md.get("user_id")
                if uid:
                    return str(uid)
        except ImportError:
            pass
        return ""

    def resolve(
        self,
        *,
        channel: str = "",
        user: str | int = "",
        user_id: str = "",
        session: str = "",
        new_session: bool = False,
        project: str = "",
        legacy_exists: Optional[Callable[[str], bool]] = None,
        trace_lookup: Optional[Callable[..., Optional[str]]] = None,
    ) -> Resolved:
        """Map channel handle (+ optional session) onto one person + thread."""
        alias = alias_id(channel, user) if (channel and str(user).strip()) else ""
        if not alias and not user_id and not session:
            raise ValueError("channel+user, user_id, or session is required")

        self._ensure()

        if session and session.lower() not in ("new", "new_session"):
            trace_uid = self._user_id_from_traces(session)
            person = self._ensure_user(alias=alias, user_id=user_id or trace_uid)
            rec = SessionRecord(
                id=session,
                user=person.id,
                start_date=today_utc(),
                created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            if alias and alias not in person.aliases:
                person.aliases.append(alias)
            self.save()
            return Resolved(
                user=person,
                session=rec,
                alias=alias,
                legacy=not rec.id.startswith("ses_"),
            )

        person = self._ensure_user(alias=alias, user_id=user_id)

        if new_session or session.lower() in ("new", "new_session"):
            rec = self._mint_session(person)
            self.save()
            return Resolved(user=person, session=rec, alias=alias)

        traced = self._session_from_traces(
            channel=channel,
            user=user,
            project=project,
            alias=alias,
            trace_lookup=trace_lookup,
        )
        if traced is not None:
            traced.user = person.id
            self.save()
            return Resolved(
                user=person,
                session=traced,
                alias=alias,
                legacy=not traced.id.startswith("ses_"),
            )

        if alias and channel and str(user).strip():
            old = legacy_session_id(channel, user)
            exists = bool(legacy_exists(old)) if legacy_exists is not None else False
            if exists:
                rec = SessionRecord(id=old, user=person.id, start_date=today_utc(), created_at="")
                self.save()
                return Resolved(user=person, session=rec, alias=alias, legacy=True)

        rec = self._mint_session(person)
        self.save()
        return Resolved(user=person, session=rec, alias=alias)


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
