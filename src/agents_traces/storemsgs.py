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
    """``<ws>/store.db`` next to ``traces_dir``, else the env workspace's.

    An explicit ``traces_dir`` pins the workspace: a caller reading
    ``TraceStore(other_dir)`` must see ``other_dir``'s sibling store.db,
    never ``Workspace.from_env().store_db`` — and must not resolve the
    env workspace at all (its domain-override warning is noise here).
    ``AGENTS_STORE_DB`` remains the explicit override. None when absent.
    """
    raw = os.environ.get("AGENTS_STORE_DB", "").strip()
    if raw:
        path = Path(raw)
    elif traces_dir is not None:
        path = Path(traces_dir).parent / "store.db"
    else:
        try:
            from agents_workspace import Workspace

            path = Path(Workspace.from_env().store_db)
        except Exception:
            return None
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


def _msg_rows(conn, session_id: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for e, a, v in conn.execute(
        "SELECT e, a, v FROM v_current WHERE e IN ("
        " SELECT e FROM v_current WHERE a = 'msg:session' AND v = ?)",
        (session_id,),
    ):
        rows.setdefault(e, {"e": e})[a.split(":", 1)[1]] = v
    return rows


def _retired(conn, session_id: str) -> set[str]:
    """``frame:*`` entities of this session flagged ``frame:retired``."""
    return {
        r[0]
        for r in conn.execute(
            "SELECT r.e FROM v_current r JOIN v_current s ON s.e = r.e"
            " WHERE r.a = 'frame:retired' AND r.v = 'true'"
            " AND s.a = 'frame:session' AND s.v = ?",
            (session_id,),
        )
    }


def _live(conn, session_id: str) -> list[dict]:
    """Live ``msg:*`` rows of one session: not deleted, not superseded by
    a retired frame. Ordered ``(ts, ord, entity)`` like the projector."""
    rows = _msg_rows(conn, session_id)
    retired = _retired(conn, session_id)
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


def _dead(conn, session_id: str) -> list[dict]:
    """Tombstoned ``msg:*`` rows (``msg:deleted='true'``): the projection's
    record of turns a peer ``/undo``-ed or a frame covered, so a host
    holding the raw JSONL can suppress its own cached copies."""
    return [
        r
        for r in _msg_rows(conn, session_id).values()
        if r.get("deleted") == "true"
    ]


def _frame_spans(conn, session_id: str) -> list[tuple[str, str]]:
    """``(first_ts, last_ts)`` coverage of this session's ACTIVE frames.

    Retired frames are skipped: a newer frame superseded their span, and
    the rows they covered stay suppressed via ``msg:deleted`` anyway.
    """
    rows: dict[str, dict] = {}
    for e, a, v in conn.execute(
        "SELECT e, a, v FROM v_current WHERE e IN ("
        " SELECT e FROM v_current WHERE a = 'frame:session' AND v = ?)",
        (session_id,),
    ):
        rows.setdefault(e, {})[a] = v
    retired = _retired(conn, session_id)
    spans = []
    for e, r in rows.items():
        if e in retired:
            continue
        first = str(r.get("frame:first_ts") or "")
        last = str(r.get("frame:last_ts") or "")
        if first and last:
            spans.append((first, last))
    return spans


def session_projection(session_id: str, *, traces_dir=None) -> dict:
    """One-connection read of the synced projection for ``session_id``:
    ``live`` msg rows, ``dead`` tombstoned rows, and active ``frames``
    coverage spans. All empty when the projection is unavailable."""
    empty: dict = {"live": [], "dead": [], "frames": []}
    path = db_path(Path(traces_dir) if traces_dir else None)
    if path is None:
        return empty
    conn = _connect(path)
    if conn is None:
        return empty
    try:
        live = [
            {
                "role": str(r.get("role") or "user"),
                "text": str(r.get("text") or ""),
                "ts": str(r.get("ts") or ""),
                "persona": str(r.get("persona") or ""),
                "user": str(r.get("user") or ""),
                "host": str(r.get("host") or ""),
            }
            for r in _live(conn, session_id)
        ]
        dead = [
            {
                "role": str(r.get("role") or "user"),
                "text": str(r.get("text") or ""),
                "ts": str(r.get("ts") or ""),
            }
            for r in _dead(conn, session_id)
        ]
        return {"live": live, "dead": dead,
                "frames": _frame_spans(conn, session_id)}
    except Exception:
        return empty
    finally:
        conn.close()


def session_messages(session_id: str, *, traces_dir=None) -> list[dict]:
    """Projected ``{role, text, ts, persona, user, host}`` rows; [] when
    the synced projection is unavailable for this session."""
    return session_projection(session_id, traces_dir=traces_dir)["live"]


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
            " WHERE a IN ('msg:session', 'msg:ts', 'msg:user', 'msg:deleted',"
            "            'msg:frame_id')"
        ):
            rows.setdefault(e, {})[a] = v
        # session -> retired frame entities; a msg whose msg:frame_id names
        # one is superseded (retired compaction summary) and must not count
        # for recency — same liveness rule as _live().
        retired: dict[str, set] = {}
        for sid, fe in conn.execute(
            "SELECT s.v, r.e FROM v_current r JOIN v_current s ON s.e = r.e"
            " WHERE r.a = 'frame:retired' AND r.v = 'true'"
            " AND s.a = 'frame:session'"
        ):
            retired.setdefault(str(sid), set()).add(fe)
        for r in rows.values():
            sid = str(r.get("msg:session") or "")
            if not sid or r.get("msg:deleted") == "true":
                continue
            if str(r.get("msg:frame_id") or "") in retired.get(sid, ()):
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
