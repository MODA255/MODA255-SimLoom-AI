from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from engineering_agent import EngineeringAgent
from engineering_agent.formats import write_dxf
from engineering_agent.mcp_server import MCPServer
from engineering_agent.queue import TaskQueue
from engineering_agent.web import create_server
from engineering_agent.viewer import render_asset_export_html


def _drawing(root: Path) -> Path:
    source = root / "drawing.dxf"
    write_dxf(source, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 4}], circles=[{"x": 5, "y": 2, "radius": 1}])
    return source


def test_render_asset_export_metadata_is_valid_json_and_self_contained(tmp_path):
    source = _drawing(tmp_path)
    view = EngineeringAgent({"tools": {}}).execute_operation("geometry.dxf_inspect", {"input_file": str(source)})
    html = render_asset_export_html("drawing.dxf", {"asset": view["geometry"], "svg": "<svg><text>ok</text></svg>"}, title="共享图纸")
    assert "simloom-ai/view-export/v1" in html
    assert "<svg" in html and "修改记录" in html
    assert str(tmp_path) not in html
    assert "src=\"http" not in html and "href=\"http" not in html
    payload = html.split("id='simloom-export'>", 1)[1].split("</script>", 1)[0]
    assert json.loads(payload)["schema_version"] == "simloom-ai/view-export/v1"


def test_asset_export_operation_is_transactional_and_validated(tmp_path):
    _drawing(tmp_path)
    agent = EngineeringAgent({"tools": {}})
    result = agent.execute_operation("project.asset_export", {
        "project_dir": str(tmp_path), "input_file": "drawing.dxf", "output_file": "exports/drawing.html",
        "title": "测试图纸",
    })
    output = tmp_path / "exports/drawing.html"
    assert result["status"] == "completed"
    assert result["run_id"] and output.is_file()
    assert result["sha256"] and result["bytes"] == output.stat().st_size
    content = output.read_text(encoding="utf-8")
    assert "simloom-ai/view-export/v1" in content
    assert "<svg" in content and "修改记录" in content
    assert str(tmp_path) not in content


def test_asset_export_mcp_discovery_call_and_safe_policy(tmp_path):
    _drawing(tmp_path)
    agent = EngineeringAgent({"tools": {}})
    full = MCPServer(agent, profile="full")
    listed = full.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"limit": 500}})
    assert any(item["name"] == "operation.project.asset_export" for item in listed["result"]["tools"])
    called = full.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "operation.project.asset_export",
        "arguments": {"project_dir": str(tmp_path), "input_file": "drawing.dxf", "output_file": "share.html"},
    }})
    assert called["result"]["structuredContent"]["operation"] == "project.asset_export"
    assert (tmp_path / "share.html").is_file()
    safe = MCPServer(agent, profile="safe")
    denied = safe.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "operation.project.asset_export",
        "arguments": {"project_dir": str(tmp_path), "input_file": "drawing.dxf", "output_file": "denied.html"},
    }})
    assert denied["result"]["isError"] is True
    assert denied["result"]["structuredContent"]["error"]["code"] == "INVALID_ARGUMENTS"


def test_asset_export_cli_and_web_routes(tmp_path):
    _drawing(tmp_path)
    cli = subprocess.run([
        sys.executable, "-m", "engineering_agent.cli", "--project-dir", str(tmp_path),
        "asset-export", "drawing.dxf", "cli.html", "--title", "CLI",
    ], capture_output=True, text=True, check=True)
    assert json.loads(cli.stdout)["status"] == "completed"
    assert (tmp_path / "cli.html").is_file()

    server = create_server(EngineeringAgent({"tools": {}}), TaskQueue(tmp_path / "queue.db"), port=0)
    server_thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        host, port = server.server_address
        request = urllib.request.Request(f"http://{host}:{port}/api/asset-export", data=json.dumps({
            "project_dir": str(tmp_path), "input_file": "drawing.dxf", "output_file": "web.html",
        }).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
        assert payload["status"] == "completed"
        assert (tmp_path / "web.html").is_file()
    finally:
        server.shutdown()
        server.server_close()

