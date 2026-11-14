import base64
import json
import os
import subprocess
import sys
import threading
import urllib.request
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from engineering_agent import EngineeringAgent
from engineering_agent.brep import get_brep_backend
from engineering_agent.mcp_server import MCPServer
from engineering_agent.web import create_server
from engineering_agent.operations import OperationRegistry
from engineering_agent.validation import validate_plan
from engineering_agent.versioning import VersionStore
from engineering_agent.formats import write_csv, write_dxf, write_json, write_pdf, write_step, write_svg
from engineering_agent.dxf import diff_documents, read_dxf, transform_document
from engineering_agent.protocol import ErrorCode, ProtocolError, failure
from engineering_agent.queue import TaskQueue
from engineering_agent.validation import validate_asset
from engineering_agent.transactions import RunTransaction
from engineering_agent.ingest import ArtifactReader
from engineering_agent.viewer import render_timeline_svg


def test_full_vehicle_chain_is_planned():
    agent = EngineeringAgent({"tools": {}})
    plan = agent.plan("用CATIA建模悬架，导入Adams做动力学，再用VI-grade做操稳和圈速分析")
    assert [step.id for step in plan.steps] == [
        "catia_model",
        "adams_simulation",
        "vigrade_vehicle_run",
    ]
    assert plan.steps[-1].depends_on == ["adams_simulation"]


def test_structural_chain_uses_ansys_after_catia():
    agent = EngineeringAgent({"tools": {}})
    plan = agent.plan("CATIA参数化零件，导出到ANSYS做网格和应力分析")
    assert [step.tool.value for step in plan.steps] == ["catia", "ansys"]
    assert plan.steps[1].depends_on == ["catia_model"]


def test_explicit_vendor_evidence_parameters_select_native_runner_actions():
    agent = EngineeringAgent({"tools": {}})
    parameters = {
        "catia": {"input_model": "inputs/part.CATPart", "native_output": "artifacts/updated.CATPart",
                  "step_output": "artifacts/updated.step", "report_output": "artifacts/catia-evidence.json",
                  "parameter_updates": [{"name": "Part1\\Length", "value": "25mm"}]},
        "ansys": {"db_file": "inputs/analysis.mechdat", "analysis_name": "Static Structural",
                  "native_output": "artifacts/solved.mechdat", "result_output": "artifacts/ansys-results.json",
                  "report_output": "artifacts/ansys-evidence.json", "required_metrics": ["equivalent_stress"],
                  "metric_units": {"equivalent_stress": "MPa"}},
    }
    plan = agent.plan("CATIA 参数更新后用 Ansys 求解", parameters=parameters)
    assert [step.action for step in plan.steps] == ["update_parameters", "solve_configured_model"]
    assert plan.steps[0].outputs == ["artifacts/updated.CATPart", "artifacts/updated.step", "artifacts/catia-evidence.json"]
    assert plan.steps[1].outputs == ["artifacts/solved.mechdat", "artifacts/ansys-results.json", "artifacts/ansys-evidence.json"]


def test_run_is_safe_dry_run_by_default():
    agent = EngineeringAgent({"tools": {}})
    report = agent.run("用Adams做机构动力学", dry_run=True)
    assert [result.status for result in report.results] == ["planned"]


def test_report_is_json_serializable():
    agent = EngineeringAgent({"tools": {}})
    report = agent.run("用VI-grade做整车操稳仿真", dry_run=True)
    json.dumps(report.to_dict(), ensure_ascii=False)


def test_plan_has_typed_operation_validation():
    agent = EngineeringAgent({"tools": {}})
    plan = agent.plan("CATIA建模后用ANSYS做应力分析")
    assert validate_plan(plan)["valid"]
    assert any(item["name"] == "ansys.mesh_and_solve" for item in OperationRegistry().list())


def test_operation_registry_enforces_published_schema_and_accepts_pathlike_inputs(tmp_path):
    registry = OperationRegistry()
    errors = registry.validate_arguments("cad.entity_array_rectangular", {
        "input_file": tmp_path / "part.dxf", "output_file": tmp_path / "out.dxf",
        "rows": 0, "columns": 2,
    })
    assert "rows:MINIMUM" in errors and "columns:MINIMUM" not in errors
    errors = registry.validate_arguments("cad.plot", {
        "input_file": tmp_path / "part.dxf", "output_file": tmp_path / "out.pdf", "paper": "A9",
    })
    assert "paper:ENUM" in errors
    errors = registry.validate_arguments("cad.plot", {
        "input_file": tmp_path / "part.dxf", "output_file": tmp_path / "out.pdf", "scale": "bad",
    })
    assert "scale:PATTERN" in errors
    assert registry.validate_arguments("cad.entity_offset", {
        "input_file": tmp_path / "part.dxf", "output_file": tmp_path / "out.dxf", "distance": 0,
    }) == []


