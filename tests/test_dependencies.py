import json
from pathlib import Path

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.mcp_server import MCPServer


def test_dependency_scan_builds_explicit_text_and_sidecar_edges(tmp_path):
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "bracket.step").write_text("ISO-10303-21;ENDSEC;", encoding="utf-8")
    (tmp_path / "drawing.dxf").write_text("0\nSECTION\n", encoding="utf-8")
    (tmp_path / "drawing.dxf.cad.json").write_text("{}", encoding="utf-8")
    (tmp_path / "assembly.xml").write_text(
        '<assembly><link href="parts/bracket.step"/><link href="missing.catpart"/></assembly>', encoding="utf-8")
    (tmp_path / "simloom.dependencies.json").write_text(json.dumps({"dependencies": [
        {"source": "assembly.xml", "target": "drawing.dxf", "relation": "drawing"},
        {"source": "assembly.xml", "target": "/Library/External/seat.CATPart", "relation": "vendor_link"},
    ]}), encoding="utf-8")
    (tmp_path / "model.CATPart").write_bytes(b"opaque native CATPart")

    result = EngineeringAgent({"tools": {}}).execute_operation("project.dependency_scan", {"project_dir": str(tmp_path)})
    assert result["schema_version"].endswith("/v1")
    assert any(edge["relation"] == "companion" for edge in result["edges"])
    assert any(edge["target"] == "parts/bracket.step" for edge in result["edges"])
    assert any(edge["target"] == "missing.catpart" for edge in result["edges"])
    assert "missing.catpart" in result["missing"]
    assert any(item.endswith("seat.CATPart") for item in result["external"])
    native = next(item for item in result["nodes"] if item["path"] == "model.CATPart")
    assert native["parse_status"] == "opaque" and native["sha256"]


def test_dependency_scan_can_limit_paths_and_reject_traversal(tmp_path):
    (tmp_path / "a.json").write_text('{"input": "b.csv"}', encoding="utf-8")
    (tmp_path / "b.csv").write_text("x\n1\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("b.csv", encoding="utf-8")
    agent = EngineeringAgent({"tools": {}})
    result = agent.execute_operation("project.dependency_scan", {"project_dir": str(tmp_path), "paths": ["a.json"]})
    assert result["paths"] == ["a.json"]
    assert {item["path"] for item in result["nodes"]} == {"a.json", "b.csv"}
    with pytest.raises(ValueError, match="DEPENDENCY"):
        agent.execute_operation("project.dependency_scan", {"project_dir": str(tmp_path), "paths": ["../a.json"]})


def test_dependency_scan_is_read_only_mcp_resource(tmp_path):
    (tmp_path / "run.json").write_text('{"result": "metrics.csv"}', encoding="utf-8")
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="safe")
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {
        "uri": f"engineering://dependencies?root={tmp_path}"}})
    payload = json.loads(response["result"]["contents"][0]["text"])
    assert payload["schema_version"].endswith("/v1") and payload["nodes"]
    assert not (tmp_path / ".engineering-agent" / "watchers").exists()
