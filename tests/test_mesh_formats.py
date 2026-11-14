from __future__ import annotations

from engineering_agent import EngineeringAgent
from engineering_agent.mesh import parse_vtk, parse_vtu, read_mesh
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
    assert "VTU mesh wireframe" in asset_view(str(tmp_path), path.name)["svg"]
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
