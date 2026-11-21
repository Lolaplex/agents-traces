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
    import sys
    from agents_traces import sync as sync_mod

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_home = Path(tmpdir)
        host_dir = fake_home / ".agents"
        monkeypatch.setenv("AGENTS_HOST_DIR", str(host_dir))
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        rules = sync_mod.sync_host_rules()
        assert any("agents-traces.mdc" in r for r in rules)
        mdc = host_dir / "workspaces" / "default" / "rules" / "agents-traces.mdc"
        assert mdc.is_file()
        assert "alwaysApply: true" in mdc.read_text(encoding="utf-8")

        files = sync_mod.sync_always_on_files()
        agents_md = host_dir / "AGENTS.md"
        claude_md = host_dir / "CLAUDE.md"
        assert agents_md.is_file()
        assert claude_md.is_file()
        assert sync_mod.MARKER in agents_md.read_text(encoding="utf-8")
        assert sync_mod.MARKER in claude_md.read_text(encoding="utf-8")
        assert not (host_dir / "workspaces" / "default" / "AGENTS.md").exists()
        assert len(files) >= 4

        # Custom CLAUDE.md keeps its own text across repeated syncs
        claude_md.unlink()
        claude_md.write_text("# Custom CLAUDE notes\n", encoding="utf-8")
        sync_mod.sync_always_on_files()
        sync_mod.sync_always_on_files()
        c_text = claude_md.read_text(encoding="utf-8")
        assert "# Custom CLAUDE notes" in c_text
        assert c_text.count(sync_mod.MARKER) == 1

        # Standalone ImportError fallback without agents_workspace
        monkeypatch.setitem(sys.modules, "agents_workspace", None)
        standalone_host = fake_home / "standalone_agents"
        monkeypatch.setenv("AGENTS_HOST_DIR", str(standalone_host))
        sync_mod.sync_always_on_files()
        assert (standalone_host / "AGENTS.md").is_file()
        assert (standalone_host / "CLAUDE.md").is_file()
        assert sync_mod.MARKER in (standalone_host / "AGENTS.md").read_text(encoding="utf-8")

