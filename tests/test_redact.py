"""Secrets are removed before any trace byte is written."""

import json
from pathlib import Path

import pytest

from agents_traces.assemble import record_message
from agents_traces.content import INLINE_CONTENT_CHARS, content_sha256, read_content
from agents_traces.models import TraceEvent
from agents_traces.redact import REDACTED, Redactor, configure_redaction
from agents_traces.store import TraceStore

OPENAI = "sk-proj-ab12cd34ef56gh78zz90"
OPENAI_BODY = "ab12cd34ef56gh78zz90"
GITHUB = "ghp_" + ("A" * 32) + "91zQ"
AWS = "AKIA" + ("0" * 16)
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.YWJjMTIz"
SLACK = "xoxb-123456789012-abcdefghij"
WEBHOOK = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXX"
PEM_BODY = "MIIBdummy1234567890keymaterial"
PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    f"{PEM_BODY}\n"
    "-----END PRIVATE KEY-----\n"
)
URL_PASSWORD = "s3cret-pass"
URL = f"https://alice:{URL_PASSWORD}@example.com/db"
BEARER = "a" * 16
ENV_VALUE = "supersecretvalue1"


def disk_text(root: Path) -> str:
    chunks: list[str] = []
    for path in sorted(root.rglob("*")):
        chunks.append(str(path.relative_to(root)))
        if path.is_file():
            chunks.append(path.read_bytes().decode("utf-8", "replace"))
    return "\n".join(chunks)


def test_builtin_secrets_never_hit_disk(tmp_path: Path):
    prose = "keep-this-sentence"
    args = {
        "cmd": f"curl -H 'Authorization: Bearer {BEARER}' {URL}",
        "api_key": "example",
        "token": ENV_VALUE,
        "headers": {"Authorization": f"Bearer {BEARER}"},
        "password": 99112233,
        "note": prose,
    }
    event = TraceEvent(
        session="ses_secret",
        type="tool_call",
        tool="shell",
        args=args,
        result=f"github {GITHUB} slack {SLACK}",
        error=f"aws {AWS} webhook {WEBHOOK}",
        stack=PEM,
        metadata={
            "content": f"jwt {JWT} key {OPENAI}",
            "role": "user",
            "digest": "placeholder",
            "sha256": "placeholder",
            "chars": 1,
            "env": f"export OPENAI_API_KEY={ENV_VALUE}",
            "quoted": '{"password": "hunter2", "ok": "yes"}',
            "yaml": "password: hunter2 now",
        },
    )
    original_args = json.loads(json.dumps(args))
    store = TraceStore(tmp_path)
    store.append(event)

    assert args == original_args
    assert args["api_key"] == "example"

    raw = disk_text(tmp_path)
    for secret in (
        OPENAI,
        OPENAI_BODY,
        GITHUB,
        AWS,
        JWT,
        SLACK,
        WEBHOOK.split("/")[-1],
        PEM_BODY,
        "BEGIN PRIVATE KEY",
        URL_PASSWORD,
        BEARER,
        ENV_VALUE,
        "hunter2",
        "99112233",
    ):
        assert secret not in raw, secret
    assert prose in raw
    assert "yes" in raw
    assert "alice" in raw
    assert "example.com" in raw
    assert REDACTED in raw
    assert "OPENAI_API_KEY" in raw
    assert event.metadata["sha256"] == content_sha256(event.metadata["content"])
    assert OPENAI not in event.metadata["content"]


def test_false_positives_stay(tmp_path: Path):
    samples = [
        "token count is 12",
        "MAX_TOKENS=4096",
        "TOKEN_COUNT=3",
        "http://localhost:8080/health",
        "src/tokens.py",
        "sk-implementation-details",
        "sk-short",
        "password policy: use a manager",
        "the password is hunter2",
        "Bearer of bad news",
        "Basic configuration for the server",
        "Authorization is required for this route",
        "authorization: enabled",
        "api_key=example",
        "eyJ",
        "2026-11-14T20:00:00Z",
        "a" * 64,
        "widget-123456",
    ]
    redactor = Redactor(use_env=False)
    for sample in samples:
        assert redactor.redact_text(sample) == sample, sample

    store = TraceStore(tmp_path, redactor=redactor)
    body = "\n".join(samples)
    record_message("ses_plain", "user", body, store=store)
    raw = disk_text(tmp_path)
    for sample in samples:
        assert sample in raw


def test_explicit_short_secrets_and_env_names(tmp_path: Path):
    redactor = Redactor(use_env=False)
    assert redactor.redact_text('{"api_key": "example"}') == '{"api_key": "[REDACTED]"}'
    assert redactor.redact_text("pwd=abc") == "pwd=[REDACTED]"
    assert redactor.redact_text("GITHUB_TOKEN=abc") == "GITHUB_TOKEN=[REDACTED]"
    assert redactor.redact_text('OPENAI_API_KEY="supersecretvalue1"') == (
        'OPENAI_API_KEY="[REDACTED]"'
    )
    assert "hunter2" not in redactor.redact_text("note: password: hunter2")
    assert redactor.redact_text("password: use a manager") == "password: [REDACTED] a manager"
    assert redactor.redact_text("api_key=examplekey12") == "api_key=[REDACTED]"

    store = TraceStore(tmp_path, redactor=redactor)
    store.append(
        TraceEvent(
            session="ses_env",
            type="custom",
            metadata={"GITHUB_TOKEN": "abc", "MAX_TOKENS": 4096, "kind": "token"},
        )
    )
    raw = disk_text(tmp_path)
    assert "abc" not in raw
    assert "4096" in raw
    assert "token" in raw


