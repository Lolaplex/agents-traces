import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
import pytest

from agents_traces.__main__ import main
from agents_traces.audit import (
    GENESIS,
    AuditError,
    AuditRecord,
    Finding,
    NON_DETERMINISM,
    OVERREACH,
    REDUNDANT_CALL,
    Scope,
    SealedLink,
    audit_replay,
    compute_digest,
    events_to_records,
    links_to_jsonl,
    load_scope,
    load_sealed_jsonl,
    parse_scope_text,
    parse_transcript_text,
    seal_records,
    verify_chain,
)
from agents_traces.models import TraceEvent
from agents_traces.store import TraceStore
import agents_traces.mcp_server as mcp_mod


SAMPLE_TRANSCRIPT = (
    '{"index": 0, "tool": "list_dir", "args": {"path": "docs"}, "response": {"n": 2}}\n'
    '{"index": 1, "tool": "read_file", "args": {"path": "a"}, "response": {"bytes": 1}}\n'
)


def test_genesis_prev_is_zeroes():
    records = parse_transcript_text(SAMPLE_TRANSCRIPT)
    links = seal_records(records)
    assert len(links) == 2
    assert links[0].prev == GENESIS
    assert links[0].index == 0


def test_chain_links_reference_previous_digest():
    records = parse_transcript_text(SAMPLE_TRANSCRIPT)
    links = seal_records(records)
    assert links[1].prev == links[0].digest
    assert len(links[0].digest) == 64
    assert len(links[1].digest) == 64


def test_digest_is_deterministic():
    r1 = parse_transcript_text(SAMPLE_TRANSCRIPT)
    r2 = parse_transcript_text(SAMPLE_TRANSCRIPT)
    l1 = seal_records(r1)
    l2 = seal_records(r2)
    assert [x.digest for x in l1] == [x.digest for x in l2]


def test_verify_intact_chain():
    records = parse_transcript_text(SAMPLE_TRANSCRIPT)
    links = seal_records(records)
    res = verify_chain(links)
    assert res.ok is True
    assert res.total_links == 2
    assert res.broken_index is None
    assert res.head_digest == links[-1].digest


def test_verify_detects_tampered_result():
    records = parse_transcript_text(SAMPLE_TRANSCRIPT)
    links = seal_records(records)
    tampered_record = AuditRecord(
        index=1,
        tool="read_file",
        args={"path": "a"},
        result={"bytes": 999},
    )
    tampered_links = [
        links[0],
        SealedLink(
            index=1,
            prev=links[1].prev,
            digest=links[1].digest,
            record=tampered_record,
        ),
    ]
    res = verify_chain(tampered_links)
    assert res.ok is False
    assert res.broken_index == 1


def test_verify_detects_broken_prev():
    records = parse_transcript_text(SAMPLE_TRANSCRIPT)
    links = seal_records(records)
    tampered_links = [
        links[0],
        SealedLink(
            index=1,
            prev=GENESIS,
            digest=links[1].digest,
            record=links[1].record,
        ),
    ]
    res = verify_chain(tampered_links)
    assert res.ok is False
    assert res.broken_index == 1


def test_roundtrip_serialisation():
    records = parse_transcript_text(SAMPLE_TRANSCRIPT)
    links = seal_records(records)
    jsonl_str = links_to_jsonl(links)
    assert jsonl_str.endswith("\n")

    with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as tmp:
        tmp.write(jsonl_str)
        tmp_path = tmp.name

    try:
        reloaded = load_sealed_jsonl(tmp_path)
        assert len(reloaded) == len(links)
        res = verify_chain(reloaded)
        assert res.ok is True
        assert res.head_digest == links[-1].digest
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_replay_clean_session():
    text = (
        '{"index": 0, "tool": "list_dir", "args": {"p": "a"}, "result": {"n": 2}}\n'
        '{"index": 1, "tool": "read_file", "args": {"p": "b"}, "result": {"b": 1}}\n'
        '{"index": 2, "tool": "search", "args": {"q": "x"}, "result": {"h": 3}}\n'
    )
    records = parse_transcript_text(text)
    res = audit_replay(records)
    assert res.findings == []
    assert res.divergence_index is None
    assert res.call_count == 3


def test_replay_non_determinism_detected():
    text = (
        '{"index": 0, "tool": "search", "args": {"q": "x"}, "result": {"h": 3}}\n'
        '{"index": 1, "tool": "search", "args": {"q": "x"}, "result": {"h": 9}}\n'
    )
    records = parse_transcript_text(text)
    res = audit_replay(records)
    assert res.divergence_index == 1
    assert res.non_deterministic_count == 1
    assert any(f.kind == NON_DETERMINISM for f in res.findings)


