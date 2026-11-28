"""W12A performance & correctness tests for pre-filtered JSONL trace scans."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agents_traces.assemble import assemble_messages
from agents_traces.identity import IdentityStore
from agents_traces.models import TraceEvent
from agents_traces.store import TraceStore


class TestW12AFastTraceScans(unittest.TestCase):
    def test_prefilter_handles_unicode_and_special_session_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TraceStore(traces_dir=Path(tmp))
            sid = "ses_üñîçødë_42"
            f = Path(tmp) / "2026-11-28.jsonl"
            # Write both ensure_ascii=True and ensure_ascii=False lines
            ev1 = TraceEvent(session=sid, type="message", metadata={"role": "user", "content": "one"})
            ev2 = TraceEvent(session=sid, type="message", metadata={"role": "assistant", "content": "two"})
            other = TraceEvent(session="ses_other", type="message", metadata={"role": "user", "content": "skip"})
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(ev1.to_dict(), ensure_ascii=True) + "\n")
                fh.write(json.dumps(other.to_dict(), ensure_ascii=False) + "\n")
                fh.write(json.dumps(ev2.to_dict(), ensure_ascii=False) + "\n")

            got = store.get_events_for_session(sid)
            self.assertEqual(len(got), 2)
            self.assertEqual([g.metadata["content"] for g in got], ["one", "two"])

    def test_include_prev_session_false_skips_rollover_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TraceStore(traces_dir=Path(tmp))
            store.append(TraceEvent(session="ses_prev", type="message", metadata={"role": "user", "content": "old"}))
            store.append(TraceEvent(session="ses_cur", type="rollover", metadata={"prev_session": "ses_prev"}))
            store.append(TraceEvent(session="ses_cur", type="message", metadata={"role": "user", "content": "new"}))

            with patch.dict(os.environ, {"AGENTS_TRACES_DIR": tmp}, clear=False):
                msgs_without = assemble_messages("ses_cur", include_prev_session=False)
                msgs_with = assemble_messages("ses_cur", include_prev_session=True)
            self.assertEqual(len(msgs_without), 1)
            self.assertIn("new", msgs_without[0]["content"])
            self.assertEqual(len(msgs_with), 2)

    def test_10k_lines_session_and_assemble_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp) / "traces"
            tdir.mkdir(parents=True)
            # 10,000 filler trace lines across 5 daily files
            for day in range(20, 25):
                p = tdir / f"2026-11-{day:02d}.jsonl"
                with open(p, "w", encoding="utf-8") as fh:
                    for i in range(2000):
                        fh.write(
                            f'{{"ts":"2026-11-{day:02d}T10:00:00Z","session":"ses_fill_{day}_{i}","type":"tool_call","tool":"mcp.terminal.read","status":"ok","metadata":{{"bytes":128}}}}\n'
                        )
            target = tdir / "2026-11-28.jsonl"
            with open(target, "w", encoding="utf-8") as fh:
                fh.write('{"ts":"2026-11-28T10:00:01Z","session":"ses_hot","type":"message","metadata":{"role":"user","content":"Hi"}}\n')
                fh.write('{"ts":"2026-11-28T10:00:02Z","session":"ses_hot","type":"message","metadata":{"role":"assistant","content":"Hello"}}\n')

            store = TraceStore(traces_dir=tdir)
            t0 = time.perf_counter()
            evs = store.get_events_for_session("ses_hot")
            msgs = assemble_messages("ses_hot", store=store)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            self.assertEqual(len(evs), 2)
            self.assertEqual(len(msgs), 2)
            self.assertLess(elapsed_ms, 250.0, f"expected < 250ms on 10k lines, got {elapsed_ms:.1f}ms")

    def test_identity_resolve_skips_trace_scan_when_session_known(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"AGENTS_HOST_DIR": tmp}, clear=False):
                ident = IdentityStore()
                first = ident.resolve(channel="web", user="u1", session="ses_known_1")
                with patch.object(IdentityStore, "_user_id_from_traces", side_effect=AssertionError("should not scan traces")):
                    second = ident.resolve(channel="web", user="u1", session="ses_known_1")
                self.assertEqual(first.user.id, second.user.id)
                self.assertEqual(second.session.id, "ses_known_1")


if __name__ == "__main__":
    unittest.main()
