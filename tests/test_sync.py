import json
import tempfile
from pathlib import Path
from agents_traces.sync import _merge_mcp_server_into_file, sync_skills


def test_merge_mcp_server_into_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / "mcp.json"
        
        # Test creating new config
        res = _merge_mcp_server_into_file(config_file)
        assert res.startswith("OK")
        assert config_file.exists()
        
        data = json.loads(config_file.read_text(encoding="utf-8"))
        assert "agents-traces" in data["mcpServers"]
        assert data["mcpServers"]["agents-traces"]["args"] == ["-m", "agents_traces", "serve"]
        
        # Test updating existing config with other servers
        data["mcpServers"]["other-server"] = {"command": "node"}
        config_file.write_text(json.dumps(data), encoding="utf-8")
        
        res = _merge_mcp_server_into_file(config_file)
        assert res.startswith("OK")
        
        updated = json.loads(config_file.read_text(encoding="utf-8"))
        assert "other-server" in updated["mcpServers"]
        assert "agents-traces" in updated["mcpServers"]


def test_splice_traces_inject_preserves_other_blocks():
    from agents_traces.sync import splice_traces_inject, MARKER, MARKER_END

    existing = "<!-- agents-memory-sync -->\n# Memory\n<!-- /agents-memory-sync -->\n"
    spliced = splice_traces_inject(existing)
    assert "<!-- agents-memory-sync -->" in spliced
    assert MARKER in spliced
    assert MARKER_END in spliced
    again = splice_traces_inject(spliced)
    assert again.count(MARKER) == 1


def test_sync_host_rules_and_always_on(monkeypatch):
    from agents_traces import sync as sync_mod

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_home = Path(tmpdir)
        monkeypatch.setenv("AGENTS_HOME", str(fake_home / ".agents"))
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        rules = sync_mod.sync_host_rules()
        assert any("agents-traces.mdc" in r for r in rules)
        mdc = fake_home / ".agents" / "rules" / "agents-traces.mdc"
        assert mdc.is_file()
        assert "alwaysApply: true" in mdc.read_text(encoding="utf-8")

        files = sync_mod.sync_always_on_files()
        agents_md = fake_home / ".agents" / "AGENTS.md"
        assert agents_md.is_file()
        assert sync_mod.MARKER in agents_md.read_text(encoding="utf-8")
        assert len(files) >= 4