def test_redaction_is_idempotent_and_updates_digest(tmp_path: Path):
    redactor = Redactor(use_env=False)
    once = redactor.redact_text(f"hello {OPENAI} end")
    assert redactor.redact_text(once) == once
    assert once == f"hello {REDACTED} end"

    store = TraceStore(tmp_path, redactor=redactor)
    event = record_message("ses_hash", "user", f"hello {OPENAI} end", store=store)
    assert event.metadata["content"] == once
    assert event.metadata["sha256"] == content_sha256(once)
    assert event.metadata["digest"] == content_sha256(once)[:12]
    assert event.metadata["chars"] == len(once)
    piece = read_content("ses_hash", event.metadata["digest"], store=store)
    assert piece.text == once
    assert OPENAI not in disk_text(tmp_path)


def test_spilled_tail_is_redacted(tmp_path: Path):
    secret = OPENAI
    body = ("n" * INLINE_CONTENT_CHARS) + " " + secret + " " + ("TAIL" * 80)
    assert len(body) > INLINE_CONTENT_CHARS
    store = TraceStore(tmp_path)
    event = record_message("ses_spill", "user", body, store=store)
    blobs = [p for p in (tmp_path / "content").rglob("*") if p.is_file()]
    assert blobs
    raw = disk_text(tmp_path)
    assert secret not in raw
    assert OPENAI_BODY not in raw
    assert "TAIL" in raw
    piece = read_content("ses_spill", event.metadata["digest"], store=store)
    assert secret not in piece.text
    assert "TAIL" in piece.text
    assert piece.stored_truncated is True


def test_off_switches_allow_the_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    secret = OPENAI
    off = tmp_path / "off"
    store = TraceStore(off, redact=False)
    record_message("ses_off", "user", f"see {secret}", store=store)
    assert secret in disk_text(off)

    monkeypatch.setenv("AGENTS_TRACE_REDACT", "0")
    env_off = tmp_path / "env"
    store = TraceStore(env_off)
    record_message("ses_env_off", "user", f"see {secret}", store=store)
    assert secret in disk_text(env_off)

    forced = tmp_path / "forced"
    store = TraceStore(forced, redact=True)
    record_message("ses_forced", "user", f"see {secret}", store=store)
    assert secret not in disk_text(forced)


def test_extra_patterns_are_store_local_and_configurable(tmp_path: Path):
    pattern = r"widget-\d{6}"
    local = tmp_path / "local"
    other = tmp_path / "other"
    marked = TraceStore(local, extra_patterns=[pattern])
    plain = TraceStore(other)
    record_message("ses_w", "user", "id widget-123456 tail", store=marked)
    record_message("ses_w", "user", "id widget-123456 tail", store=plain)
    assert "widget-123456" not in disk_text(local)
    assert "tail" in disk_text(local)
    assert "widget-123456" in disk_text(other)

    configure_redaction(extra_patterns=[pattern])
    try:
        shared = tmp_path / "shared"
        store = TraceStore(shared)
        record_message("ses_c", "user", "id widget-654321 tail", store=store)
        assert "widget-654321" not in disk_text(shared)
        assert "tail" in disk_text(shared)
    finally:
        configure_redaction(extra_patterns=[], follow_env=True)


def test_env_extra_pattern_and_invalid_pattern(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENTS_TRACE_REDACT_EXTRA", json.dumps([r"widget-\d{6}"]))
    store = TraceStore(tmp_path)
    record_message("ses_e", "user", "id widget-123456 tail", store=store)
    raw = disk_text(tmp_path)
    assert "widget-123456" not in raw
    assert "tail" in raw

    with pytest.raises(ValueError):
        TraceStore(tmp_path / "bad", extra_patterns=["("])

    monkeypatch.setenv("AGENTS_TRACE_REDACT_EXTRA", "not-json")
    broken = TraceStore(tmp_path / "broken")
    with pytest.raises(ValueError):
        broken.append(TraceEvent(session="ses_bad", type="custom", metadata={"note": "x"}))
    assert not list((tmp_path / "broken").glob("*.jsonl"))


def test_nested_token_field(tmp_path: Path):
    store = TraceStore(tmp_path)
    store.append(
        TraceEvent(
            session="ses_nest",
            type="tool_call",
            tool="fetch",
            args={"auth": {"token": "nested-secret-value", "scheme": "bearer"}},
        )
    )
    raw = disk_text(tmp_path)
    assert "nested-secret-value" not in raw
    assert "bearer" in raw
    assert "fetch" in raw
