from pathlib import Path

from engineering_agent import EngineeringAgent


def test_extract_requirements_reads_plate_facts_without_guessing(tmp_path: Path):
    document = tmp_path / "requirements.md"
    document.write_text("plate width 120 mm, height 70 mm, 4 holes diameter 10 mm, edge offset 12 mm", encoding="utf-8")
    result = EngineeringAgent({"tools": {}}).execute_operation("project.extract_requirements", {
        "project_dir": str(tmp_path), "document": document.name,
    })
    assert result["facts"] == {"kind": "plate", "units": "mm", "width": 120.0,
                                "height": 70.0, "hole_diameter": 10.0, "edge_offset": 12.0}
    assert result["confidence"] > 0.8
