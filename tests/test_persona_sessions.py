"""C201: SessionRecord keyed by workspace+user+persona, stored under the persona."""
import os
import tempfile
import unittest
from pathlib import Path

from agents_workspace import Host

from agents_traces.identity import (
    IdentityStore,
    SessionStore,
    mint_session_id,
)


class PersonaSessions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.host = Host(dir=Path(self._tmp.name) / "host")
        self.ws = self.host.create("demo")

    def tearDown(self):
        self._tmp.cleanup()

    def test_same_user_two_personas_two_sessions(self):
        store = IdentityStore(workspace=self.ws)
        a = store.resolve(channel="repl", user="me", persona="cos",
                          new_session=True)
        b = store.resolve(channel="repl", user="me", persona="ops",
                          new_session=True)
        self.assertNotEqual(a.session.id, b.session.id)
        self.assertEqual(a.user.id, b.user.id)
        self.assertEqual(a.session.persona, "cos")
        self.assertEqual(b.session.persona, "ops")
        self.assertEqual(a.session.workspace, self.ws.id)
        # storage under the persona dir
        self.assertTrue((self.ws.personas / "cos" / "sessions"
                         / f"{a.session.id}.json").is_file())
        self.assertTrue((self.ws.personas / "ops" / "sessions"
                         / f"{b.session.id}.json").is_file())

    def test_session_id_is_ulid_prefixed(self):
        sid = mint_session_id()
        self.assertTrue(sid.startswith("ses_"))
        body = sid[4:]
        self.assertEqual(len(body), 26)
        self.assertTrue(body.isalnum() and body.upper() == body)

    def test_legacy_session_lands_on_first_persona(self):
        store = IdentityStore(workspace=self.ws)
        res = store.resolve(session="ses_oldlegacy1",
                            channel="repl", user="me")
        self.assertEqual(res.session.persona, "default")
        self.assertTrue((self.ws.personas / "default" / "sessions"
                         / "ses_oldlegacy1.json").is_file())

    def test_resume_reads_persisted_record(self):
        store = IdentityStore(workspace=self.ws)
        first = store.resolve(channel="repl", user="me", persona="cos",
                              new_session=True)
        # new store instance -> reads from disk, keeps persona
        store2 = IdentityStore(workspace=self.ws)
        res = store2.resolve(session=first.session.id,
                             channel="repl", user="me")
        self.assertEqual(res.session.persona, "cos")
        self.assertEqual(res.session.id, first.session.id)

    def test_list_sessions_scoped_by_persona(self):
        store = IdentityStore(workspace=self.ws)
        store.resolve(channel="repl", user="me", persona="cos", new_session=True)
        store.resolve(channel="repl", user="me", persona="ops", new_session=True)
        sessions = SessionStore(self.ws)
        self.assertEqual(len(sessions.list(persona="cos")), 1)
        self.assertEqual(len(sessions.list(persona="ops")), 1)
        self.assertEqual(len(sessions.list()), 2)

    def test_message_event_stamps_workspace_persona(self):
        """C201 contract: trace events carry workspace + persona."""
        from agents_traces.assemble import record_message
        from agents_traces.store import TraceStore

        store = IdentityStore(workspace=self.ws)
        res = store.resolve(channel="repl", user="me", persona="cos",
                            new_session=True)
        ts = TraceStore()
        ts._root = self.ws.traces
        ev = record_message(res.session.id, "user", "hi",
                            channel="repl", user="me",
                            workspace=res.session.workspace,
                            persona=res.session.persona, store=ts)
        self.assertEqual(ev.metadata["workspace"], res.session.workspace)
        self.assertEqual(ev.metadata["persona"], "cos")

    def test_tool_call_event_stamps_env_context(self):
        from agents_traces import interceptor

        events = []
        old_env = dict(os.environ)
        try:
            os.environ["AGENTS_HOST_DIR"] = str(self.host.dir)
            os.environ["AGENTS_WORKSPACE"] = self.ws.id
            os.environ["AGENTS_PERSONA"] = "cos"
            os.environ["AGENTS_TRACES_DIR"] = str(self.ws.traces)
            store = interceptor.get_global_store()
            old_append = store.append
            store.append = events.append
            interceptor.append_trace(tool="mcp.x", session="ses_t1")
        finally:
            store.append = old_append
            os.environ.clear()
            os.environ.update(old_env)
        md = events[0].metadata
        self.assertEqual(md["workspace"], self.ws.id)
        self.assertEqual(md["persona"], "cos")


if __name__ == "__main__":
    unittest.main()
