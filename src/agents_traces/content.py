"""Content-addressed bodies for trace records.

JSONL stays bounded. A string longer than ``INLINE_CONTENT_CHARS`` keeps a
prefix on the event and the full UTF-8 text under ``content/<sha[:2]>/<sha>``.
The hash is SHA-256 of those bytes. ``digest`` is the first 12 hex characters,
the same value ``<ref digest="...">`` already prints.

``read_content`` is a host-optional backend: this package never imports a host.
A host that does not want traces simply does not call it.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from .models import TraceEvent

INLINE_CONTENT_CHARS = 20000
_HEX = frozenset("0123456789abcdef")
_SHA_FILE = re.compile(r"[a-f0-9]{64}")
_SHA_IN_TEXT = re.compile(r"\b[a-f0-9]{64}\b")


class ContentError(Exception):
    """Base error for retained-content reads."""

    code = "content_error"


class DigestMismatchError(ContentError):
    """Stored bytes do not hash to the digest the caller asked for."""

    code = "digest_mismatch"

    def __init__(self, expected: str, actual: str, message: str = ""):
        self.expected = expected
        self.actual = actual
        detail = message or f"digest mismatch: expected {expected}, got {actual}"
        super().__init__(detail)


class ContentNotFoundError(ContentError):
    """No retained body for this session and digest."""

    code = "not_found"

    def __init__(self, message: str, *, session: str = "", digest: str = ""):
        self.session = session
        self.digest = digest
        super().__init__(message)


class AmbiguousDigestError(ContentError):
    """The digest selects more than one distinct body in the session."""

    code = "ambiguous_digest"

    def __init__(self, digest: str):
        self.digest = digest
        super().__init__(f"digest {digest} matches multiple bodies")


@dataclass(frozen=True)
class ContentSlice:
    """A verified window of one retained body.

    ``digest`` is the SHA-256 of the full text, not of ``text`` when the
    window is partial. ``truncated`` is true when ``text`` is not the full
    body. ``stored_truncated`` is true when the JSONL field itself was a
    prefix. ``total_chars`` is the original length in Unicode code points.
    """

    session: str
    digest: str
    digest_short: str
    text: str
    offset: int
    limit: Optional[int]
    total_chars: int
    truncated: bool
    stored_truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "digest": self.digest,
            "digest_short": self.digest_short,
            "text": self.text,
            "offset": self.offset,
            "limit": self.limit,
            "total_chars": self.total_chars,
            "truncated": self.truncated,
            "stored_truncated": self.stored_truncated,
        }


@dataclass(frozen=True)
class _Payload:
    inline: str
    sha256: str
    digest: str
    truncated: bool


def content_sha256(text: str) -> str:
    """SHA-256 hex of the UTF-8 bytes of ``text``."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def content_digest(text: str) -> str:
    """12-hex prefix stored in ``<ref digest="...">``."""
    return content_sha256(text)[:12]


def normalize_digest(digest: str) -> str:
    """Accept the 12-char ref digest or the full 64-char SHA-256."""
    cleaned = (digest or "").strip().lower()
    if not cleaned or any(ch not in _HEX for ch in cleaned) or len(cleaned) not in (12, 64):
        raise ValueError("digest must be 12 or 64 hex characters")
    return cleaned


def digest_matches(text: str, digest: str) -> bool:
    full = content_sha256(text)
    if len(digest) == 64:
        return full == digest
    if len(digest) == 12:
        return full.startswith(digest)
    return False


def blob_path(traces_dir: Path, sha: str) -> Path:
    sha = sha.strip().lower()
    return traces_dir / "content" / sha[:2] / sha


def write_content(traces_dir: Path, text: str) -> str:
    """Store ``text`` by its SHA-256. Idempotent. Replaces a corrupt file."""
    data = (text or "").encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    path = blob_path(traces_dir, sha)
    if path.is_file():
        try:
            if path.read_bytes() == data:
                return sha
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{sha}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return sha