def test_mcp_surface_lists_and_calls_tools():
    server = MCPServer(EngineeringAgent({"tools": {}}))
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert any(item["name"] == "engineering_plan" for item in listed["result"]["tools"])
    called = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "engineering_plan", "arguments": {"prompt": "用Adams做动力学"}}})
    assert "adams_simulation" in called["result"]["content"][0]["text"]
    templates = server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/templates/list"})
    assert templates["result"]["resourceTemplates"]
    missing = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "engineering_ingest", "arguments": {"path": str(Path("/does/not/exist"))}}})
    assert missing["result"]["isError"] is True
    assert missing["result"]["structuredContent"]["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_mcp_returns_structured_content_and_operation_schema_metadata(tmp_path):
    drawing = tmp_path / "drawing.dxf"
    write_dxf(drawing, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 5}])
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full")

    planned = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "engineering_plan", "arguments": {"prompt": "用Adams做动力学"}}})
    structured = planned["result"]["structuredContent"]
    assert structured["schemaVersion"] == server.schema_version
    assert structured["plan"]["steps"][0]["tool"] == "adams"

    opened = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "operation.cad.document_open",
        "arguments": {"input_file": str(drawing), "operation_schema_version": "1.0"}}})
    assert opened["result"]["structuredContent"]["operation"] == "cad.document_open"
    assert opened["result"]["structuredContent"]["operationSchemaVersion"] == "1.0"

    unsupported = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "operation.cad.document_open",
        "arguments": {"input_file": str(drawing), "operation_schema_version": "99.0"}}})
    assert unsupported["result"]["isError"] is True
    error = unsupported["result"]["structuredContent"]["error"]
    assert error["code"] == "SCHEMA_VERSION_UNSUPPORTED"
    assert error["schemaVersion"] == server.schema_version
    assert error["details"]["supported"] == ["1.0"]


def test_mcp_tool_execution_errors_are_is_error_but_unknown_tools_are_jsonrpc_errors():
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="safe")
    denied = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "engineering_run", "arguments": {"prompt": "执行任务", "dry_run": False}}})
    assert denied["result"]["isError"] is True
    assert denied["result"]["structuredContent"]["error"]["code"] == "INVALID_ARGUMENTS"

    unknown = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "engineering_missing_tool", "arguments": {}}})
    assert unknown["error"]["code"] == "INVALID_ARGUMENTS"


def test_version_timeline_svg_exposes_safe_keyboard_selectable_nodes():
    svg = render_timeline_svg([{
        "event": "version_created",
        "version_id": "v-1",
        "label": "方案 <A>",
        "created_at": "2026-11-13T10:00:00Z",
    }])
    assert "class='timeline-node'" in svg
    assert "data-version-id='v-1'" in svg
    assert "tabindex='0'" in svg and "role='button'" in svg
    assert "方案 &lt;A&gt;" in svg


def test_mcp_pagination_subscription_and_schema_compatibility(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}))
    initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    assert initialized["result"]["capabilities"]["resources"]["subscribe"] is True
    page = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"limit": 2}})
    assert len(page["result"]["tools"]) == 2
    assert page["result"]["nextCursor"]
    next_page = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"limit": 2, "cursor": page["result"]["nextCursor"]}})
    assert next_page["result"]["tools"]
    uri = f"engineering://project?root={tmp_path}"
    subscribed = server.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/subscribe", "params": {"uri": uri}})
    assert subscribed["result"]["subscribed"] is True
    server.handle({"jsonrpc": "2.0", "id": 5, "method": "resources/unsubscribe", "params": {"uri": uri}})
    unsupported = server.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "engineering_plan", "arguments": {"prompt": "用Adams做动力学", "schema_version": "engineering-agent/mcp/v99"}}})
    assert unsupported["result"]["isError"] is True
    assert unsupported["result"]["structuredContent"]["error"]["code"] == "SCHEMA_VERSION_UNSUPPORTED"


def test_mcp_negotiates_requested_protocol_and_publishes_standard_output_schema():
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full")
    initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": "2025-03-26"}})
    assert initialized["result"]["protocolVersion"] == "2025-03-26"
    defaulted = server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}})
    assert defaulted["result"]["protocolVersion"] == "2025-03-26"

    listed = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"limit": 500}})
    plan = next(item for item in listed["result"]["tools"] if item["name"] == "engineering_plan")
    operation = next(item for item in listed["result"]["tools"] if item["name"] == "operation.cad.entity_move")
    assert plan["outputSchema"]["type"] == "object"
    assert "schema_version" in plan["inputSchema"]["properties"]
    assert operation["outputSchema"]["type"] == "object"
    assert "document" in operation["outputSchema"]["properties"]
    assert "operationSchemaVersion" in operation["inputSchema"]["properties"]


