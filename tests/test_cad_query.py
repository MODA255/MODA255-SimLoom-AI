from __future__ import annotations

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import Block, CadDocument
from engineering_agent.cad_operations import _block_instance_bounds
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
    assert {"attribute_properties", "attribute_text_contains", "block_geometry"} <= set(query["inputSchema"]["properties"])
    assert query["outputSchema"]["properties"]["warnings"]["items"]["type"] == "string"


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


def test_cad_query_accepts_agentcad_filter_object_and_tag_prefix(tmp_path):
    source = _drawing(tmp_path)
    agent = EngineeringAgent({"tools": {}})
    tagged = agent.execute_operation("cad.entity_update", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": source,
        "entity_id": "e00002", "patch": {"tag": "hole.1"},
    })
    assert tagged["persisted_validation"]["valid"]
    result = agent.execute_operation("cad.query", {
        "project_dir": str(tmp_path), "input_file": source,
        "filter": {"type": "CIRCLE", "layer": "HOLES", "tag_prefix": "hole."},
    })
    assert result["total_count"] == 1
    assert result["entities"][0]["tag"] == "hole.1"


def test_cad_query_filters_block_name_and_insert_attributes(tmp_path):
    source = tmp_path / "blocks.dxf"
    write_dxf(source, blocks=[
        {"name": "LABEL", "base_point": {"x": 0, "y": 0}, "entities": [
            {"type": "LINE", "start": {"x": 0, "y": 0}, "end": {"x": 4, "y": 0}},
        ]},
    ], entities=[
        {"type": "INSERT", "block_name": "LABEL", "insert": {"x": 10, "y": 20},
         "attributes": [{"tag": "NAME", "text": "Bracket", "insert": {"x": 10, "y": 20}, "height": 2}]},
        {"type": "INSERT", "block_name": "LABEL", "insert": {"x": 40, "y": 20},
         "attributes": [{"tag": "NAME", "text": "Cover", "insert": {"x": 40, "y": 20}, "height": 2}]},
    ])
    agent = EngineeringAgent({"tools": {}})
    result = agent.execute_operation("cad.query", {
        "project_dir": str(tmp_path), "input_file": source,
        "filter": {"type": "INSERT", "block_name": "LABEL", "attribute_properties": {"NAME": "Bracket"}},
        "attribute_text_contains": "brack", "block_geometry": True,
        "include_geometry": False,
    })
    assert result["total_count"] == 1
    entity = result["entities"][0]
    assert entity["block_name"] == "LABEL"
    assert "attributes" not in entity
    assert entity["block_instance"]["bounds"] == [10.0, 20.0, 14.0, 20.0]
    assert entity["block_instance"]["block_path"] == ["LABEL"]
    assert entity["block_instance"]["block_paths"] == [["LABEL"]]


def test_cad_query_block_geometry_applies_rotation_scale_and_window(tmp_path):
    source = tmp_path / "transformed-block.dxf"
    write_dxf(source, blocks=[
        {"name": "PIN", "base_point": {"x": 1, "y": 2}, "entities": [
            {"type": "LINE", "start": {"x": 1, "y": 2}, "end": {"x": 3, "y": 2}},
        ]},
    ], inserts=[{"block_name": "PIN", "insert": {"x": 10, "y": 20},
                 "scale": {"x": 2, "y": 3}, "rotation": 90}])
    agent = EngineeringAgent({"tools": {}})
    result = agent.execute_operation("cad.query", {
        "project_dir": str(tmp_path), "input_file": source, "entity_type": "INSERT",
        "block_geometry": True, "window": [{"x": 9.5, "y": 23.5}, {"x": 10.5, "y": 24.5}],
    })
    assert result["total_count"] == 1
    assert result["entities"][0]["block_instance"]["bounds"] == [10.0, 20.0, 10.0, 24.0]

    outside = agent.execute_operation("cad.query", {
        "project_dir": str(tmp_path), "input_file": source, "entity_type": "INSERT",
        "block_geometry": True, "window": [{"x": 11, "y": 23.5}, {"x": 12, "y": 24.5}],
    })
    assert outside["total_count"] == 0


def test_cad_query_nested_blocks_returns_all_geometry_paths(tmp_path):
    source = tmp_path / "nested-blocks.dxf"
    write_dxf(source, blocks=[
        {"name": "LEAF", "base_point": {"x": 0, "y": 0}, "entities": [
            {"type": "LINE", "start": {"x": 0, "y": 0}, "end": {"x": 1, "y": 0}},
        ]},
        {"name": "ASSEMBLY", "base_point": {"x": 0, "y": 0}, "entities": [
            {"type": "INSERT", "block_name": "LEAF", "insert": {"x": 5, "y": 2}, "rotation": 90},
            {"type": "LINE", "start": {"x": 0, "y": 0}, "end": {"x": 0, "y": 1}},
        ]},
    ], inserts=[{"block_name": "ASSEMBLY", "insert": {"x": 10, "y": 10}, "scale": {"x": 2, "y": 2}}])
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.query", {
        "project_dir": str(tmp_path), "input_file": source, "entity_type": "INSERT", "block_geometry": True,
    })
    instance = result["entities"][0]["block_instance"]
    assert instance["block_path"] == ["ASSEMBLY", "LEAF"]
    assert instance["block_paths"] == [["ASSEMBLY"], ["ASSEMBLY", "LEAF"]]
    assert instance["bounds"] == [10.0, 10.0, 20.0, 16.0]


def test_block_bounds_reports_missing_and_recursive_definitions():
    missing = CadDocument(blocks={})
    bounds, info = _block_instance_bounds(missing, {"block_name": "UNKNOWN", "insert": {"x": 0, "y": 0}})
    assert bounds is None
    assert info["warnings"] == ["BLOCK_NOT_FOUND:UNKNOWN"]

    recursive = CadDocument(blocks={
        "A": Block("A", entities=[{"type": "INSERT", "block_name": "A", "insert": {"x": 0, "y": 0}}]),
    })
    bounds, info = _block_instance_bounds(recursive, {"block_name": "A", "insert": {"x": 0, "y": 0}})
    assert bounds is None
    assert info["warnings"] == ["BLOCK_RECURSIVE:A/A"]
