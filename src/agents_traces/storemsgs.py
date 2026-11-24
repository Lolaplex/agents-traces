"""Optional read of the synced ``msg:*`` projection in ``<ws>/store.db``.

Klanker's msgsync projects trace messages into the workspace store so a
peer that receives ``store.db`` (without the peer's traces) can still
read session history. This module is the traces-side reader: soft-import
of ``agents_store`` only — no store.db, no agents_store, or an empty
projection all degrade to zero rows and the JSONL trace stays the local
source of truth.

Read semantics mirror ``klanker.msgsync``: a message is live unless
``msg:deleted`` or its ``msg:frame_id`` names a retired frame, and rows
order by ``(ts, ord, entity)``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def db_path(traces_dir: Optional[Path] = None) -> Optional[Path]:
    """``<ws>/store.db`` for the active workspace; None when absent."""
    raw = os.environ.get("AGENTS_STORE_DB", "").strip()
    if raw:
        path = Path(raw)
    else:
        try:
            from agents_workspace import Workspace

            path = Path(Workspace.from_env().store_db)
        except Exception:
            if traces_dir is None:
                return None
            path = Path(traces_dir).parent / "store.db"
    return path if path.is_file() else None


def _connect(path: Path):
    try:
        from agents_store.db import connect
    except ImportError:
        return None
    try:
        return connect(str(path), check_same_thread=False)
    except Exception:
        return None


def _entity_ords(conn, entities: list[str]) -> dict[str, str]:
    if not entities:
        return {}
    marks = ",".join("?" for _ in entities)
    return {
        e: str(o or "")
        for e, o in conn.execute(
            f"SELECT e, MAX(ord) FROM datom WHERE e IN ({marks}) GROUP BY e",
            entities,
        )
    }


def _live(conn, session_id: str) -> list[dict]:
    """Live ``msg:*`` rows of one session: not deleted, not superseded by
    a retired frame. Ordered ``(ts, ord, entity)`` like the projector."""
    rows: dict[str, dict] = {}
    for e, a, v in conn.execute(
        "SELECT e, a, v FROM v_current WHERE e IN ("
        " SELECT e FROM v_current WHERE a = 'msg:session' AND v = ?)",
        (session_id,),
    ):
        rows.setdefault(e, {"e": e})[a.split(":", 1)[1]] = v
    retired = {
        r[0]
        for r in conn.execute(
            "SELECT r.e FROM v_current r JOIN v_current s ON s.e = r.e"
            " WHERE r.a = 'frame:retired' AND r.v = 'true'"
            " AND s.a = 'frame:session' AND s.v = ?",
            (session_id,),
        )
    }
    live = [
        r
        for r in rows.values()
        if r.get("deleted") != "true"
        and str(r.get("frame_id") or "") not in retired
        and str(r.get("text") or "").strip()
    ]
    ords = _entity_ords(conn, [r["e"] for r in live])
    return sorted(
        live, key=lambda r: (str(r.get("ts") or ""), ords.get(r["e"], ""), r["e"])
    )


def session_messages(session_id: str, *, traces_dir=None) -> list[dict]:
    """Projected ``{role, text, ts, persona, user, host}`` rows; [] when
    the synced projection is unavailable for this session."""
    path = db_path(Path(traces_dir) if traces_dir else None)
    if path is None:
        return []
    conn = _connect(path)
    if conn is None:
        return []
    try:
        out = []
        for r in _live(conn, session_id):
            out.append(
                {
                    "role": str(r.get("role") or "user"),
                    "text": str(r.get("text") or ""),
                    "ts": str(r.get("ts") or ""),
                    "persona": str(r.get("persona") or ""),
                    "user": str(r.get("user") or ""),
                    "host": str(r.get("host") or ""),
                }
            )
        return out
    except Exception:
        return []
    finally:
        conn.close()


def find_latest_session(
    *,
    user: str = "",
    alias: str = "",
    max_idle_hours: float = 0.0,
) -> Optional[str]:
    """Resume from the synced projection: newest session whose live msgs
    carry ``msg:user`` matching the alias (or raw user when unaliased).

    Mirrors ``TraceStore.find_latest_session`` for hosts whose local
    traces are empty (store.db arrived without the peer's jsonl).
    """
    path = db_path()
    if path is None:
        return None
    conn = _connect(path)
    if conn is None:
        return None
    try:
        want = {w for w in (str(user or ""), str(alias or "")) if w}
        last: dict[str, str] = {}
        rows: dict[str, dict] = {}
        for e, a, v in conn.execute(
            "SELECT e, a, v FROM v_current"
            " WHERE a IN ('msg:session', 'msg:ts', 'msg:user', 'msg:deleted')"
        ):
            rows.setdefault(e, {})[a] = v
        for r in rows.values():
            sid = str(r.get("msg:session") or "")
            if not sid or r.get("msg:deleted") == "true":
                continue
            mu = str(r.get("msg:user") or "")
            # mirrors TraceStore.find_latest_session: a msg without a
            # recorded user never matches a non-empty user/alias filter
            if want and mu not in want:
                continue
            ts = str(r.get("msg:ts") or "")
            if ts > last.get(sid, ""):
                last[sid] = ts
        if not last:
            return None
        sid, last_ts = max(last.items(), key=lambda kv: kv[1])
        if max_idle_hours > 0 and last_ts:
            try:
                from datetime import datetime, timezone

                last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                idle = (now - last_dt).total_seconds() / 3600.0
                if last_dt.date() != now.date() and idle >= max_idle_hours:
                    return None
            except Exception:
                pass
        return sid
    except Exception:
        return None
    finally:
        conn.close()
