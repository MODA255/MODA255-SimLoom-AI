from __future__ import annotations

from pathlib import Path

from engineering_agent import EngineeringAgent
from engineering_agent.mesh import parse_vtk, parse_vtu, read_mesh, write_glb
from engineering_agent.viewer import asset_view


VTK = """# vtk DataFile Version 3.0
triangle with result
ASCII
DATASET POLYDATA
POINTS 3 float
0 0 0  1 0 0  0 1 0
POLYGONS 1 4
3 0 1 2
POINT_DATA 3
SCALARS stress float 1
LOOKUP_TABLE default
10 20 30
"""


VTU = """<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">
  <UnstructuredGrid><Piece NumberOfPoints="4" NumberOfCells="1">
    <Points><DataArray type="Float32" NumberOfComponents="3" format="ascii">0 0 0 1 0 0 1 1 0 0 1 0</DataArray></Points>
    <Cells>
      <DataArray type="Int32" Name="connectivity" format="ascii">0 1 2 3</DataArray>
      <DataArray type="Int32" Name="offsets" format="ascii">4</DataArray>
      <DataArray type="UInt8" Name="types" format="ascii">9</DataArray>
    </Cells>
    <PointData><DataArray type="Float32" Name="temperature" format="ascii">1 2 3 4</DataArray></PointData>
  </Piece></UnstructuredGrid>
</VTKFile>
"""


def test_legacy_vtk_mesh_and_node_result_field():
    mesh = parse_vtk(VTK)
    assert mesh["format"] == "VTK"
    assert mesh["face_count"] == 1
    assert mesh["scalar_name"] == "stress"
    assert mesh["scalar_range"] == [10.0, 30.0]


def test_vtu_mesh_and_node_result_field(tmp_path):
    path = tmp_path / "plate.vtu"
    path.write_text(VTU, encoding="utf-8")
    mesh = parse_vtu(VTU)
    assert mesh["format"] == "VTU"
    assert mesh["face_count"] == 1 and mesh["scalar_name"] == "temperature"
    asset = EngineeringAgent().ingest(str(path))
    assert asset["geometry"]["vertex_count"] == 4
    view = asset_view(str(tmp_path), path.name)
    assert "VTU mesh wireframe" in view["svg"]
    assert "temperature" in view["viewer"]
    assert "linear-gradient" in view["viewer"]
    assert read_mesh(path)["scalar_range"] == [1.0, 4.0]


def test_vtk_and_vtu_mesh_validation_is_clean(tmp_path):
    vtk_path = tmp_path / "triangle.vtk"
    vtk_path.write_text(VTK, encoding="utf-8")
    vtu_path = tmp_path / "plate.vtu"
    vtu_path.write_text(VTU, encoding="utf-8")

    for path in (vtk_path, vtu_path):
        result = EngineeringAgent().execute_operation(
            "geometry.mesh_validate", {"input_file": str(path)}
        )
        assert result["validation"]["valid"]
        assert "UNKNOWN_GEOMETRY_FORMAT" not in result["validation"]["warnings"]


def test_glb_round_trip_and_viewer_ingest(tmp_path):
    path = tmp_path / "result.glb"
    write_glb(path,
              [{"x": 0, "y": 0, "z": 0}, {"x": 1, "y": 0, "z": 0}, {"x": 0, "y": 1, "z": 0}],
              [[0, 1, 2]], colors=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
              scalar_name="stress", scalar_range=[0, 10])
    mesh = read_mesh(path)
    assert mesh["format"] == "GLB"
    assert mesh["face_count"] == 1
    assert mesh["colors"][0] == [1.0, 0.0, 0.0]
    assert mesh["scalar_name"] == "stress"
    asset = EngineeringAgent().ingest(str(path))
    assert asset["geometry"]["format"] == "GLB"
    view = asset_view(str(tmp_path), path.name)
    assert "engineering-3d-viewer" in view["viewer"]
    assert "stress" in view["viewer"] and "linear-gradient" in view["viewer"]


def test_glb_uses_common_artifact_write_transaction(tmp_path):
    path = tmp_path / "written.glb"
    result = EngineeringAgent().execute_operation("artifact.write", {
        "project_dir": str(tmp_path), "path": "written.glb", "format": "glb",
        "payload": {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, 2]],
                    "scalar_name": "temperature", "scalar_range": [1, 4]},
    })
    assert Path(result["path"]) == path
    assert read_mesh(path)["scalar_name"] == "temperature"
