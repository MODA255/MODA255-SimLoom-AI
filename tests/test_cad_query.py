from __future__ import annotations

from engineering_agent import EngineeringAgent
from engineering_agent.formats import write_dxf


def _drawing(tmp_path):
    path = tmp_path / "query.dxf"
    write_dxf(path, entities=[
        {"type": "LINE", "layer": "FRAME", "start": {"x": 0, "y": 0}, "end": {"x": 20, "y": 0}},
        {"type": "CIRCLE", "layer": "HOLES", "center": {"x": 10, "y": 10}, "radius": 2},
        {"type": "TEXT", "layer": "ANNOTATION", "insert": {"x": 2, "y": 2}, "text": "M10 HOLE", "height": 2},
    ])
    return path


def test_cad_query_filters_semantic_entities_and_returns_stable_refs(tmp_path):
    source = _drawing(tmp_path)
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.query", {
        "project_dir": str(tmp_path), "input_file": source,
        "entity_type": "CIRCLE", "layer": "HOLES", "window": [{"x": 5, "y": 5}, {"x": 15, "y": 15}],
    })
    assert result["total_count"] == 1
    entity = result["entities"][0]
    assert entity["type"] == "CIRCLE"
    assert entity["ref"]["document_id"] == result["document_id"]
    assert entity["ref"]["entity_id"] == entity["id"]
    assert result["validation"]["valid"] is True


def test_cad_query_text_properties_pagination_and_metadata_only(tmp_path):
    source = _drawing(tmp_path)
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.query", {
        "project_dir": str(tmp_path), "input_file": source,
        "text_contains": "m10", "limit": 1, "include_geometry": False,
    })
    assert result["total_count"] == 1 and result["entities"][0]["type"] == "TEXT"
    assert result["entities"][0].keys() >= {"id", "type", "layer", "ref"}
    assert "text" not in result["entities"][0]


def test_cad_query_is_discoverable_through_mcp(tmp_path):
    from engineering_agent.mcp_server import MCPServer
    listed = MCPServer(EngineeringAgent({"tools": {}}), profile="safe").handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"limit": 500},
    })
    query = next(item for item in listed["result"]["tools"] if item["name"] == "operation.cad.query")
    assert query["outputSchema"]["properties"]["entities"]["type"] == "array"


def test_cad_query_accepts_entity_refs_and_numeric_closed_filters(tmp_path):
    source = _drawing(tmp_path)
    agent = EngineeringAgent({"tools": {}})
    first = agent.execute_operation("cad.query", {
        "project_dir": str(tmp_path), "input_file": source, "entity_type": "CIRCLE",
    })
    entity_ref = first["entities"][0]["ref"]
    result = agent.execute_operation("cad.query", {
        "project_dir": str(tmp_path), "input_file": source,
        "entity_ids": [entity_ref], "number_ranges": {"radius": {"min": 1, "max": 3}},
        "closed": False,
    })
    assert result["matched_ids"] == [first["matched_ids"][0]]