def test_mcp_stdio_notifications_ping_and_real_subprocess_round_trip(tmp_path):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    process = subprocess.Popen([sys.executable, "-m", "engineering_agent.cli", "mcp", "--profile", "safe"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=environment, cwd=str(Path(__file__).resolve().parents[1]))
    assert process.stdin and process.stdout
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2024-11-05"}}) + "\n")
    process.stdin.flush()
    initialized = json.loads(process.stdout.readline())
    assert initialized["result"]["protocolVersion"] == "2024-11-05"
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")
    process.stdin.flush()
    assert json.loads(process.stdout.readline()) == {"jsonrpc": "2.0", "id": 2, "result": {}}
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"limit": 1}}) + "\n")
    process.stdin.flush()
    listed = json.loads(process.stdout.readline())
    assert len(listed["result"]["tools"]) == 1
    process.stdin.close()
    process.terminate()
    process.wait(timeout=5)


def test_mcp_subscription_queues_resource_update_after_mutation(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full")
    uri = f"engineering://project?root={tmp_path}"
    subscribed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/subscribe", "params": {"uri": uri}})
    assert subscribed["result"]["subscribed"] is True
    created = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "engineering_version", "arguments": {"project_dir": str(tmp_path), "label": "baseline", "paths": []}}})
    assert "result" in created
    notifications = server.drain_notifications()
    assert notifications == [{"jsonrpc": "2.0", "method": "notifications/resources/updated",
                              "params": {"uri": uri, "schemaVersion": server.schema_version}}]


def test_mcp_asset_view_returns_structured_metadata_and_image_content(tmp_path):
    drawing = tmp_path / "drawing.dxf"
    write_dxf(drawing, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 5}], texts=[{"x": 1, "y": 1, "text": "mount"}])
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="safe")
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"limit": 500}})
    view_schema = next(item for item in listed["result"]["tools"] if item["name"] == "engineering_asset_view")
    assert view_schema["inputSchema"]["properties"]["include_image"]["default"] is True

    called = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "engineering_asset_view",
        "arguments": {"root": str(tmp_path), "path": drawing.name},
    }})
    content = called["result"]["content"]
    assert [item["type"] for item in content] == ["text", "image"]
    assert "DXF" in content[0]["text"]
    assert base64.b64decode(content[1]["data"]).startswith(b"<svg")
    assert content[1]["mimeType"] == "image/svg+xml"

    uri = f"engineering://asset?root={tmp_path}&path={drawing.name}"
    resource = server.handle({"jsonrpc": "2", "id": 3, "method": "resources/read", "params": {"uri": uri}})
    contents = resource["result"]["contents"]
    assert {item["mimeType"] for item in contents} == {"application/json", "image/svg+xml"}
    assert base64.b64decode(next(item for item in contents if item["mimeType"] == "image/svg+xml")["blob"]).startswith(b"<svg")
    discovered = server.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {"root": str(tmp_path), "path": drawing.name}})
    assert any(item["uri"].startswith("engineering://asset?") for item in discovered["result"]["resources"])


def test_mcp_cad_resource_cannot_escape_project_root(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="safe")
    uri = f"engineering://cad?root={tmp_path}&path=/etc/passwd"
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": uri}})
    assert response["error"]["code"] == "PATH_OUTSIDE_ROOT"


def test_mcp_inspect_image_returns_read_only_crop_with_source_coordinates(tmp_path):
    source = tmp_path / "drawing.png"
    image = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    image.paste((0, 0, 255, 255), (20, 10, 60, 30))
    image.save(source)
    original = source.read_bytes()
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full")
    called = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
        "name": "engineering_inspect_image",
        "arguments": {"root": str(tmp_path), "path": source.name, "region": [20, 10, 100, 50], "max_size": 160, "rotate": 90},
    }})
    content = called["result"]["content"]
    metadata = json.loads(content[0]["text"])
    preview = Image.open(BytesIO(base64.b64decode(content[1]["data"])))
    assert [item["type"] for item in content] == ["text", "image"]
    assert metadata["source_size"] == [120, 80]
    assert metadata["region"] == [20, 10, 100, 50]
    assert metadata["preview_size"] == [80, 160]
    assert metadata["mime_type"] == "image/png"
    assert preview.size == (80, 160)
    assert source.read_bytes() == original


