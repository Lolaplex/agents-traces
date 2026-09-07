from datetime import datetime, timezone

from agents_traces.prompt import PromptParts, clock_message, render_system_prompt, stamp_content


def test_system_prompt_has_sections_and_start_date_not_clock():
    xml = render_system_prompt(
        PromptParts(
            role="agent",
            workflow="do the work",
            guardrails="no invention",
            tool_rules="call when needed",
            skills=[("mcp.traces", "assemble a session")],
            user_id="fabian",
            user_display="Fabian",
            work="homelab",
            project="agents-harness",
            timezone="Europe/Berlin",
            aliases=["telegram:1", "http:fabian"],
            session_id="ses_abc",
            start_date="2026-08-31",
        )
    )
    assert xml.startswith("<system_prompt>")
    assert "<instructions>" in xml
    assert "<role>agent</role>" in xml
    assert 'name="mcp.traces"' in xml
    assert 'start_date="2026-08-31"' in xml
    assert "<clock" not in xml
    assert "homelab" in xml


def test_clock_is_a_separate_message():
    msg = clock_message()
    assert msg["role"] == "system"
    assert msg["content"].startswith("<clock")


def test_clock_converts_berlin_with_weekday():
    utc = datetime(2026, 9, 7, 15, 20, tzinfo=timezone.utc)
    msg = clock_message(utc, timezone_name="Europe/Berlin")
    assert msg["role"] == "system"
    assert 'timezone="Europe/Berlin"' in msg["content"]
    assert "Monday" in msg["content"]
    assert "17:20:00" in msg["content"]
    assert "+02:00" in msg["content"]
    assert "+00:00" not in msg["content"]


def test_clock_converts_tokyo_crosses_date():
    utc = datetime(2026, 9, 7, 15, 20, tzinfo=timezone.utc)
    msg = clock_message(utc, timezone_name="Asia/Tokyo")
    assert 'timezone="Asia/Tokyo"' in msg["content"]
    assert "Tuesday" in msg["content"]
    assert "00:20:00" in msg["content"]
    assert "+09:00" in msg["content"]


def test_clock_invalid_tz_falls_back_utc():
    utc = datetime(2026, 9, 7, 15, 20, tzinfo=timezone.utc)
    msg = clock_message(utc, timezone_name="Not/AZone")
    assert 'timezone="UTC"' in msg["content"]
    assert "Monday" in msg["content"]
    assert "15:20:00" in msg["content"]


def test_stamp_is_idempotent():
    once = stamp_content("2026-08-31T00:00:00Z", "hi")
    assert once.startswith("<ts>2026-08-31T00:00:00Z</ts>")
    assert stamp_content("2026-08-31T01:00:00Z", once) == once


def test_preview_is_not_a_summary():
    from agents_traces.prompt import content_digest, preview_text

    body = "x" * 2000
    shown = preview_text(body, session="ses_a", ts="2026-08-31T00:00:00Z")
    assert len(shown) < len(body)
    assert shown.startswith("x" * 10)
    assert "<ref " in shown
    assert content_digest(body) in shown
    assert "summary" not in shown.lower()
    # Second projection is byte-identical (frozen digest, not a new paraphrase).
    assert preview_text(body, session="ses_a", ts="2026-08-31T00:00:00Z") == shown


def test_empty_prompt_does_not_inject_english_persona():
    xml = render_system_prompt(PromptParts(session_id="ses_x"))
    assert "You are a personal agent" not in xml
    assert "Talk and run" not in xml


def test_custom_instructions_blob_does_not_inject_english_defaults():
    xml = render_system_prompt(PromptParts(instructions="Du bist ein Agent.", session_id="ses_x"))
    assert "Du bist ein Agent." in xml
    assert "You are a personal agent" not in xml


def test_collapse_evicts_old_large_stdio_not_user_text():
    from agents_traces.prompt import collapse_old_stdio, content_digest, preview_text

    blob = "cargo test\n" + ("x" * 800)
    preview = preview_text(blob)
    msgs = [
        {"role": "user", "content": "run tests"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": preview},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c2"}, {"id": "c3"}]},
        {"role": "tool", "tool_call_id": "c2", "content": "exit 1"},
        {"role": "tool", "tool_call_id": "c3", "content": preview_text("ok\n" + ("y" * 800))},
    ]
    out = collapse_old_stdio(msgs)
    assert out[0]["content"] == "run tests"
    assert out[1]["tool_calls"][0]["id"] == "c1"
    assert out[2]["content"].startswith("<stdio")
    assert "x" * 800 not in out[2]["content"]
    assert content_digest(blob) in out[2]["content"]
    assert out[4]["content"] == "exit 1"
    assert "yyyy" in out[5]["content"]
    again = collapse_old_stdio(out)
    assert again[2]["content"] == out[2]["content"]


def test_collapse_keeps_small_old_stdio():
    from agents_traces.prompt import collapse_old_stdio

    msgs = [
        {"role": "tool", "content": "ok"},
        {"role": "assistant", "content": "next"},
        {"role": "tool", "content": "still going"},
    ]
    out = collapse_old_stdio(msgs)
    assert out[0]["content"] == "ok"
