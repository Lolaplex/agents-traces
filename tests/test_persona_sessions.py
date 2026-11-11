"""C201: SessionRecord keyed by workspace+user+persona, stored under the persona."""
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


if __name__ == "__main__":
    unittest.main()
