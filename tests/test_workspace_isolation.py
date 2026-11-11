"""Per-workspace trace stores and per-call identity resolution."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import agents_traces.interceptor as interceptor
from agents_traces.identity import IdentityStore, default_identity_path
from agents_traces.models import TraceEvent
from agents_traces.store import TraceStore, get_default_traces_dir
from agents_workspace import Host


def _env(host: Path, ws_id: str) -> dict:
    with patch.dict(os.environ, {"AGENTS_HOST_DIR": str(host)}, clear=False):
        if not Host.from_env().workspace_exists(ws_id):
            Host.from_env().create(ws_id)
    return {"AGENTS_HOST_DIR": str(host), "AGENTS_WORKSPACE": ws_id}


def test_interceptor_keeps_one_store_per_workspace():
    with tempfile.TemporaryDirectory() as tmp:
        host = Path(tmp) / "host"
        host.mkdir()
        try:
            with patch.dict(os.environ, _env(host, "wa"), clear=False):
                sa = interceptor.get_global_store()
                interceptor.append_trace(type="tool_call", tool="probe_a")
            with patch.dict(os.environ, _env(host, "wb"), clear=False):
                sb = interceptor.get_global_store()
                interceptor.append_trace(type="tool_call", tool="probe_b")
            assert sa is not sb
            assert "wa" in str(sa.traces_dir)
            assert "wb" in str(sb.traces_dir)
            assert any("probe_a" in (e.tool or "") for e in sa.iter_events())
            assert not any("probe_a" in (e.tool or "") for e in sb.iter_events())
            # switching back hits the cached store
            with patch.dict(os.environ, _env(host, "wa"), clear=False):
                assert interceptor.get_global_store() is sa
        finally:
            interceptor._stores.clear()
            interceptor._process_session_id = None


def test_traces_dir_resolves_per_workspace_per_call():
    with tempfile.TemporaryDirectory() as tmp:
        host = Path(tmp) / "host"
        host.mkdir()
        with patch.dict(os.environ, _env(host, "wa"), clear=False):
            a = get_default_traces_dir()
            TraceStore().append(TraceEvent(session="s-a", type="tool_call", tool="t"))
        with patch.dict(os.environ, _env(host, "wb"), clear=False):
            b = get_default_traces_dir()
        assert a != b
        assert a.is_dir()
        assert not b.exists() or not list(b.glob("*.jsonl"))


def test_identity_path_resolves_per_call():
    with tempfile.TemporaryDirectory() as tmp:
        host = Path(tmp) / "host"
        host.mkdir()
        with patch.dict(os.environ, _env(host, "wa"), clear=False):
            a = default_identity_path()
        with patch.dict(os.environ, _env(host, "wb"), clear=False):
            b = default_identity_path()
        # users.json is host-level: one file shared by workspaces
        assert a == b == host / "users.json"
        # explicit workspace binding produces the same host file
        with patch.dict(os.environ, _env(host, "wa"), clear=False):
            from agents_workspace import Workspace

            ws = Workspace.from_env()
            assert IdentityStore(workspace=ws).path == host / "users.json"


def test_identity_falls_back_to_local_user_id():
    with tempfile.TemporaryDirectory() as tmp:
        ident = IdentityStore(Path(tmp) / "users.json")
        res = ident.resolve(channel="repl", user="local")
        assert res.user.id.startswith("u_")
        data = ident.path.read_text(encoding="utf-8")
        assert res.user.id in data