def test_mcp_measure_image_returns_source_bbox_centroid_and_overlay(tmp_path):
    source = tmp_path / "drawing.png"
    image = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    image.paste((0, 0, 255, 255), (20, 10, 60, 30))
    image.save(source)
    original = source.read_bytes()
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="safe")
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"limit": 500}})
    assert any(item["name"] == "engineering_measure_image" for item in listed["result"]["tools"])
    called = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "engineering_measure_image", "arguments": {
            "root": str(tmp_path), "path": source.name, "target_color": [0, 0, 255],
            "region": [0, 0, 100, 50], "tolerance": 0,
        }}})
    content = called["result"]["content"]
    metadata = json.loads(content[0]["text"])
    preview = Image.open(BytesIO(base64.b64decode(content[1]["data"])))
    assert metadata["match_count"] == 800
    assert metadata["match_bbox"] == [20, 10, 60, 30]
    assert metadata["centroid"] == [39.5, 19.5]
    assert preview.size == (1024, 512)
    assert source.read_bytes() == original


def test_mcp_measure_image_maps_source_pixels_to_drawing_coordinates(tmp_path):
    source = tmp_path / "drawing.png"
    image = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    image.paste((0, 0, 255, 255), (20, 10, 60, 30))
    image.save(source)
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="safe")
    called = server.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
        "name": "engineering_measure_image", "arguments": {
            "root": str(tmp_path), "path": source.name, "target_color": [0, 0, 255],
            "tolerance": 0, "coordinate_system": {
                "x_min": 0, "x_max": 120, "y_min": 0, "y_max": 80, "y_axis": "up",
            },
        }}})
    metadata = json.loads(called["result"]["content"][0]["text"])
    assert metadata["coordinate_transform"]["pixel_origin"] == "top_left"
    assert metadata["world_match_bbox"] == [20.0, 50.0, 60.0, 70.0]
    assert metadata["world_centroid"] == [39.5, 60.5]

    called_down = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
        "name": "engineering_measure_image", "arguments": {
            "root": str(tmp_path), "path": source.name, "target_color": [0, 0, 255],
            "tolerance": 0, "coordinate_system": {
                "x_min": -10, "x_max": 50, "y_min": 100, "y_max": 180, "y_axis": "down",
            },
        }}})
    metadata_down = json.loads(called_down["result"]["content"][0]["text"])
    assert metadata_down["world_match_bbox"] == [0.0, 110.0, 20.0, 130.0]
    assert metadata_down["world_centroid"] == [9.75, 119.5]


def test_measure_image_rejects_invalid_coordinate_system(tmp_path):
    source = tmp_path / "drawing.png"
    Image.new("RGBA", (12, 8), (0, 0, 255, 255)).save(source)
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="safe")
    called = server.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {
        "name": "engineering_measure_image", "arguments": {
            "root": str(tmp_path), "path": source.name, "target_color": [0, 0, 255],
            "coordinate_system": {"x_min": 0, "x_max": 1, "y_min": 0, "y_max": 1, "y_axis": "sideways"},
        }}})
    assert called["result"]["isError"] is True
    assert called["result"]["structuredContent"]["error"]["code"] == "BAD_IMAGE_COORDINATE_SYSTEM"


def test_mcp_operation_schema_exposes_output_contract_and_side_effects():
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full")
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"limit": 500}})
    cad_move = next(item for item in listed["result"]["tools"] if item["name"] == "operation.cad.entity_move")
    schema = cad_move["inputSchema"]
    assert schema["x-engineering-schema-version"] == "1.0"
    assert schema["x-output-schema"]
    assert "write_artifacts" in schema["x-side-effects"]
    assert schema["x-safety"] == "write"


def test_ingest_step_csv_and_json(tmp_path):
    step = tmp_path / "part.step"
    step.write_text("#1=CARTESIAN_POINT('',(0.,0.,0.));#2=CARTESIAN_POINT('',(2.,3.,4.));", encoding="utf-8")
    csv_path = tmp_path / "motion.csv"
    csv_path.write_text("time,yaw\n0,0\n1,2\n", encoding="utf-8")
    result = tmp_path / "result.json"
    result.write_text('{"vehicle":{"lap_time_s":92.0}}', encoding="utf-8")
    agent = EngineeringAgent({"tools": {}})
    assert agent.ingest(str(step))["geometry"]["cartesian_point_count"] == 2
    assert agent.ingest(str(csv_path))["tables"]["row_count"] == 2
    assert agent.ingest(str(result))["metrics"]["vehicle.lap_time_s"] == 92.0


