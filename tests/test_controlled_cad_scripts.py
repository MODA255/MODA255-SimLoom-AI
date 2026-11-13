"""Evidence for the opt-in CAD script transaction boundary."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.formats import write_dxf
from engineering_agent.mcp_server import MCPServer


def _source(root: Path) -> Path:
    path = root / "source.dxf"
    write_dxf(path, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0}])
    return path


def _script() -> str:
    return """import json, sys, ezdxf
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
doc = ezdxf.readfile(request['input_file'])
doc.modelspace().add_line((3, 2), (4, 2))
doc.saveas(request['output_file'])
"""


def test_cad_script_is_disabled_by_default(tmp_path):
    source = _source(tmp_path)
    with pytest.raises(ValueError, match="CAD_SCRIPTS_DISABLED"):
        EngineeringAgent({"tools": {}}).execute_operation("cad.script_apply", {
            "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(tmp_path / "out.dxf"),
            "script": _script(),
        })


def test_mcp_full_profile_exposes_script_policy_and_error_code(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full")
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"limit": 500}})
    tool = next(item for item in listed["result"]["tools"] if item["name"] == "operation.cad.script_apply")
    assert tool["inputSchema"]["properties"]["allow_scripts"]["default"] is False
    source = _source(tmp_path)
    called = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "operation.cad.script_apply", "arguments": {
            "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(tmp_path / "out.dxf"),
            "script": _script(),
        }}})
    assert called["result"]["isError"] is True
    assert called["result"]["structuredContent"]["error"]["code"] == "CAD_SCRIPTS_DISABLED"


def test_cad_script_is_one_validated_diff_and_history_revision(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "exports" / "out.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.script_apply", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
        "script": _script(), "allow_scripts": True, "parameters": {"case": "bracket"},
    })
    assert result["validation"]["valid"]
    assert result["persisted_validation"]["valid"]
    assert result["diff"]["change_count"] > 0
    assert result["history"]["can_undo"]
    assert output.is_file() and Path(str(output) + ".cad.json").is_file()
    assert result["script_execution"]["policy"]["valid"]


def test_cad_script_file_receives_request_and_keeps_project_isolated(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "out.dxf"
    script = tmp_path / "scripts" / "edit.py"
    script.parent.mkdir()
    script.write_text(_script(), encoding="utf-8")
    result = EngineeringAgent({"cad": {"allow_scripts": True}, "tools": {}}).execute_operation("cad.script_apply", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
        "script_file": "scripts/edit.py",
    })
    assert result["script"] == "scripts/edit.py"
    assert output.is_file()


def test_cad_script_cannot_escape_workspace(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "out.dxf"
    before = source.read_bytes()
    script = """from pathlib import Path
Path('/tmp/simloom-escape-test.txt').write_text('should fail')
"""
    with pytest.raises(ValueError, match="CAD_SCRIPT_PATH_OUTSIDE_WORKSPACE"):
        EngineeringAgent({"tools": {}}).execute_operation("cad.script_apply", {
            "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
            "script": script, "allow_scripts": True,
        })
    assert source.read_bytes() == before
    assert not output.exists()


def test_cad_script_timeout_rolls_back_without_output(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "out.dxf"
    with pytest.raises(ValueError, match="CAD_SCRIPT_TIMEOUT"):
        EngineeringAgent({"tools": {}}).execute_operation("cad.script_apply", {
            "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
            "script": "while True:\n    pass\n", "allow_scripts": True, "timeout_seconds": 1,
        })
    assert not output.exists()


def test_cad_visual_diff_returns_entity_overlay(tmp_path):
    source = _source(tmp_path)
    edited = tmp_path / "edited.dxf"
    agent = EngineeringAgent({"tools": {}})
    agent.execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(edited),
        "entity_ids": ["e00001"], "delta": [3, 1, 0],
    })
    result = agent.execute_operation("cad.visual_diff", {
        "project_dir": str(tmp_path), "input_file": str(source), "right_file": str(edited),
    })
    assert result["visual_diff"]["counts"]["changed"] == 1
    assert "data-cad-visual-diff='v1'" in result["visual_diff"]["svg"]
    assert "data-diff-kind='removed'" in result["visual_diff"]["svg"]
    assert "data-diff-kind='changed'" in result["visual_diff"]["svg"]

