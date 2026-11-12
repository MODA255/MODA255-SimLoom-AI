"""Independent geometry and engineering-drawing validation checks."""
from __future__ import annotations

from engineering_agent.cad_model import CadDocument, validate_cad_document


def _document(*entities):
    return CadDocument.from_dxf({"entities": list(entities), "layers": {"0": {}}}, name="validation.dxf")


def test_validator_reports_duplicate_geometry_without_rejecting_by_default():
    document = _document(
        {"id": "a", "type": "LINE", "layer": "0", "start": {"x": 0, "y": 0}, "end": {"x": 10, "y": 0}},
        {"id": "b", "type": "LINE", "layer": "0", "start": {"x": 0, "y": 0}, "end": {"x": 10, "y": 0}},
    )
    result = validate_cad_document(document)
    assert result["valid"]
    assert result["duplicate_geometry"] == [{"entity_ids": ["a", "b"], "count": 2}]
    strict = validate_cad_document(document, {"reject_duplicate_geometry": True})
    assert not strict["valid"]
    assert strict["errors"][0]["code"] == "DUPLICATE_GEOMETRY"


def test_validator_checks_text_readability_and_closed_loop_area():
    document = _document(
        {"id": "text", "type": "TEXT", "layer": "0", "insert": {"x": 0, "y": 0}, "text": "label", "height": 0.5},
        {"id": "loop", "type": "LWPOLYLINE", "layer": "0", "closed": True,
         "vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 2, "y": 0}]},
    )
    result = validate_cad_document(document, {"min_text_height": 1})
    codes = {error["code"] for error in result["errors"]}
    assert "TEXT_TOO_SMALL" in codes
    assert "CLOSED_POLYLINE_DEGENERATE" in codes


def test_validator_can_require_dimension_association():
    document = _document(
        {"id": "dimension", "type": "DIMENSION", "layer": "0", "definition": {"x": 50, "y": 50}, "measurement": 10},
    )
    result = validate_cad_document(document, {"require_dimension_association": True})
    assert not result["valid"]
    assert result["errors"][0]["code"] == "DIMENSION_NOT_ASSOCIATED"


def test_validator_reports_dimension_crossing_and_can_reject_it():
    document = _document(
        {"id": "d1", "type": "DIMENSION", "layer": "0", "start": {"x": 0, "y": 0}, "end": {"x": 10, "y": 10}, "definition": {"x": 0, "y": 0}, "measurement": 14},
        {"id": "d2", "type": "DIMENSION", "layer": "0", "start": {"x": 0, "y": 10}, "end": {"x": 10, "y": 0}, "definition": {"x": 0, "y": 10}, "measurement": 14},
    )
    result = validate_cad_document(document)
    assert any(item.startswith("DIMENSION_CROSSING:") for item in result["warnings"])
    strict = validate_cad_document(document, {"reject_drafting_warnings": True})
    assert not strict["valid"]
    assert any(item["code"] == "DIMENSION_CROSSING" for item in strict["errors"])