def read_blob(traces_dir: Path, sha: str) -> str:
    """Return the artifact for ``sha`` after checking the hash."""
    cleaned = (sha or "").strip().lower()
    if not _SHA_FILE.fullmatch(cleaned):
        raise ContentNotFoundError(f"content blob missing: {sha}", digest=cleaned)
    path = blob_path(traces_dir, cleaned)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ContentNotFoundError(
            f"content blob missing: {cleaned}",
            digest=cleaned,
        ) from exc
    actual = hashlib.sha256(data).hexdigest()
    if actual != cleaned:
        raise DigestMismatchError(cleaned, actual)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DigestMismatchError(
            cleaned,
            actual,
            message=f"content blob {cleaned} is not valid UTF-8",
        ) from exc


def load_retained(traces_dir: Path, inline: str, meta: dict[str, Any], prefix: str) -> str:
    """Full text for a field. Missing blobs fall back to the inline prefix.

    A hash mismatch is not hidden: ``DigestMismatchError`` propagates.
    """
    flag = f"{prefix}truncated" if prefix else "truncated"
    sha_key = f"{prefix}sha256" if prefix else "sha256"
    sha = str(meta.get(sha_key) or "")
    if meta.get(flag) and sha:
        try:
            return read_blob(traces_dir, sha)
        except ContentNotFoundError:
            return inline
    return inline


def _meta_key(prefix: str, name: str) -> str:
    return f"{prefix}{name}" if prefix else name


def spill_overflow(traces_dir: Path, event: TraceEvent) -> None:
    """Bound long strings on ``event`` and keep the full text in an artifact.

    Fields: ``metadata.content``, ``result``, ``error``, ``stack``.
    A field already marked truncated is only re-prefixed, not rewritten.
    Strings at or under the inline cap are left untouched.
    """
    meta = dict(event.metadata or {})
    changed = False

    def spill(prefix: str, text: str) -> str:
        nonlocal changed
        if len(text) <= INLINE_CONTENT_CHARS:
            return text
        flag = _meta_key(prefix, "truncated")
        sha_key = _meta_key(prefix, "sha256")
        if meta.get(flag) and meta.get(sha_key):
            changed = True
            return text[:INLINE_CONTENT_CHARS]
        sha = write_content(traces_dir, text)
        meta[sha_key] = sha
        meta[_meta_key(prefix, "digest")] = sha[:12]
        meta[_meta_key(prefix, "chars")] = len(text)
        meta[flag] = True
        changed = True
        return text[:INLINE_CONTENT_CHARS]

    content = meta.get("content")
    if isinstance(content, str):
        bounded = spill("", content)
        if bounded != content:
            meta["content"] = bounded
    if isinstance(event.result, str):
        event.result = spill("result_", event.result)
    if isinstance(event.error, str):
        event.error = spill("error_", event.error)
    if isinstance(event.stack, str):
        event.stack = spill("stack_", event.stack)
    if changed:
        event.metadata = meta


def _payload_from(meta: dict[str, Any], prefix: str, inline: str) -> _Payload:
    sha = str(meta.get(_meta_key(prefix, "sha256")) or "")
    digest = str(meta.get(_meta_key(prefix, "digest")) or "")
    return _Payload(
        inline=inline,
        sha256=sha.strip().lower(),
        digest=digest.strip().lower(),
        truncated=bool(meta.get(_meta_key(prefix, "truncated"))),
    )


def _iter_payloads(event: TraceEvent) -> Iterator[_Payload]:
    meta = event.metadata if isinstance(event.metadata, dict) else {}
    content = meta.get("content")
    if isinstance(content, str) and content != "":
        yield _payload_from(meta, "", content)
    elif isinstance(meta.get("prompt"), str) and meta.get("prompt") != "":
        yield _payload_from(meta, "", str(meta.get("prompt")))
    if isinstance(event.result, str) and event.result != "":
        yield _payload_from(meta, "result_", event.result)
    if isinstance(event.error, str) and event.error != "":
        yield _payload_from(meta, "error_", event.error)
    if isinstance(event.stack, str) and event.stack != "":
        yield _payload_from(meta, "stack_", event.stack)


def _clean_session(session: str) -> str:
    cleaned = (session or "").strip()
    if cleaned.startswith("trace:"):
        cleaned = cleaned[len("trace:") :].strip()
    return cleaned