def test_replay_redundant_call_detected():
    text = (
        '{"index": 0, "tool": "read_file", "args": {"p": "a"}, "result": {"b": 1}}\n'
        '{"index": 1, "tool": "read_file", "args": {"p": "a"}, "result": {"b": 1}}\n'
    )
    records = parse_transcript_text(text)
    res = audit_replay(records)
    assert res.redundant_count == 1
    assert res.divergence_index is None
    assert any(f.kind == REDUNDANT_CALL for f in res.findings)


def test_replay_mutation_between_reads_not_redundant():
    text = (
        '{"index": 0, "tool": "read_file", "args": {"p": "a"}, "result": {"b": 1}}\n'
        '{"index": 1, "tool": "write_file", "args": {"p": "a"}, "result": {"ok": true}}\n'
        '{"index": 2, "tool": "read_file", "args": {"p": "a"}, "result": {"b": 1}}\n'
    )
    records = parse_transcript_text(text)
    res = audit_replay(records)
    assert res.redundant_count == 0


def test_scope_and_overreach():
    scope_json = '{"agent": "reviewer", "allowed_tools": ["read_file", "search"]}'
    scope = parse_scope_text(scope_json)
    assert scope.permits("read_file") is True
    assert scope.permits("write_file") is False

    text = (
        '{"index": 0, "tool": "read_file", "args": {"p": "a"}, "result": {"b": 1}}\n'
        '{"index": 1, "tool": "write_file", "args": {"p": "a"}, "result": {"ok": true}}\n'
    )
    records = parse_transcript_text(text)
    res = audit_replay(records, allowed_tools=scope)
    assert res.overreach_count == 1
    assert any(f.kind == OVERREACH and f.index == 1 for f in res.findings)


def test_cli_seal_verify_audit(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("AGENTS_TRACES_DIR", tmpdir)

        # 1. Record events into store
        store = TraceStore(traces_dir=tmpdir)
        store.append(
            TraceEvent(
                session="s-audit-test",
                type="tool_call",
                tool="read_file",
                args={"path": "main.py"},
                result="print('hello')",
                status="ok",
            )
        )
        store.append(
            TraceEvent(
                session="s-audit-test",
                type="tool_call",
                tool="read_file",
                args={"path": "main.py"},
                result="print('hello')",
                status="ok",
            )
        )

        sealed_file = str(Path(tmpdir) / "sealed" / "s-audit-test.sealed.jsonl")

        # 2. Seal via CLI
        monkeypatch.setattr(
            "sys.argv",
            ["agents-traces", "seal", "--session", "s-audit-test", "-o", sealed_file],
        )
        f = io.StringIO()
        with redirect_stdout(f):
            rc = main()
        assert rc == 0
        assert "Sealed 2 tool calls" in f.getvalue()

        # 3. Verify via CLI
        monkeypatch.setattr("sys.argv", ["agents-traces", "verify", sealed_file])
        f = io.StringIO()
        with redirect_stdout(f):
            rc = main()
        assert rc == 0
        assert "VERIFIED OK" in f.getvalue()

        # 4. Audit via CLI
        monkeypatch.setattr(
            "sys.argv", ["agents-traces", "audit", "--session", "s-audit-test"]
        )
        f = io.StringIO()
        with redirect_stdout(f):
            rc = main()
        assert rc == 0
        out = f.getvalue()
        assert "redundant" in out


def test_mcp_seal_verify_audit(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_store = TraceStore(traces_dir=tmpdir)
        monkeypatch.setattr(mcp_mod, "store", fake_store)

        fake_store.append(
            TraceEvent(
                session="s-mcp-audit",
                type="tool_call",
                tool="search_docs",
                args={"query": "fastmcp"},
                result={"hits": 2},
                status="ok",
            )
        )

        # Test trace_seal
        sealed_path = str(Path(tmpdir) / "sealed" / "sealed.jsonl")
        seal_res_str = mcp_mod.trace_seal(
            session_id="s-mcp-audit", output_path=sealed_path
        )
        seal_data = json.loads(seal_res_str)
        assert seal_data["status"] == "sealed"
        assert seal_data["total_links"] == 1
        assert "head_digest" in seal_data

        # Test trace_verify
        verify_res_str = mcp_mod.trace_verify(sealed_path)
        verify_data = json.loads(verify_res_str)
        assert verify_data["ok"] is True
        assert verify_data["total_links"] == 1

        # Test trace_audit
        audit_res_str = mcp_mod.trace_audit(session_id="s-mcp-audit")
        audit_data = json.loads(audit_res_str)
        assert audit_data["call_count"] == 1
        assert audit_data["divergence_index"] is None
        assert audit_data["findings"] == []
