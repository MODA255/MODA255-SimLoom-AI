import json
from pathlib import Path

from engineering_agent import EngineeringAgent
from engineering_agent.mcp_server import MCPServer
from engineering_agent.operations import OperationRegistry
from engineering_agent.validation import validate_plan
from engineering_agent.versioning import VersionStore
from engineering_agent.formats import write_csv, write_dxf, write_json, write_pdf, write_step, write_svg
from engineering_agent.dxf import diff_documents, read_dxf, transform_document
from engineering_agent.protocol import ErrorCode, ProtocolError, failure
from engineering_agent.validation import validate_asset
from engineering_agent.transactions import RunTransaction
from engineering_agent.ingest import ArtifactReader


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


def test_mcp_surface_lists_and_calls_tools():
    server = MCPServer(EngineeringAgent({"tools": {}}))
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert any(item["name"] == "engineering_plan" for item in listed["result"]["tools"])
    called = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "engineering_plan", "arguments": {"prompt": "用Adams做动力学"}}})
    assert "adams_simulation" in called["result"]["content"][0]["text"]
    templates = server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/templates/list"})
    assert templates["result"]["resourceTemplates"]
    missing = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "engineering_ingest", "arguments": {"path": str(Path("/does/not/exist"))}}})
    assert missing["error"]["code"] == "RESOURCE_NOT_FOUND"


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
