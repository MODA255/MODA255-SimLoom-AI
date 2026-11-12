"""Fixtures are authored with ezdxf, independently of SimLoom's writer."""
import io
import json
from pathlib import Path

import ezdxf
import pytest
from ezdxf.lldxf.tagwriter import TagWriter
from ezdxf.math import Vec3

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.dxf_validation import inspect_native
from engineering_agent.formats import write_dxf


def edit(path, operation, **arguments):
    return EngineeringAgent().execute_operation(operation, {
        "project_dir": str(path.parent), "input_file": str(path), "output_file": str(path),
        "auto_repair": False, **arguments})


def tags(entity):
    stream = io.StringIO()
    entity.export_dxf(TagWriter(stream, dxfversion=entity.doc.dxfversion))
    return stream.getvalue()


def rich_drawing(path):
    doc = ezdxf.new("R2013", setup=True)
    doc.layers.new("VENDOR", dxfattribs={"color": 4, "linetype": "DASHED", "flags": 1})
    doc.linetypes.new("CUSTOM", dxfattribs={"description": "custom dashed", "pattern": [1., .5, -.5]})
    doc.styles.new("CUSTOM", dxfattribs={"font": "txt", "width": .8})
    doc.appids.new("VENDOR_DATA")
    msp = doc.modelspace()
    line = msp.add_line((0, 0), (10, 0))
    line.set_xdata("VENDOR_DATA", [(1000, "external-data"), (1040, 3.14159)])
    xrecord = line.new_extension_dict().add_xrecord("ENGINEERING")
    xrecord.reset([(1, "retain-this-record"), (90, 42)])
    spline = msp.add_spline([(0, 0), (2, 4), (6, 3), (10, 1)], dxfattribs={"layer": "VENDOR", "linetype": "CUSTOM"})
    text = msp.add_text("  A  ", dxfattribs={"style": "CUSTOM", "height": 2.5})
    block = doc.blocks.new("PART")
    block.add_circle((0, 0), 2)
    block.add_attdef("CODE", (0, 3), height=1.5)
    insert = msp.add_blockref("PART", (15, 4))
    insert.add_auto_attribs({"CODE": "BRACKET-A"})
    anonymous = doc.blocks.new_anonymous_block("U")
    anonymous.add_line((0, 0), (1, 2))
    msp.add_blockref(anonymous.name, (20, 10))
    hatch = msp.add_hatch(color=3)
    hatch.paths.add_polyline_path([(0, 0), (20, 0), (20, 20), (0, 20)], is_closed=True, flags=1)
    hatch.paths.add_polyline_path([(5, 5), (5, 10), (10, 10), (10, 5)], is_closed=True, flags=0)
    circular = msp.add_hatch(color=2)
    edge = circular.paths.add_edge_path(flags=1)
    edge.add_arc((30, 30), 5, 0, 360)
    dimension = msp.add_aligned_dim((0, 0), (10, 0), 4)
    dimension.render()
    sheet = doc.layouts.new("A3")
    sheet.page_setup(size=(420, 297), units="mm")
    paper_text = sheet.add_text("DRAWING A", dxfattribs={"insert": (20, 20), "height": 3})
    viewport = sheet.add_viewport((180, 140), (300, 200), (10, 10), 40)
    doc.saveas(path)
    return {"line": line.dxf.handle, "spline": spline.dxf.handle, "text": text.dxf.handle,
            "insert": insert.dxf.handle, "hatch": hatch.dxf.handle, "circular": circular.dxf.handle,
            "dimension": dimension.dimension.dxf.handle, "paper_text": paper_text.dxf.handle,
            "viewport": viewport.dxf.handle, "xrecord": xrecord.dxf.handle}


