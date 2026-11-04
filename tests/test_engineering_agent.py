import json

from engineering_agent import EngineeringAgent
from engineering_agent.mcp_server import MCPServer
from engineering_agent.operations import OperationRegistry
from engineering_agent.validation import validate_plan
from engineering_agent.versioning import VersionStore
from engineering_agent.formats import write_csv, write_dxf, write_json, write_pdf, write_step, write_svg
from engineering_agent.protocol import ErrorCode, ProtocolError, failure
from engineering_agent.validation import validate_asset


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