def test_version_diff_ignores_ephemeral_object_id(tmp_path):
    result = tmp_path / "result.json"
    result.write_text('{"lap_time_s":95}', encoding="utf-8")
    store = VersionStore(tmp_path)
    left = store.ingest_and_create("left", [result])
    result.write_text('{"lap_time_s":90}', encoding="utf-8")
    right = store.ingest_and_create("right", [result], left["id"])
    diff = store.diff(left["id"], right["id"])
    assert any(item["path"].endswith("lap_time_s") for item in diff["changes"])


def test_version_diff_matches_assets_by_source_path_when_order_changes(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text('{"metric": 1}', encoding="utf-8")
    second.write_text('{"metric": 2}', encoding="utf-8")
    store = VersionStore(tmp_path)
    left = store.ingest_and_create("left", [first, second])
    right = store.create("left", list(reversed([ArtifactReader().read(path) for path in (first, second)])), left["id"])
    assert store.diff(left["id"], right["id"])["change_count"] == 0


def test_project_visual_diff_uses_captured_dxf_geometry(tmp_path):
    drawing = tmp_path / "part.dxf"
    write_dxf(drawing, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0}])
    agent = EngineeringAgent({"tools": {}})
    left = agent.create_version(str(tmp_path), "left", [str(drawing)])
    agent.execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": str(drawing), "output_file": str(drawing),
        "entity_ids": ["e00001"], "delta": [2, 0, 0],
    })
    right = agent.create_version(str(tmp_path), "right", [str(drawing)], left["id"])
    result = agent.execute_operation("project.visual_diff", {
        "project_dir": str(tmp_path), "left": left["id"], "right": right["id"],
    })
    assert result["visual_diff"]["counts"]["changed"] == 1
    assert "data-cad-visual-diff='v1'" in result["visual_diff"]["svg"]


def test_local_formats_and_independent_validators(tmp_path):
    write_step(tmp_path / "part.step", [(0, 0, 0), (5, 6, 7)])
    write_csv(tmp_path / "data.csv", [{"time": 0, "value": 1}, {"time": 1, "value": 2}])
    write_json(tmp_path / "result.json", {"metric": 1.2})
    write_dxf(tmp_path / "part.dxf", lines=[{"x1": 0, "y1": 0, "x2": 5, "y2": 6}])
    write_svg(tmp_path / "part.svg", lines=[{"x1": 0, "y1": 0, "x2": 5, "y2": 6}])
    write_pdf(tmp_path / "report.pdf", "Engineering Agent")
    agent = EngineeringAgent({"tools": {}})
    assert validate_asset(agent.ingest(str(tmp_path / "part.step")))["valid"]
    assert validate_asset(agent.ingest(str(tmp_path / "result.json")), ["metric"])["valid"]
    assert all((tmp_path / name).exists() for name in ("part.step", "data.csv", "result.json", "part.dxf", "part.svg", "report.pdf"))


def test_protocol_error_envelope():
    error = ProtocolError(ErrorCode.INVALID_ARGUMENTS, "bad argument", details={"field": "x"})
    payload = failure(error)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_ARGUMENTS"


def test_dxf_semantic_entities_layers_transform_and_diff(tmp_path):
    source = tmp_path / "source.dxf"
    target = tmp_path / "target.dxf"
    write_dxf(
        source,
        lines=[{"x1": 0, "y1": 0, "x2": 5, "y2": 0, "layer": "FRAME"}],
        circles=[{"x": 2, "y": 2, "radius": 1, "layer": "WHEEL"}],
        polylines=[{"points": [(0, 0), (1, 0), (1, 1)], "closed": True, "layer": "FRAME"}],
        texts=[{"x": 1, "y": 1, "text": "mount", "layer": "ANNOTATION"}],
    )
    document = read_dxf(source)
    assert document["entity_types"] == {"LINE": 1, "CIRCLE": 1, "LWPOLYLINE": 1, "TEXT": 1}
    assert document["layers"]["FRAME"] == 2
    moved = transform_document(document, translate=(10, 0, 0), scale=2)
    assert moved["bbox"]["x"]["min"] == 10
    diff = diff_documents(document, moved)
    assert diff["change_count"] >= 1
    write_dxf(target, entities=moved["entities"])
    inspected = EngineeringAgent({"tools": {}}).execute_operation("geometry.dxf_inspect", {"input_file": str(target)})
    assert inspected["geometry"]["entity_count"] == 4


