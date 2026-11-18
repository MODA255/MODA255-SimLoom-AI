from engineering_agent.engineering_context import SCHEMA, validate_engineering_context


def test_context_validator_accepts_referenced_native_objects_and_paths():
    result = validate_engineering_context({
        "schema_version": "simloom-ai/engineering-context/v1",
        "software": {"name": "ansys"},
        "native_objects": [{"id": "analysis", "kind": "analysis"}],
        "coordinate_systems": [{"id": "body", "origin": [0, 0, 0]}],
        "load_cases": [{"id": "gravity", "model_reference": "analysis", "coordinate_system": "body"}],
        "model_references": [{"id": "mesh", "path": "inputs/model.cdb"}],
    }, expected_tool="ansys")
    assert result["schema_version"] == SCHEMA
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["counts"]["load_cases"] == 1


def test_context_validator_reports_duplicate_and_missing_references():
    result = validate_engineering_context({
        "schema_version": "simloom-ai/engineering-context/v1",
        "software": {"name": "catia"},
        "native_objects": [{"id": "part"}, {"id": "part"}],
        "load_cases": [{"id": "lc", "model_reference": "missing", "coordinate_system": "cs"}],
    }, expected_tool="ansys")
    codes = {item["code"] for item in result["errors"]}
    assert result["valid"] is False
    assert {"CONTEXT_DUPLICATE_ID", "CONTEXT_TOOL_MISMATCH",
            "CONTEXT_MODEL_REFERENCE_NOT_FOUND", "CONTEXT_COORDINATE_SYSTEM_NOT_FOUND"} <= codes


def test_context_validator_rejects_absolute_and_parent_traversal_paths():
    for path in ("/tmp/model.cdb", "../model.cdb", "C:/model.cdb", "inputs\\model.cdb"):
        result = validate_engineering_context({
            "schema_version": "simloom-ai/engineering-context/v1",
            "model_references": [{"id": "model", "path": path}],
        })
        assert result["valid"] is False
        assert any(item["code"] == "CONTEXT_MODEL_PATH_INVALID" for item in result["errors"])