def _addressed(payload: _Payload, query: str) -> bool:
    sha = payload.sha256
    digest = payload.digest
    if sha and (query == sha or (len(query) == 12 and sha.startswith(query))):
        return True
    if digest and query == digest:
        return True
    return False


def _resolve_payload(traces_dir: Path, payload: _Payload, query: str) -> Optional[tuple[str, bool]]:
    """Return ``(full_text, stored_truncated)`` or None. Raises on a bad hash."""
    if _addressed(payload, query):
        if payload.truncated and payload.sha256:
            text = read_blob(traces_dir, payload.sha256)
        else:
            text = payload.inline
        if not digest_matches(text, query):
            raise DigestMismatchError(query, content_sha256(text))
        return text, payload.truncated
    if payload.truncated:
        return None
    if digest_matches(payload.inline, query):
        return payload.inline, False
    return None


def _window(text: str, offset: int, limit: Optional[int]) -> tuple[str, bool]:
    if offset >= len(text):
        piece = ""
    elif limit is None:
        piece = text[offset:]
    else:
        piece = text[offset : offset + limit]
    truncated = offset != 0 or (limit is not None and offset + limit < len(text))
    return piece, truncated


def read_content(
    session: str,
    digest: str,
    *,
    offset: int = 0,
    limit: Optional[int] = None,
    store: Optional[Any] = None,
) -> ContentSlice:
    """Load one retained body for ``session`` and verify ``digest``.

    ``digest`` is the 12-character ref prefix or the full SHA-256.
    ``offset`` and ``limit`` are Unicode code-point indexes into the full
    text, applied only after the hash check. ``limit=None`` reads through
    the end. ``limit=0`` returns an empty window.

    The session must reference the body. A digest that does not match the
    stored bytes raises ``DigestMismatchError``.
    """
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")
    query = normalize_digest(digest)
    session_id = _clean_session(session)
    if not session_id:
        raise ContentNotFoundError(
            "session id is required",
            session=session,
            digest=query,
        )
    from .store import TraceStore

    target = store if store is not None else TraceStore()
    found: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for event in target.iter_events(session_id=session_id):
        for payload in _iter_payloads(event):
            resolved = _resolve_payload(target.traces_dir, payload, query)
            if resolved is None:
                continue
            text, stored_truncated = resolved
            if text in seen:
                continue
            seen.add(text)
            found.append((text, stored_truncated))
    if not found:
        raise ContentNotFoundError(
            f"no content for session {session_id!r} with digest {query}",
            session=session_id,
            digest=query,
        )
    if len(found) > 1:
        raise AmbiguousDigestError(query)
    text, stored_truncated = found[0]
    piece, truncated = _window(text, offset, limit)
    full = content_sha256(text)
    return ContentSlice(
        session=session_id,
        digest=full,
        digest_short=full[:12],
        text=piece,
        offset=offset,
        limit=limit,
        total_chars=len(text),
        truncated=truncated,
        stored_truncated=stored_truncated,
    )


def prune_unreferenced(traces_dir: Path) -> int:
    """Delete content artifacts no remaining trace file mentions.

    A false keep is preferred: any 64-hex string in a ``.jsonl`` or
    ``.jsonl.gz`` file counts as a reference. Partial ``*.tmp`` files go.
    """
    root = traces_dir / "content"
    if not root.is_dir():
        return 0
    referenced: set[str] = set()
    try:
        names = list(traces_dir.iterdir())
    except OSError:
        names = []
    for path in names:
        if not path.is_file():
            continue
        name = path.name
        try:
            if name.endswith(".jsonl"):
                blob = path.read_text(encoding="utf-8", errors="replace")
            elif name.endswith(".jsonl.gz"):
                with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
                    blob = handle.read()
            else:
                continue
        except OSError:
            continue
        referenced.update(_SHA_IN_TEXT.findall(blob))
    removed = 0
    files = [path for path in root.rglob("*") if path.is_file()]
    for path in files:
        if path.suffix == ".tmp" or path.name.startswith("."):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
            continue
        if _SHA_FILE.fullmatch(path.name) and path.name not in referenced:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    dirs = sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True)
    for path in dirs:
        try:
            path.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass
    return removed