def test_dxf_blocks_hatches_coordinate_systems_and_topology(tmp_path):
    path = tmp_path / "rich.dxf"
    write_dxf(
        path,
        hatches=[{"layer": "FILL", "pattern": "ANSI31", "points": [(0, 0), (2, 0), (2, 2), (0, 2)]}],
        inserts=[{"layer": "INST", "block_name": "BOLT", "x": 5, "y": 6}],
        blocks=[{"name": "BOLT", "base_point": {"x": 0, "y": 0, "z": 0}, "entities": [{"type": "CIRCLE", "layer": "0", "center": {"x": 0, "y": 0, "z": 0}, "radius": 1}]}],
        coordinate_systems=[{"name": "UCS", "origin": {"x": 10, "y": 20, "z": 0}, "x_axis": {"x": 0, "y": 1, "z": 0}, "y_axis": {"x": -1, "y": 0, "z": 0}}],
    )
    document = read_dxf(path)
    assert "BOLT" in document["blocks"]
    assert document["entities"][0]["type"] == "HATCH"
    assert any(item["name"] == "UCS" for item in document["coordinate_systems"])
    assert document["topology"]["relations"][0]["to"] == "block:BOLT"
    asset = EngineeringAgent({"tools": {}}).ingest(str(path))
    assert asset["geometry"]["blocks"]["BOLT"]["entity_count"] == 1


def test_generate_and_write_asset_release_transaction_lock(tmp_path):
    agent = EngineeringAgent({"tools": {}})
    generated = agent.generate(str(tmp_path), {"name": "transactional"})
    assert all(Path(path).is_file() for path in generated.values())
    assert not (tmp_path / ".engineering-agent" / "project.lock").exists()
    target = tmp_path / "nested" / "payload.json"
    written = agent.write_asset(str(target), "json", {"ok": True})
    assert Path(written) == target
    assert json.loads(target.read_text(encoding="utf-8"))["ok"] is True
    assert not (tmp_path / ".engineering-agent" / "project.lock").exists()


def test_obj_mesh_ingest_validation_and_viewer(tmp_path):
    mesh = tmp_path / "cube.obj"
    mesh.write_text(
        "\n".join([
            "v 0 0 0", "v 1 0 0", "v 1 1 0", "v 0 1 0", "v 0 0 1", "v 1 0 1", "v 1 1 1", "v 0 1 1",
            "f 1 2 3 4", "f 5 8 7 6", "f 1 5 6 2", "f 2 6 7 3", "f 3 7 8 4", "f 5 1 4 8",
        ]) + "\n",
        encoding="utf-8",
    )
    agent = EngineeringAgent({"tools": {}})
    asset = agent.ingest(str(mesh))
    assert asset["geometry"]["face_count"] == 6
    assert agent.execute_operation("geometry.mesh_validate", {"input_file": str(mesh)})["validation"]["valid"]
    from engineering_agent.viewer import asset_view
    assert "OBJ mesh wireframe" in asset_view(str(tmp_path), "cube.obj")["svg"]
    assert "engineering-3d-viewer" in asset_view(str(tmp_path), "cube.obj")["viewer"]


def test_large_offline_fixture_roundtrip(tmp_path):
    mesh = tmp_path / "large.obj"
    vertices = [(index, index % 17, index % 5) for index in range(1200)]
    faces = [(index, index + 1, index + 2) for index in range(0, 1197, 3)]
    EngineeringAgent({"tools": {}}).write_asset(str(mesh), "obj", {"vertices": vertices, "faces": faces})
    asset = EngineeringAgent({"tools": {}}).ingest(str(mesh))
    assert asset["geometry"]["vertex_count"] == 1200
    assert asset["geometry"]["face_count"] == 399


def test_brep_constraint_solver_and_backend_status(tmp_path):
    agent = EngineeringAgent({"tools": {}})
    solved = agent.execute_operation(
        "brep.solve_constraints",
        {"parameters": {"width": 5, "height": 2}, "constraints": [{"type": "min", "left": "width", "value": 10}, {"type": "distance", "left": "length", "right": "width", "value": 3}]},
    )
    assert solved["converged"] is True
    assert solved["parameters"]["width"] == 10
    assert solved["parameters"]["length"] == 13
    doctor = agent.doctor()
    brep = next(item for item in doctor if item["tool"] == "step-brep")
    assert "available" in brep
    windows = agent.validate_windows_backends()
    assert len(windows["tools"]) == 4
    if windows["platform"].lower() != "windows":
        assert windows["ready_count"] == 0


def test_brep_operation_reports_kernel_or_input_error(tmp_path):
    agent = EngineeringAgent({"tools": {}})
    step = tmp_path / "part.step"
    write_step(step, [(0, 0, 0), (1, 1, 1)])
    try:
        agent.execute_operation("brep.inspect_step", {"input_file": str(step)})
    except RuntimeError as exc:
        message = str(exc)
        assert "OPENCASCADE_BACKEND_UNAVAILABLE" in message or "STEP_" in message


