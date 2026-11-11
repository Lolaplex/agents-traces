"""Person vs thread vs channel handle.

An alias is how a device names you (`telegram:5712`, `repl:local`).
A user is the person those aliases resolve to (stable `u_…` until DID binds).
A session is a conversation thread (`ses_…`) — **resume from traces**, not identity.

Human profile (name, work, prefs) lives in the workspace memory `USER.md`.
Crypto identity lives on the host in `<host>/keys/` (`did:key`, agents-keys).
This file is alias bindings only (`<host>/users.json`, resolved per call).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from agents_workspace import Workspace

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def mint_ulid() -> str:
    """ULID: 48-bit ms timestamp + 80-bit randomness, Crockford base32."""
    ts_ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ts_ms << 80) | rand
    chars = []
    for _ in range(26):
        chars.append(_ULID_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def default_identity_path(workspace: "Workspace | None" = None) -> Path:
    return (workspace or Workspace.from_env()).identity


def __getattr__(name: str):
    if name == "DEFAULT_PATH":
        # Compatibility: resolved per access, never frozen at import.
        return default_identity_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    """``ses_<ULID>``: kind prefix plus sortable ULID body (D-session-id)."""
    return "ses_" + mint_ulid()


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
    """A conversation thread keyed by workspace + user + persona.

    Sessions live under ``<ws>/personas/<persona>/sessions/<id>.json`` and
    are created only via API/CLI resolve paths - never implicitly by reads.
    """
    id: str
    user: str
    start_date: str
    workspace: str = ""
    persona: str = ""
    channel: str = ""
    workdir: str = ""
    created_at: str = ""
    last_active: str = ""
    prev_session: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "user": self.user,
            "start_date": self.start_date,
            "created_at": self.created_at,
        }
        for key in ("workspace", "persona", "channel", "workdir",
                    "last_active", "prev_session"):
            value = getattr(self, key)
            if value:
                d[key] = value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionRecord":
        return cls(
            id=str(data["id"]),
            user=str(data.get("user") or ""),
            start_date=str(data.get("start_date") or today_utc()),
            workspace=str(data.get("workspace") or ""),
            persona=str(data.get("persona") or ""),
            channel=str(data.get("channel") or ""),
            workdir=str(data.get("workdir") or ""),
            created_at=str(data.get("created_at") or ""),
            last_active=str(data.get("last_active") or ""),
            prev_session=str(data.get("prev_session") or ""),
        )


@dataclass
class Resolved:
    user: UserRecord
    session: SessionRecord
    alias: str
    legacy: bool = False


class SessionStore:
    """Session records under ``<ws>/personas/<persona>/sessions/<id>.json``.

    Keyed by (workspace, persona, user). Legacy sessions land on the
    workspace's first persona (`default`) - the first created, not a
    privileged one.
    """

    def __init__(self, workspace: "Workspace | None" = None):
        self.ws = workspace or Workspace.from_env()

    def _dir(self, persona: str) -> Path:
        return self.ws.personas / persona / "sessions"

    def _path(self, persona: str, session_id: str) -> Path:
        return self._dir(persona) / f"{session_id}.json"

    def put(self, rec: SessionRecord, persona: str = "") -> SessionRecord:
        p = persona or rec.persona or self.ws.default_persona or "default"
        rec.persona = p
        rec.workspace = rec.workspace or self.ws.id
        path = self._path(p, rec.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec.to_dict(), indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, path)
        return rec

    def get(self, session_id: str, persona: str = "") -> Optional[SessionRecord]:
        personas = [persona] if persona else self._persona_ids()
        for p in personas:
            path = self._path(p, session_id)
            if path.is_file():
                try:
                    return SessionRecord.from_dict(
                        json.loads(path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        return None

    def _persona_ids(self) -> list[str]:
        root = self.ws.personas
        if not root.is_dir():
            return []
        return sorted(e.name for e in root.iterdir()
                      if e.is_dir() and (e / "sessions").is_dir())

    def list(self, persona: str = "", user: str = "") -> list[SessionRecord]:
        out: list[SessionRecord] = []
        for p in ([persona] if persona else self._persona_ids()):
            d = self._dir(p)
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.json")):
                try:
                    rec = SessionRecord.from_dict(
                        json.loads(f.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
                if user and rec.user != user:
                    continue
                out.append(rec)
        return out


def resolve_persona(explicit: str = "", workspace: "Workspace | None" = None) -> str:
    """Persona for a new session: explicit > AGENTS_PERSONA > ws default."""
    pick = (explicit or "").strip() or os.environ.get("AGENTS_PERSONA", "").strip()
    if pick:
        return pick
    ws = workspace or Workspace.from_env()
    return ws.default_persona or "default"


class IdentityStore:
    def __init__(
        self,
        path: Path | str | None = None,
        workspace: "Workspace | None" = None,
    ):
        self.ws = workspace or Workspace.from_env()
        self.ws_id = self.ws.id
        self.path = Path(path) if path else default_identity_path(self.ws)
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

    def _mint_session(self, user: UserRecord, *, persona: str = "",
                      channel: str = "", workdir: str = "") -> SessionRecord:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return SessionRecord(
            id=mint_session_id(),
            user=user.id,
            start_date=today_utc(),
            workspace=self.ws_id,
            persona=persona,
            channel=channel,
            workdir=workdir,
            created_at=now,
            last_active=now,
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
        max_idle_hours: float = 0.0,
    ) -> Optional[SessionRecord]:
        if trace_lookup is None:
            try:
                from .store import TraceStore

                trace_lookup = TraceStore().find_latest_session
            except ImportError:
                return None
        try:
            sid = trace_lookup(
                channel=channel,
                user=str(user),
                project=project,
                alias=alias,
                max_idle_hours=max_idle_hours,
            )
        except TypeError:
            sid = trace_lookup(
                channel=channel, user=str(user), project=project, alias=alias
            )
        if not sid:
            return None
        return SessionRecord(id=sid, user="", start_date=today_utc(), created_at="")

    def _record_rollover(self, session_id: str, prev_session: str) -> None:
        """Persist the rollover link as a trace event (best effort)."""
        try:
            from .models import TraceEvent
            from .store import TraceStore

            TraceStore().append(
                TraceEvent(
                    session=session_id,
                    type="rollover",
                    metadata={"prev_session": prev_session},
                )
            )
        except Exception:
            pass

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
        persona: str = "",
        workdir: str = "",
        new_session: bool = False,
        project: str = "",
        legacy_exists: Optional[Callable[[str], bool]] = None,
        max_idle_hours: float = 4.0,
        trace_lookup: Optional[Callable[..., Optional[str]]] = None,
    ) -> Resolved:
        """Map channel handle (+ optional session) onto one person + thread."""
        alias = alias_id(channel, user) if (channel and str(user).strip()) else ""
        if not alias and not user_id and not session:
            raise ValueError("channel+user, user_id, or session is required")

        self._ensure()
        sessions = SessionStore(self.ws)
        persona_id = resolve_persona(persona, self.ws)

        if session and session.lower() not in ("new", "new_session"):
            trace_uid = self._user_id_from_traces(session)
            person = self._ensure_user(alias=alias, user_id=user_id or trace_uid)
            existing = sessions.get(session)
            rec = existing or SessionRecord(
                id=session,
                user=person.id,
                start_date=today_utc(),
                workspace=self.ws_id,
                # an unknown session lands on the first persona: it was the
                # first created, not a privileged default
                persona=persona_id,
                channel=channel,
                workdir=workdir,
                created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            if not existing:
                sessions.put(rec)
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
            rec = sessions.put(
                self._mint_session(person, persona=persona_id,
                                   channel=channel, workdir=workdir))
            self.save()
            return Resolved(user=person, session=rec, alias=alias)

        traced = self._session_from_traces(
            channel=channel,
            user=user,
            project=project,
            alias=alias,
            trace_lookup=trace_lookup,
            max_idle_hours=max_idle_hours,
        )
        if traced is not None:
            traced.user = person.id
            existing = sessions.get(traced.id)
            if existing:
                traced = existing
            else:
                traced.workspace = self.ws_id
                traced.persona = persona_id
                traced.channel = channel
                sessions.put(traced)
            self.save()
            return Resolved(
                user=person,
                session=traced,
                alias=alias,
                legacy=not traced.id.startswith("ses_"),
            )

        # Not resumed: either nothing traced yet, or the traced session was
        # stale. If it was stale, link the fresh mint to it — the rollover is
        # recorded as a trace event so context continuity survives without a
        # session store.
        stale = (
            self._session_from_traces(
                channel=channel,
                user=user,
                project=project,
                alias=alias,
                trace_lookup=trace_lookup,
                max_idle_hours=0.0,
            )
            if max_idle_hours > 0
            else None
        )
        if stale is not None:
            rec = self._mint_session(person, persona=persona_id,
                                     channel=channel, workdir=workdir)
            rec.prev_session = stale.id
            self._record_rollover(rec.id, stale.id)
            sessions.put(rec)
            self.save()
            return Resolved(user=person, session=rec, alias=alias)

        if alias and channel and str(user).strip():
            old = legacy_session_id(channel, user)
            exists = bool(legacy_exists(old)) if legacy_exists is not None else False
            if exists:
                # legacy channel-user thread migrates onto the first persona
                rec = SessionRecord(
                    id=old, user=person.id, start_date=today_utc(),
                    workspace=self.ws_id, persona=persona_id,
                    channel=channel, workdir=workdir, created_at="")
                sessions.put(rec)
                self.save()
                return Resolved(user=person, session=rec, alias=alias, legacy=True)

        rec = sessions.put(
            self._mint_session(person, persona=persona_id,
                               channel=channel, workdir=workdir))
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