def test_external_drawing_preserves_resources_layouts_attributes_and_vendor_data(tmp_path):
    path = tmp_path / "supplier.dxf"
    handles = rich_drawing(path)
    before = ezdxf.readfile(path)
    saved = {key: tags(before.entitydb[handle]) for key, handle in handles.items() if key != "line"}
    layer_tags = tags(before.layers.get("VENDOR"))
    linetype_tags = tags(before.linetypes.get("CUSTOM"))
    style_tags = tags(before.styles.get("CUSTOM"))
    result = edit(path, "cad.entity_move", entity_id=handles["line"], delta=[3, 4, 0])
    assert result["persisted_validation"]["native"]["valid"]
    after = ezdxf.readfile(path)
    assert after.entitydb[handles["line"]].dxf.start == Vec3(3, 4, 0)
    assert after.entitydb[handles["line"]].get_xdata("VENDOR_DATA") == before.entitydb[handles["line"]].get_xdata("VENDOR_DATA")
    for key, expected in saved.items():
        assert tags(after.entitydb[handles[key]]) == expected, key
    assert tags(after.layers.get("VENDOR")) == layer_tags
    assert tags(after.linetypes.get("CUSTOM")) == linetype_tags
    assert tags(after.styles.get("CUSTOM")) == style_tags
    assert after.layers.get("VENDOR").is_frozen()
    assert after.layers.get("VENDOR").rgb is None
    Path(str(path) + ".cad.json").unlink()
    assert CadDocument.open(path).sheets["A3"]["width"] == 420
    assert len(after.modelspace().query("INSERT")[0].attribs) == 1


def test_hatch_holes_and_arc_edges_move_without_collapsing_paths(tmp_path):
    path = tmp_path / "holes.dxf"
    handles = rich_drawing(path)
    edit(path, "cad.entity_move", entity_ids=[handles["hatch"], handles["circular"]], delta=[7, 9, 0])
    native = ezdxf.readfile(path)
    hatch = native.entitydb[handles["hatch"]]
    assert len(hatch.paths) == 2
    assert hatch.paths[0].vertices[0][:2] == (7, 9)
    assert hatch.paths[1].vertices[0][:2] == (12, 14)
    edge = native.entitydb[handles["circular"]].paths[0].edges[0]
    assert tuple(edge.center) == (37, 39)
    assert edge.radius == 5


def test_insert_attributes_move_with_block_and_keep_identity(tmp_path):
    path = tmp_path / "attributes.dxf"
    handles = rich_drawing(path)
    before = ezdxf.readfile(path).entitydb[handles["insert"]]
    attrib = before.attribs[0]
    edit(path, "cad.entity_move", entity_id=handles["insert"], delta=[4, 6, 0])
    after = ezdxf.readfile(path).entitydb[handles["insert"]]
    assert after.attribs[0].dxf.handle == attrib.dxf.handle
    assert after.attribs[0].dxf.insert.isclose(attrib.dxf.insert + Vec3(4, 6, 0))
    assert after.attribs[0].dxf.text == "BRACKET-A"


def test_dimension_move_and_rotate_updates_real_definition_and_graphics(tmp_path):
    path = tmp_path / "dimensions.dxf"
    handles = rich_drawing(path)
    edit(path, "cad.entity_rotate", entity_id=handles["dimension"], angle=90, center={"x": 0, "y": 0})
    edit(path, "cad.entity_move", entity_id=handles["dimension"], delta=[2, 3, 0])
    native = ezdxf.readfile(path)
    dimension = native.entitydb[handles["dimension"]]
    assert dimension.dxf.defpoint2.isclose(Vec3(2, 3))
    assert dimension.dxf.defpoint3.isclose(Vec3(2, 13))
    assert dimension.get_measurement() == pytest.approx(10)
    assert len(dimension.get_geometry_block()) > 0
    assert inspect_native(path)["valid"]


def test_bulges_widths_and_arc_angles_transform_using_native_geometry(tmp_path):
    path = tmp_path / "curves.dxf"
    doc = ezdxf.new("R2013")
    poly = doc.modelspace().add_lwpolyline([(0, 0, 1, 2, 0), (3, 0, 2, 3, 1), (3, 3, 0, 0, 0)], format="xyseb", close=True)
    arc = doc.modelspace().add_arc((0, 0), 5, 10, 70)
    doc.saveas(path)
    edit(path, "cad.entity_rotate", entity_ids=[poly.dxf.handle, arc.dxf.handle], angle=90)
    native = ezdxf.readfile(path)
    assert [v[4] for v in native.entitydb[poly.dxf.handle].get_points()] == [0, 1, 0]
    assert [v[2:4] for v in native.entitydb[poly.dxf.handle].get_points()] == [(1, 2), (2, 3), (0, 0)]
    assert native.entitydb[arc.dxf.handle].dxf.start_angle == pytest.approx(100)
    assert native.entitydb[arc.dxf.handle].dxf.end_angle == pytest.approx(160)