def test_brep_tessellate_step_exports_and_validates_display_mesh(tmp_path, monkeypatch):
    class FakeBackend:
        name = "test-brep"

        def tessellate_step(self, path, deflection):
            assert Path(path).is_file()
            assert deflection == pytest.approx(0.25)
            return {"vertices": [{"x": 0, "y": 0, "z": 0}, {"x": 1, "y": 0, "z": 0}, {"x": 0, "y": 1, "z": 0}],
                    "faces": [[0, 1, 2]], "deflection": deflection}

    step = tmp_path / "part.step"
    step.write_text("placeholder", encoding="utf-8")
    output = tmp_path / "preview.obj"
    monkeypatch.setattr("engineering_agent.agent.get_brep_backend", lambda: FakeBackend())
    result = EngineeringAgent({"tools": {}}).execute_operation("brep.tessellate_step", {
        "project_dir": str(tmp_path), "input_file": "part.step", "output_file": "preview.obj", "deflection": 0.25,
    })
    assert output.is_file()
    assert result["validation"]["valid"] is True
    assert result["persisted_validation"]["valid"] is True
    assert result["mesh"]["triangle_count"] == 1

    glb_result = EngineeringAgent({"tools": {}}).execute_operation("brep.tessellate_step", {
        "project_dir": str(tmp_path), "input_file": "part.step", "output_file": "preview.glb", "deflection": 0.25,
    })
    assert (tmp_path / "preview.glb").is_file()
    assert glb_result["persisted_validation"]["valid"] is True


def test_opencascade_brep_primitive_and_boolean_when_available(tmp_path):
    backend = get_brep_backend()
    if backend.name != "opencascade":
        pytest.skip("OpenCascade binding is optional")
    left = tmp_path / "left.step"
    right = tmp_path / "right.step"
    fused = tmp_path / "fused.step"
    backend.create_primitive("box", {"length": 10, "width": 20, "height": 5}, left)
    backend.create_primitive("box", {"length": 5, "width": 10, "height": 3}, right)
    result = backend.boolean("fuse", left, right, fused)
    assert result["inspection"]["valid"] is True
    assert result["inspection"]["volume"] == pytest.approx(1000, rel=1e-6)
    assert result["inspection"]["topology"]["solid"] == 1


def test_opencascade_additional_primitives_when_available(tmp_path):
    backend = get_brep_backend()
    if backend.name != "opencascade":
        pytest.skip("OpenCascade binding is optional")
    sphere = backend.create_primitive("sphere", {"radius": 3}, tmp_path / "sphere.step")
    cone = backend.create_primitive("cone", {"radius1": 4, "radius2": 1, "height": 5}, tmp_path / "cone.step")
    assert sphere["inspection"]["valid"] is True
    assert sphere["inspection"]["volume"] == pytest.approx(4 / 3 * 3.141592653589793 * 27, rel=1e-6)
    assert cone["inspection"]["valid"] is True
    assert cone["inspection"]["volume"] > 0


def test_brep_feature_graph_rebuilds_parameterized_boolean_model(tmp_path):
    backend = get_brep_backend()
    if backend.name != "opencascade":
        pytest.skip("OpenCascade binding is optional")
    output = tmp_path / "feature-graph.step"
    result = EngineeringAgent({"tools": {}}).execute_operation("brep.build_feature_graph", {
        "project_dir": str(tmp_path), "output_file": str(output),
        "parameters": {"length": 8, "width": 20},
        "constraints": [{"type": "min", "left": "length", "value": 10}],
        "features": [
            {"id": "base", "type": "box", "parameters": {"length": "length", "width": "width", "height": 5}},
            {"id": "tool", "type": "cylinder", "parameters": {"radius": 2, "height": 5}},
            {"id": "result", "type": "cut", "left": "base", "right": "tool"},
        ],
    })
    assert result["inspection"]["valid"] is True
    assert result["constraints"]["parameters"]["length"] == 10
    assert result["final_feature"] == "result"
    assert output.is_file()
    assert Path(str(output) + ".brep.json").is_file()


def test_brep_write_rejects_paths_outside_project(tmp_path):
    outside = tmp_path.parent / "outside.step"
    with pytest.raises(ValueError, match="PATH_OUTSIDE_PROJECT"):
        EngineeringAgent({"tools": {}}).execute_operation("brep.create_primitive", {
            "project_dir": str(tmp_path), "primitive": "box", "output_file": str(outside),
            "parameters": {"length": 1, "width": 1, "height": 1},
        })


