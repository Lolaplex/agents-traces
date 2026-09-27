"""
Audit, cryptographic sealing, tamper verification, and replay analysis for agents-traces.

Provides:
- SHA-256 hash chaining of tool calls (genesis 64 zero hex).
- Tamper detection / verification for sealed session logs.
- Replay analysis detecting non-determinism (divergence) and redundant calls.
- Permission overreach auditing against declared tool scopes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Union

from .models import TraceEvent

GENESIS = "0" * 64

# Standard mutators: tools whose execution can alter system or environment state.
# An identical read call after a mutator is NOT considered redundant.
DEFAULT_MUTATORS: FrozenSet[str] = frozenset({
    "write_file",
    "create_file",
    "delete_file",
    "move_file",
    "replace_file_content",
    "write_to_file",
    "edit_file",
    "execute_command",
    "run_command",
    "add_memory",
    "write_memory_file",
    "delete_memory",
    "write_doc",
    "delete_doc",
    "browser_click",
    "browser_type",
    "browser_fill_form",
    "browser_select",
})

NON_DETERMINISM = "non-determinism"
REDUNDANT_CALL = "redundant-call"
OVERREACH = "permission-overreach"

KIND_ORDER = {NON_DETERMINISM: 0, REDUNDANT_CALL: 1, OVERREACH: 2}


class AuditError(Exception):
    """Raised on invalid transcript or audit failure."""


@dataclass(frozen=True)
class Scope:
    """Declared permission scope for tool calls."""

    agent: str
    allowed_tools: FrozenSet[str]

    def permits(self, tool: str) -> bool:
        return tool in self.allowed_tools


def parse_scope_text(text: str) -> Scope:
    """Parse scope JSON text into a Scope object."""
    try:
        obj = json.loads(text)
    except Exception as exc:
        raise AuditError(f"invalid scope JSON: {exc}") from exc

    if isinstance(obj, list):
        return Scope(agent="anonymous", allowed_tools=frozenset(str(x) for x in obj))
    if not isinstance(obj, dict):
        raise AuditError("scope must be a JSON object or array")

    agent = str(obj.get("agent", "anonymous"))
    allowed = obj.get("allowed_tools")
    if not isinstance(allowed, list):
        raise AuditError("scope must have an array field 'allowed_tools'")

    return Scope(agent=agent, allowed_tools=frozenset(str(x) for x in allowed))


def load_scope(path_or_str: Union[str, Path]) -> Scope:
    """Load Scope from a file path or JSON string."""
    p = Path(path_or_str)
    if p.exists() and p.is_file():
        return parse_scope_text(p.read_text(encoding="utf-8", errors="replace"))
    return parse_scope_text(str(path_or_str))


@dataclass(frozen=True)
class AuditRecord:
    """Canonical tool call representation for hashing and auditing."""

    index: int
    tool: str
    args: Dict[str, Any]
    result: Any = None
    error: Optional[str] = None
    session: Optional[str] = None
    ts: Optional[str] = None
    legacy_response_key: bool = False

    def canonical_call(self) -> str:
        """Stable JSON string identifying tool + arguments."""
        return json.dumps(
            {"tool": self.tool, "args": self.args or {}},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )

    def canonical_response(self) -> str:
        """Stable JSON string representing tool output / error."""
        payload = {"error": self.error, "result": self.result}
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )


@dataclass(frozen=True)
class SealedLink:
    """One link in the SHA-256 hash chain."""

    index: int
    prev: str
    digest: str
    record: AuditRecord

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "index": self.index,
            "prev": self.prev,
            "digest": self.digest,
            "tool": self.record.tool,
            "args": self.record.args,
        }
        if self.record.legacy_response_key:
            d["response"] = self.record.result if self.record.result is not None else (self.record.error or {})
        else:
            d["result"] = self.record.result
            if self.record.error is not None:
                d["error"] = self.record.error
        if self.record.session is not None:
            d["session"] = self.record.session
        if self.record.ts is not None:
            d["ts"] = self.record.ts
        return d


@total_ordering
@dataclass(frozen=True)
class Finding:
    """Individual audit observation (non-determinism, redundant call, overreach)."""

    kind: str
    index: int
    tool: str
    detail: str

    def _key(self):
        return (self.index, KIND_ORDER.get(self.kind, 99), self.tool, self.detail)

    def __lt__(self, other):
        if not isinstance(other, Finding):
            return NotImplemented
        return self._key() < other._key()

    def format_line(self) -> str:
        return f"index {self.index:03d} | [{self.kind}] tool '{self.tool}': {self.detail}"


@dataclass(frozen=True)
class VerifyResult:
    """Verification outcome of a hash chain."""

    ok: bool
    total_links: int
    head_digest: Optional[str] = None
    broken_index: Optional[int] = None
    expected: Optional[str] = None
    found: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "total_links": self.total_links,
            "head_digest": self.head_digest,
            "broken_index": self.broken_index,
            "expected": self.expected,
            "found": self.found,
        }


@dataclass(frozen=True)
class AuditResult:
    """Summary of replay audit."""

    session: Optional[str]
    call_count: int
    findings: List[Finding]
    divergence_index: Optional[int] = None

    @property
    def non_deterministic_count(self) -> int:
        return sum(1 for f in self.findings if f.kind == NON_DETERMINISM)

    @property
    def redundant_count(self) -> int:
        return sum(1 for f in self.findings if f.kind == REDUNDANT_CALL)

    @property
    def overreach_count(self) -> int:
        return sum(1 for f in self.findings if f.kind == OVERREACH)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session": self.session,
            "call_count": self.call_count,
            "divergence_index": self.divergence_index,
            "non_deterministic_count": self.non_deterministic_count,
            "redundant_count": self.redundant_count,
            "overreach_count": self.overreach_count,
            "findings": [
                {
                    "kind": f.kind,
                    "index": f.index,
                    "tool": f.tool,
                    "detail": f.detail,
                }
                for f in self.findings
            ],
        }


def compute_digest(prev: str, record: AuditRecord, legacy_response_key: bool = False) -> str:
    """Compute deterministic SHA-256 digest over (prev + record fields)."""
    if legacy_response_key or record.legacy_response_key:
        # ToolReplay canonical schema compatibility
        payload = {
            "args": record.args or {},
            "index": record.index,
            "prev": prev,
            "response": record.result if record.result is not None else (record.error or {}),
            "tool": record.tool,
        }
    else:
        payload = {
            "args": record.args or {},
            "error": record.error,
            "index": record.index,
            "prev": prev,
            "result": record.result,
            "tool": record.tool,
        }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def events_to_records(events: Iterable[TraceEvent]) -> List[AuditRecord]:
    """Extract tool calls from TraceEvents into sequentially indexed AuditRecords."""
    tool_events: List[TraceEvent] = []
    for e in events:
        if e.tool is not None or e.type == "tool_call":
            tool_events.append(e)

    # Chronological sort
    tool_events.sort(key=lambda e: e.ts or "")

    records: List[AuditRecord] = []
    for idx, e in enumerate(tool_events):
        records.append(
            AuditRecord(
                index=idx,
                tool=e.tool or "unknown",
                args=e.args or {},
                result=e.result,
                error=e.error,
                session=e.session,
                ts=e.ts,
            )
        )
    return records


def parse_transcript_text(text: str) -> List[AuditRecord]:
    """Parse JSONL transcript text into AuditRecords (supports agents-traces and ToolReplay format)."""
    records: List[AuditRecord] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception as exc:
            raise AuditError(f"line {line_no}: invalid JSON: {exc}") from exc

        if not isinstance(obj, dict):
            raise AuditError(f"line {line_no}: expected JSON object")

        tool = obj.get("tool")
        if not tool:
            # Check if TraceEvent format
            if obj.get("type") != "tool_call":
                continue
            tool = obj.get("tool", "unknown")

        args = obj.get("args") or {}
        if not isinstance(args, dict):
            args = {"_raw": args}

        # Check response vs result
        legacy_key = "response" in obj and "result" not in obj
        result = obj.get("result") if "result" in obj else obj.get("response")
        error = obj.get("error")
        session = obj.get("session")
        ts = obj.get("ts")
        idx = obj.get("index")

        records.append(
            AuditRecord(
                index=len(records) if idx is None else int(idx),
                tool=str(tool),
                args=args,
                result=result,
                error=error,
                session=session,
                ts=ts,
                legacy_response_key=legacy_key,
            )
        )

    # Re-index if needed to ensure 0-based contiguous indices
    normalized: List[AuditRecord] = []
    for expected, rec in enumerate(records):
        if rec.index != expected:
            normalized.append(
                AuditRecord(
                    index=expected,
                    tool=rec.tool,
                    args=rec.args,
                    result=rec.result,
                    error=rec.error,
                    session=rec.session,
                    ts=rec.ts,
                    legacy_response_key=rec.legacy_response_key,
                )
            )
        else:
            normalized.append(rec)
    return normalized


def seal_records(records: Sequence[AuditRecord]) -> List[SealedLink]:
    """Seal AuditRecords into a cryptographic SHA-256 hash chain starting at GENESIS."""
    links: List[SealedLink] = []
    prev = GENESIS
    for record in records:
        digest = compute_digest(prev, record)
        links.append(SealedLink(index=record.index, prev=prev, digest=digest, record=record))
        prev = digest
    return links


def links_to_jsonl(links: Sequence[SealedLink]) -> str:
    """Serialize sealed links to deterministic JSONL."""
    lines = []
    for link in links:
        lines.append(
            json.dumps(link.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
        )
    return "\n".join(lines) + ("\n" if lines else "")


def load_sealed_jsonl(text_or_path: Union[str, Path]) -> List[SealedLink]:
    """Load sealed links from JSONL text or file path."""
    p = Path(text_or_path)
    if p.exists() and p.is_file():
        text = p.read_text(encoding="utf-8", errors="replace")
    else:
        text = str(text_or_path)

    links: List[SealedLink] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception as exc:
            raise AuditError(f"line {line_no}: invalid JSON: {exc}") from exc

        for req in ("index", "prev", "digest", "tool"):
            if req not in obj:
                raise AuditError(f"line {line_no}: missing required field '{req}'")

        legacy_key = "response" in obj and "result" not in obj
        result = obj.get("result") if "result" in obj else obj.get("response")

        record = AuditRecord(
            index=int(obj["index"]),
            tool=str(obj["tool"]),
            args=obj.get("args") or {},
            result=result,
            error=obj.get("error"),
            session=obj.get("session"),
            ts=obj.get("ts"),
            legacy_response_key=legacy_key,
        )
        links.append(
            SealedLink(
                index=int(obj["index"]),
                prev=str(obj["prev"]),
                digest=str(obj["digest"]),
                record=record,
            )
        )
    return links


def verify_chain(links: Sequence[SealedLink]) -> VerifyResult:
    """Verify cryptographic integrity of a sealed hash chain."""
    if not links:
        return VerifyResult(ok=True, total_links=0, head_digest=GENESIS)

    prev = GENESIS
    for link in links:
        if link.prev != prev:
            return VerifyResult(
                ok=False,
                total_links=len(links),
                broken_index=link.index,
                expected=prev,
                found=link.prev,
            )

        # Try both formats (native result vs legacy response key)
        recomputed = compute_digest(prev, link.record, legacy_response_key=link.record.legacy_response_key)
        if recomputed != link.digest:
            alt = compute_digest(prev, link.record, legacy_response_key=not link.record.legacy_response_key)
            if alt == link.digest:
                recomputed = alt

        if recomputed != link.digest:
            return VerifyResult(
                ok=False,
                total_links=len(links),
                broken_index=link.index,
                expected=recomputed,
                found=link.digest,
            )
        prev = link.digest

    return VerifyResult(ok=True, total_links=len(links), head_digest=prev)


def audit_replay(
    records: Sequence[AuditRecord],
    mutators: FrozenSet[str] = DEFAULT_MUTATORS,
    allowed_tools: Optional[Union[Scope, Iterable[str]]] = None,
    session: Optional[str] = None,
) -> AuditResult:
    """
    Audit records for:
    1. Permission overreach (if allowed_tools provided).
    2. Non-determinism (identical tool call yielding different response).
    3. Redundant calls (repeated identical call with no intervening mutator).
    """
    findings: List[Finding] = []
    divergence_index: Optional[int] = None
    allowed_set: Optional[FrozenSet[str]] = None
    if isinstance(allowed_tools, Scope):
        allowed_set = allowed_tools.allowed_tools
    elif allowed_tools is not None:
        allowed_set = frozenset(allowed_tools)

    # Overreach detection
    if allowed_set is not None:
        for rec in records:
            if rec.tool not in allowed_set:
                findings.append(
                    Finding(
                        kind=OVERREACH,
                        index=rec.index,
                        tool=rec.tool,
                        detail=f"tool '{rec.tool}' is not in allowed scope",
                    )
                )

    # Replay analysis
    seen_response: Dict[str, str] = {}
    last_seen_index: Dict[str, int] = {}

    for pos, record in enumerate(records):
        call_key = record.canonical_call()
        resp_key = record.canonical_response()

        if call_key in seen_response:
            if seen_response[call_key] != resp_key:
                # Non-determinism detected
                findings.append(
                    Finding(
                        kind=NON_DETERMINISM,
                        index=record.index,
                        tool=record.tool,
                        detail=(
                            f"returned different response than identical call at index {last_seen_index[call_key]}"
                        ),
                    )
                )
                if divergence_index is None:
                    divergence_index = record.index
            else:
                # Identical response: check if any mutator intervened
                prev_pos: Optional[int] = None
                for back in range(pos - 1, -1, -1):
                    if records[back].canonical_call() == call_key:
                        prev_pos = back
                        break

                if prev_pos is not None:
                    mutated = False
                    for between in range(prev_pos + 1, pos):
                        mid = records[between]
                        if mid.tool in mutators or mid.canonical_call() != call_key:
                            mutated = True
                            break
                    if not mutated:
                        findings.append(
                            Finding(
                                kind=REDUNDANT_CALL,
                                index=record.index,
                                tool=record.tool,
                                detail=(
                                    f"repeats identical call from index {records[prev_pos].index} with no state change between them"
                                ),
                            )
                        )
        else:
            seen_response[call_key] = resp_key

        last_seen_index[call_key] = record.index

    sorted_findings = sorted(findings)
    target_session = session or (records[0].session if records else None)

    return AuditResult(
        session=target_session,
        call_count=len(records),
        findings=sorted_findings,
        divergence_index=divergence_index,
    )


def format_audit_text(result: AuditResult) -> str:
    """Format audit results for terminal display."""
    lines = []
    lines.append("\033[1;36m=== Trace Replay & Audit Report ===\033[0m")
    lines.append(f"Session:     {result.session or 'n/a'}")
    lines.append(f"Total Calls: {result.call_count}")
    
    if result.divergence_index is not None:
        lines.append(f"Divergence:  \033[31mIndex {result.divergence_index}\033[0m (environment non-determinism)")
    else:
        lines.append("Divergence:  \033[32mNone (deterministic replay)\033[0m")

    status_parts = []
    if result.non_deterministic_count > 0:
        status_parts.append(f"\033[31m{result.non_deterministic_count} non-deterministic\033[0m")
    if result.redundant_count > 0:
        status_parts.append(f"\033[33m{result.redundant_count} redundant\033[0m")
    if result.overreach_count > 0:
        status_parts.append(f"\033[35m{result.overreach_count} scope overreach\033[0m")

    if not status_parts:
        lines.append("Integrity:   \033[32mClean (0 issues detected)\033[0m")
    else:
        lines.append(f"Issues:      {', '.join(status_parts)}")

    if result.findings:
        lines.append("\n\033[1mFindings:\033[0m")
        for f in result.findings:
            if f.kind == NON_DETERMINISM:
                lines.append(f"  \033[31m•\033[0m {f.format_line()}")
            elif f.kind == REDUNDANT_CALL:
                lines.append(f"  \033[33m•\033[0m {f.format_line()}")
            else:
                lines.append(f"  \033[35m•\033[0m {f.format_line()}")
    lines.append("")
    return "\n".join(lines)


def format_verify_text(result: VerifyResult) -> str:
    """Format chain verification result for terminal display."""
    lines = []
    lines.append("\033[1;36m=== SHA-256 Hash Chain Verification ===\033[0m")
    lines.append(f"Total Links: {result.total_links}")
    if result.ok:
        lines.append(f"Status:      \033[32mVERIFIED OK (Tamper-evident chain valid)\033[0m")
        if result.head_digest:
            lines.append(f"Head Digest: {result.head_digest[:16]}...{result.head_digest[-16:]}")
    else:
        lines.append(f"Status:      \033[31mCORRUPTED / TAMPERED at index {result.broken_index}\033[0m")
        lines.append(f"Expected:    {result.expected}")
        lines.append(f"Found:       {result.found}")
    lines.append("")
    return "\n".join(lines)