def test_stale_sidecar_and_reordered_entities_cannot_reassign_ids(tmp_path):
    path = tmp_path / "ids.dxf"
    write_dxf(path, lines=[{"x1": 0, "y1": 0, "x2": 1, "y2": 0}, {"x1": 0, "y1": 5, "x2": 1, "y2": 5}])
    edit(path, "cad.selection_create", name="selected", entity_ids=["e00001"])
    doc = ezdxf.readfile(path)
    first = list(doc.modelspace())[0]
    doc.modelspace().unlink_entity(first)
    doc.modelspace().add_entity(first)
    doc.saveas(path)
    document = CadDocument.open(path)
    assert document.metadata["sidecar_warning"] == "CAD_SIDECAR_STALE"
    assert document.entities[0]["id"] == "e00002"
    assert document.entity("e00001")["start"]["y"] == 0
    assert not document.selection_sets


def test_native_audit_detects_wrong_numeric_dimension_text_without_writing(tmp_path):
    path = tmp_path / "wrong.dxf"
    doc = ezdxf.new("R2013", setup=True)
    dimension = doc.modelspace().add_aligned_dim((0, 0), (10, 0), 3, text="99.00")
    dimension.render()
    doc.saveas(path)
    before = path.read_bytes()
    report = edit(path, "cad.audit")["validation"]
    assert not report["valid"]
    assert "DIMENSION_TEXT_MISMATCH" in {e["code"] for e in report["errors"]}
    assert path.read_bytes() == before


def test_silent_writer_geometry_loss_rolls_back_files_and_history(tmp_path, monkeypatch):
    import engineering_agent.cad_operations as operations
    path = tmp_path / "rollback.dxf"
    write_dxf(path, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0}])
    edit(path, "cad.entity_move", entity_id="e00001", delta=[1, 0, 0])
    sidecar = Path(str(path) + ".cad.json")
    history = next((tmp_path / ".engineering-agent/cad-history").glob("*.json"))
    before = [p.read_bytes() for p in (path, sidecar, history)]
    writer = operations.write_native
    def corrupt(target, **arguments):
        writer(target, **arguments)
        native = ezdxf.readfile(target)
        native.modelspace().query("LINE")[0].dxf.end = (900, 900, 0)
        native.saveas(target)
    monkeypatch.setattr(operations, "write_native", corrupt)
    with pytest.raises(ValueError, match="NATIVE_GEOMETRY_MISMATCH"):
        edit(path, "cad.entity_move", entity_id="e00001", delta=[2, 0, 0])
    assert [p.read_bytes() for p in (path, sidecar, history)] == before


def test_purge_keeps_nested_block_dependencies_and_layers(tmp_path):
    path = tmp_path / "nested.dxf"
    doc = ezdxf.new("R2013")
    doc.layers.new("CHILD_LAYER")
    doc.layers.new("UNUSED")
    child = doc.blocks.new("CHILD")
    child.add_circle((0, 0), 1, dxfattribs={"layer": "CHILD_LAYER"})
    parent = doc.blocks.new("PARENT")
    parent.add_blockref("CHILD", (0, 0))
    doc.modelspace().add_blockref("PARENT", (1, 2))
    doc.blocks.new("UNUSED_BLOCK")
    doc.saveas(path)
    edit(path, "cad.entity_purge")
    native = ezdxf.readfile(path)
    assert "CHILD" in native.blocks and "PARENT" in native.blocks
    assert "CHILD_LAYER" in native.layers
    assert "UNUSED" not in native.layers and "UNUSED_BLOCK" not in native.blocks


def test_viewport_and_sheet_deletions_change_native_layouts(tmp_path):
    path = tmp_path / "layouts.dxf"
    handles = rich_drawing(path)
    edit(path, "cad.viewport_delete", name=handles["viewport"])
    assert not [v for v in ezdxf.readfile(path).layouts.get("A3").query("VIEWPORT") if v.dxf.id != 1]
    edit(path, "cad.sheet_delete", name="A3")
    assert "A3" not in ezdxf.readfile(path).layouts


def test_binary_dxf_is_edited_as_a_native_drawing(tmp_path):
    path = tmp_path / "binary.dxf"
    doc = ezdxf.new("R2013")
    line = doc.modelspace().add_line((1, 2), (3, 4))
    doc.saveas(path, fmt="bin")
    edit(path, "cad.entity_move", entity_id=line.dxf.handle, delta=[2, 3, 0])
    assert ezdxf.readfile(path).entitydb[line.dxf.handle].dxf.start == Vec3(3, 5)