def test_brep_persisted_validation_failure_restores_previous_file(tmp_path, monkeypatch):
    class FakeBackend:
        name = "test-brep"

        def create_primitive(self, primitive, parameters, output):
            Path(output).write_text("new-shape", encoding="utf-8")
            return {
                "backend": self.name,
                "primitive": primitive,
                "output": str(output),
                "inspection": {"valid": True, "is_null": False, "volume": 1.0, "topology": {"solid": 1}},
            }

        def inspect_step(self, path):
            return {"valid": False, "is_null": True, "volume": 0.0, "topology": {}}

    output = tmp_path / "part.step"
    output.write_text("previous-shape", encoding="utf-8")
    monkeypatch.setattr("engineering_agent.agent.get_brep_backend", lambda: FakeBackend())
    with pytest.raises(ValueError, match="BREP_PERSISTED_VALIDATION_FAILED"):
        EngineeringAgent({"tools": {}}).execute_operation("brep.create_primitive", {
            "project_dir": str(tmp_path), "primitive": "box", "output_file": "part.step",
            "parameters": {"length": 1, "width": 1, "height": 1},
        })
    assert output.read_text(encoding="utf-8") == "previous-shape"


def test_offline_vehicle_chain_commits_cross_tool_artifacts(tmp_path):
    agent = EngineeringAgent({"tools": {}})
    result = agent.run_offline_vehicle_chain(tmp_path, {"name": "offline_suspension"})
    assert result["status"] == "committed"
    assert result["simulation_backend"] == "offline-surrogate"
    assert {stage["tool"] for stage in result["stages"]} == {"catia", "ansys", "adams", "vi-grade"}
    assert all(item["passed"] for item in result["verdicts"])
    assert Path(result["artifacts"]["offline/model.step"]).is_file()
    assert Path(result["artifacts"]["offline/ansys_result.json"]).is_file()
    assert Path(result["artifacts"]["offline/adams_motion.csv"]).is_file()
    assert Path(result["artifacts"]["offline/vigrade_metrics.json"]).is_file()
    assert Path(result["report"]).is_file()


def test_mcp_exposes_offline_vehicle_chain(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full")
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert any(item["name"] == "engineering_offline_run" for item in listed["result"]["tools"])
    called = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "engineering_offline_run", "arguments": {"project_dir": str(tmp_path)}}})
    assert "offline_vehicle_chain" in called["result"]["content"][0]["text"]


def test_web_workspace_shell_and_offline_endpoint(tmp_path):
    server = create_server(EngineeringAgent({"tools": {}}), TaskQueue(tmp_path / "queue.db"), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as response:
            html = response.read().decode("utf-8")
        assert "SimLoom AI" in html
        assert "运行离线闭环" in html
        assert "结果文件监测" in html

        with urllib.request.urlopen(base + "/api/templates", timeout=5) as response:
            templates = json.loads(response.read())
        assert templates["schemaVersion"] == "simloom-ai/web/v1"
        assert any(item["id"] == "vehicle-handling" for item in templates["templates"])

        request = urllib.request.Request(
            base + "/api/offline-run",
            data=json.dumps({"project_dir": str(tmp_path / "workspace"), "model_spec": {"name": "web_demo"}}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read())
        assert result["status"] == "committed"
        assert all(item["passed"] for item in result["verdicts"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dxf_operation_transform_is_transactional(tmp_path):
    source = tmp_path / "source.dxf"
    write_dxf(source, lines=[{"x1": 0, "y1": 0, "x2": 1, "y2": 0}])
    result = EngineeringAgent({"tools": {}}).execute_operation(
        "geometry.dxf_transform",
        {"project_dir": str(tmp_path), "input_file": str(source), "output_file": str(tmp_path / "target.dxf"), "translate": [3, 4, 0]},
    )
    assert Path(result["path"]).is_file()
    assert result["geometry"]["bbox"]["x"]["min"] == 3


def test_transaction_commit_failure_restores_partial_outputs(tmp_path):
    transaction = RunTransaction(tmp_path)
    transaction.begin({"test": "partial-failure"})
    staged = transaction.stage_dir / "ok.txt"
    staged.write_text("new", encoding="utf-8")
    try:
        transaction.commit({"ok.txt": str(staged), "missing.txt": str(transaction.stage_dir / "missing.txt")})
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("commit should fail for a missing staged artifact")
    assert not (tmp_path / "artifacts" / "generated" / "ok.txt").exists()
    assert transaction.state == "failed"
    assert not (tmp_path / ".engineering-agent" / "project.lock").exists()


def test_stale_project_lock_is_recovered(tmp_path):
    lock = tmp_path / ".engineering-agent" / "project.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text('{"pid": 99999999, "run_id": "old"}', encoding="utf-8")
    transaction = RunTransaction(tmp_path)
    transaction.begin({"test": "stale-lock"})
    assert transaction.state == "running"
    transaction.rollback("test cleanup")
