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