def test_r12_version_and_2d_polyline_survive_move(tmp_path):
    path = tmp_path / "r12.dxf"
    doc = ezdxf.new("R12")
    poly = doc.modelspace().add_polyline2d([(0, 0), (5, 0), (5, 5)], close=True)
    poly.vertices[1].dxf.bulge = .5
    doc.saveas(path)
    edit(path, "cad.entity_move", entity_id=poly.dxf.handle, delta=[2, 3, 0])
    native = ezdxf.readfile(path)
    assert native.dxfversion == "AC1009"
    actual = native.entitydb[poly.dxf.handle]
    assert actual.vertices[0].dxf.location == Vec3(2, 3)
    assert actual.vertices[1].dxf.bulge == .5
    assert actual.is_closed


def test_native_audit_reports_repairs_instead_of_accepting_healed_file(tmp_path):
    path = tmp_path / "bad-style.dxf"
    doc = ezdxf.new("R2013")
    doc.modelspace().add_line((0, 0), (5, 0), dxfattribs={"linetype": "NON_EXISTENT"})
    doc.saveas(path)
    before = path.read_bytes()
    report = inspect_native(path)
    assert not report["valid"]
    assert report["audit_repair_count"] > 0
    assert path.read_bytes() == before


def test_new_color_attribute_and_inch_layout_survive_sidecar_removal(tmp_path):
    path = tmp_path / "inches.dxf"
    doc = ezdxf.new("R2013")
    doc.units = 1
    line = doc.modelspace().add_line((0, 0), (10, 0))
    sheet = doc.layouts.new("ANSI")
    sheet.page_setup(size=(11, 8.5), units="inch")
    doc.saveas(path)
    edit(path, "cad.entity_update", entity_id=line.dxf.handle, patch={"color_index": 3})
    native = ezdxf.readfile(path)
    assert native.entitydb[line.dxf.handle].dxf.color == 3
    assert native.units == 1
    Path(str(path) + ".cad.json").unlink()
    assert CadDocument.open(path).sheets["ANSI"]["width"] == pytest.approx(11)


def test_hatch_pattern_translation_preserves_custom_line_phase(tmp_path):
    path = tmp_path / "pattern.dxf"
    doc = ezdxf.new("R2013")
    hatch = doc.modelspace().add_hatch()
    hatch.set_pattern_fill("CUSTOM", definition=[[45, (2, 3), (-1, 1), [2, -1]]])
    hatch.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)], is_closed=True)
    doc.saveas(path)
    edit(path, "cad.entity_move", entity_id=hatch.dxf.handle, delta=[4, 5, 0])
    after = ezdxf.readfile(path).entitydb[hatch.dxf.handle]
    assert tuple(after.pattern.lines[0].base_point) == pytest.approx((6, 8))
    assert after.pattern.lines[0].dash_length_items == [2, -1]


def test_spline_transform_keeps_native_identity(tmp_path):
    path = tmp_path / "unsupported.dxf"
    handles = rich_drawing(path)
    edit(path, "cad.entity_move", entity_id=handles["spline"], delta=[1, 0, 0])
    native = ezdxf.readfile(path).entitydb[handles["spline"]]
    assert native.dxf.handle == handles["spline"]
    assert tuple(native.fit_points[0]) == pytest.approx((1, 0, 0))


def test_associative_hatch_transform_moves_boundary_entities_and_keeps_links(tmp_path):
    path = tmp_path / "associative-hatch.dxf"
    doc = ezdxf.new("R2013")
    model = doc.modelspace()
    lines = [model.add_line(start, end) for start, end in [
        ((0, 0), (10, 0)), ((10, 0), (10, 10)), ((10, 10), (0, 10)), ((0, 10), (0, 0))]]
    hatch = model.add_hatch()
    hatch.dxf.associative = 1
    path_data = hatch.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)], is_closed=True, flags=2)
    path_data.source_boundary_objects = [line.dxf.handle for line in lines]
    doc.saveas(path)

    result = edit(path, "cad.entity_move", entity_id=hatch.dxf.handle, delta=[4, 5, 0])
    assert result["persisted_validation"]["valid"]
    native = ezdxf.readfile(path)
    moved_hatch = native.entitydb[hatch.dxf.handle]
    assert moved_hatch.dxf.associative == 1
    assert moved_hatch.paths[0].source_boundary_objects == [line.dxf.handle for line in lines]
    assert moved_hatch.paths[0].vertices[0][:2] == (4, 5)
    assert native.entitydb[lines[0].dxf.handle].dxf.start.isclose(Vec3(4, 5))
