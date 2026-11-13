"""File and pixel evidence for editable title blocks and physical drawing export."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import ezdxf
from PIL import Image
from pypdf import PdfReader
import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.plotting import validate_plot


def operation(path, operation_name, **kwargs):
    return EngineeringAgent().execute_operation(operation_name, {
        "project_dir": str(path.parent), "input_file": path.name, "output_file": path.name, "auto_repair": False, **kwargs})


@pytest.fixture
def drawing(tmp_path):
    path = tmp_path / "bracket.dxf"
    doc = ezdxf.new("R2013", setup=True)
    doc.units = 4
    space = doc.modelspace()
    space.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True)
    for center in [(10, 10), (90, 10), (90, 50), (10, 50)]: space.add_circle(center, 4)
    dimension = space.add_aligned_dim((0, 0), (100, 0), -10)
    dimension.render()
    doc.saveas(path)
    return path


def sheet(drawing):
    return operation(drawing, "cad.sheet_prepare", name="A3", paper="A3", fields={"TITLE": "Mounting bracket", "NUMBER": "SL-101", "REV": "A"})


def plot(path, suffix="pdf", **kwargs):
    return operation(path, "cad.plot", output_file="exports/drawing." + suffix, **kwargs)


def test_native_title_fields_survive_sidecar_removal_and_edit_with_stable_handles(drawing):
    result = sheet(drawing)
    assert result["persisted_validation"]["valid"]
    native = ezdxf.readfile(drawing)
    insert = native.layouts.get("A3").query("INSERT")[0]
    handle = insert.dxf.handle
    handles = {a.dxf.tag: a.dxf.handle for a in insert.attribs}
    assert {e.dxf.tag for e in native.blocks.get(insert.dxf.name).query("ATTDEF")} == set(handles)
    # Opening the DXF in a fresh project must not depend on our sidecar/history.
    # Removing a tracked sidecar in place correctly conflicts with old history.
    imported = drawing.parent / "supplier.dxf"
    imported.write_bytes(drawing.read_bytes())
    drawing = imported
    opened = CadDocument.open(drawing)
    assert opened.sheets["A3"]["title_block"]["fields"]["NUMBER"] == "SL-101"
    assert next(e for e in opened.entities if e.get("title_block_id"))["title_field_widths"]["TITLE"] > 170
    updated = operation(drawing, "cad.title_block_update", sheet="A3", fields={"TITLE": "Support <B>", "REV": "B"})
    assert updated["persisted_validation"]["valid"]
    after = ezdxf.readfile(drawing).entitydb[handle]
    assert {a.dxf.tag: a.dxf.handle for a in after.attribs} == handles
    assert after.get_attrib("REV").dxf.text == "B"
    assert after.get_attrib("TITLE").dxf.text == "Support <B>"


def test_sheet_creation_is_one_undoable_edit_and_update_does_not_duplicate(drawing):
    before = drawing.read_bytes()
    sheet(drawing)
    created = drawing.read_bytes()
    operation(drawing, "cad.undo")
    assert drawing.read_bytes() == before
    operation(drawing, "cad.redo")
    assert drawing.read_bytes() == created
    operation(drawing, "cad.title_block_update", sheet="A3", fields={"REV": "B"})
    assert len(ezdxf.readfile(drawing).layouts.get("A3").query("INSERT")) == 1
    operation(drawing, "cad.undo")
    assert drawing.read_bytes() == created


@pytest.mark.parametrize("operation_name,arguments,match", [
    ("cad.title_block_create", {"sheet": "A3", "fields": {"TITLE": "duplicate"}}, "TITLE_BLOCK_EXISTS"),
    ("cad.title_block_update", {"sheet": "A3", "fields": {"UNKNOWN": "x"}}, "TITLE_FIELD_NOT_FOUND"),
    ("cad.sheet_prepare", {"name": "A3"}, "SHEET_NAME_INVALID_OR_EXISTS"),
])
def test_invalid_title_edits_leave_drawing_unchanged(drawing, operation_name, arguments, match):
    sheet(drawing)
    before = drawing.read_bytes()
    with pytest.raises(ValueError, match=match): operation(drawing, operation_name, **arguments)
    assert drawing.read_bytes() == before


def test_pdf_is_vector_drawing_on_exact_a3_sheet(drawing):
    sheet(drawing)
    before = drawing.read_bytes()
    sidecar_before = Path(str(drawing) + ".cad.json").read_bytes()
    result = plot(drawing, layout="A3")
    reader = PdfReader(result["path"], strict=True)
    page = reader.pages[0]
    assert len(reader.pages) == 1
    assert float(page.mediabox.width) * 25.4 / 72 == pytest.approx(420, abs=.01)
    assert float(page.mediabox.height) * 25.4 / 72 == pytest.approx(297, abs=.01)
    operators = [operator for _, operator in page.get_contents().operations]
    assert operators.count(b"m") > 10 and b"c" in operators  # real lines, circular curves and glyph paths
    assert not result["report"]["warnings"]
    assert result["report"]["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert drawing.read_bytes() == before
    assert Path(str(drawing) + ".cad.json").read_bytes() == sidecar_before
    assert Path(result["report_path"]).is_file()


def test_svg_is_vector_drawing_on_exact_a3_sheet(drawing):
    sheet(drawing)
    result = plot(drawing, "svg", layout="A3")
    from xml.etree import ElementTree as ET
    root = ET.parse(result["path"]).getroot()
    assert root.tag.rsplit("}", 1)[-1] == "svg"
    assert result["report"]["format"] == "svg"
    assert result["validation"]["valid"] is True
    assert result["validation"]["page_mm"] == pytest.approx([420, 297], abs=.1)
    assert any(element.tag.rsplit("}", 1)[-1] == "path" for element in root.iter())


def test_multi_page_pdf_uses_independent_model_page_sizes(drawing):
    result = operation(drawing, "cad.plot", output_file="exports/multi-model.pdf", pages=[
        {"layout": "Model", "paper": "A4", "orientation": "portrait", "scale": "fit"},
        {"layout": "Model", "paper": "A3", "orientation": "landscape", "scale": "fit"},
    ])
    reader = PdfReader(result["path"], strict=True)
    assert len(reader.pages) == 2
    sizes = [
        (float(page.mediabox.width) * 25.4 / 72, float(page.mediabox.height) * 25.4 / 72)
        for page in reader.pages
    ]
    assert sizes[0] == pytest.approx((210, 297), abs=.01)
    assert sizes[1] == pytest.approx((420, 297), abs=.01)
    assert result["report"]["multi_page"] is True
    assert result["report"]["page_count"] == 2
    assert [page["page_index"] for page in result["report"]["pages"]] == [1, 2]
    assert result["validation"]["pages"] == 2
    assert result["validation"]["page_mm"][0] == pytest.approx([210, 297], abs=.01)
    assert result["validation"]["page_mm"][1] == pytest.approx([420, 297], abs=.01)


def test_multi_page_pdf_reads_each_paperspace_layout(drawing):
    sheet(drawing)
    second = operation(drawing, "cad.sheet_prepare", name="A4", paper="A4", fields={"TITLE": "Second sheet"})
    assert second["persisted_validation"]["valid"]
    result = operation(drawing, "cad.plot", output_file="exports/multi-layout.pdf", pages=[
        {"layout": "A3"},
        {"layout": "A4"},
    ])
    reader = PdfReader(result["path"], strict=True)
    assert len(reader.pages) == 2
    sizes = [
        (float(page.mediabox.width) * 25.4 / 72, float(page.mediabox.height) * 25.4 / 72)
        for page in reader.pages
    ]
    assert sizes[0] == pytest.approx((420, 297), abs=.01)
    assert sizes[1] == pytest.approx((297, 210), abs=.01)
    assert [page["layout"] for page in result["report"]["pages"]] == ["A3", "A4"]
    assert result["validation"]["pages"] == 2


def test_paperspace_png_contains_model_viewport_and_title_block(drawing):
    sheet(drawing)
    result = plot(drawing, "png", layout="A3", dpi=100)
    with Image.open(result["path"]) as image:
        assert image.width == pytest.approx(420 / 25.4 * 100, abs=1)
        assert image.height == pytest.approx(297 / 25.4 * 100, abs=1)
        gray = image.convert("L")
        top = gray.crop((0, 0, image.width, int(image.height * .65)))
        bottom = gray.crop((int(image.width * .55), int(image.height * .75), image.width, image.height))
        assert sum(n for v, n in enumerate(top.histogram()) if v < 100) > 200
        assert sum(n for v, n in enumerate(bottom.histogram()) if v < 100) > 200


@pytest.mark.parametrize("drawing_units,length,scale,expected_mm", [(4, 100, "1:1", 100), (4, 100, "1:2", 50), (1, 4, "1:1", 101.6)])
def test_numeric_plot_scale_matches_physical_pixel_distance(tmp_path, drawing_units, length, scale, expected_mm):
    path = tmp_path / "scale.dxf"
    doc = ezdxf.new("R2013"); doc.units = drawing_units
    doc.modelspace().add_line((0, 0), (length, 0))
    doc.saveas(path)
    result = plot(path, "png", scale=scale, dpi=127)
    with Image.open(result["path"]) as image:
        gray = image.convert("L")
        pixels = gray.load()
        columns = [x for x in range(gray.width) if any(pixels[x, y] < 100 for y in range(gray.height // 2 - 3, gray.height // 2 + 4))]
        assert max(columns) - min(columns) == pytest.approx(expected_mm * 5, abs=2)


def test_solid_hatch_hole_remains_white_in_export(tmp_path):
    path = tmp_path / "hole.dxf"
    doc = ezdxf.new("R2013"); doc.units = 4
    hatch = doc.modelspace().add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (20, 0), (20, 20), (0, 20)], is_closed=True, flags=1)
    hatch.paths.add_polyline_path([(5, 5), (5, 15), (15, 15), (15, 5)], is_closed=True, flags=0)
    doc.saveas(path)
    result = plot(path, "png", scale="1:1", dpi=254)
    with Image.open(result["path"]) as image:
        cx, cy = image.width // 2, image.height // 2
        gray = image.convert("L")
        assert gray.getpixel((cx, cy)) > 245
        assert gray.getpixel((cx + 75, cy)) < 10
        assert gray.getpixel((cx + 150, cy)) > 245


def test_hidden_and_nonplot_layers_are_excluded_from_plot_bounds(tmp_path):
    path = tmp_path / "layers.dxf"
    doc = ezdxf.new("R2013"); doc.units = 4
    doc.layers.new("HIDDEN").off()
    doc.layers.new("NO_PLOT", dxfattribs={"plot": 0})
    doc.modelspace().add_line((0, 0), (10, 10))
    for layer in ("HIDDEN", "NO_PLOT"):
        doc.modelspace().add_circle((10000, 10000), 100, dxfattribs={"layer": layer})
    doc.saveas(path)
    result = plot(path, "png")
    x0, x1, *_ = result["report"]["world_bounds"]
    assert x1 - x0 < 100


@pytest.mark.parametrize("arguments,match", [
    ({"layout": "missing"}, "PLOT_LAYOUT_NOT_FOUND"),
    ({"scale": "0:1"}, "INVALID_PLOT_SCALE"),
    ({"scale": "100:1"}, "PLOT_SCALE_DOES_NOT_FIT"),
    ({"dpi": float("nan")}, "INVALID_PLOT_DPI"),
    ({"paper": "A0", "dpi": 600}, "PLOT_IMAGE_TOO_LARGE"),
    ({"output_file": "../../outside.png"}, "PATH_OUTSIDE_PROJECT"),
    ({"output_file": "bracket.dxf"}, "PLOT_REQUIRES_DXF_TO_PDF_PNG_OR_SVG"),
])
def test_invalid_plot_requests_do_not_modify_source_or_create_output(drawing, arguments, match):
    before = drawing.read_bytes()
    with pytest.raises(ValueError, match=match):
        operation(drawing, "cad.plot", **({"output_file": "bad.png"} | arguments))
    assert drawing.read_bytes() == before
    assert not (drawing.parent / "bad.png").exists()


def test_undefined_units_require_choice_for_numeric_scale(tmp_path):
    path = tmp_path / "unitless.dxf"
    doc = ezdxf.new("R2013"); doc.units = 0
    doc.modelspace().add_line((0, 0), (100, 0)); doc.saveas(path)
    with pytest.raises(ValueError, match="PLOT_DRAWING_UNITS_REQUIRED"): plot(path, scale="1:1")
    assert plot(path, scale="1:1", drawing_units="mm")["validation"]["valid"]


def test_failed_export_restores_previous_plot_and_report(drawing, monkeypatch):
    import engineering_agent.plotting as plotting
    result = plot(drawing)
    files = [Path(result["path"]), Path(result["report_path"])]
    before = [p.read_bytes() for p in files]
    validate = plotting.validate_plot
    def fail_persisted(path, report):
        if path == files[0]: raise ValueError("READBACK_FAILED")
        return validate(path, report)
    monkeypatch.setattr(plotting, "validate_plot", fail_persisted)
    with pytest.raises(ValueError, match="READBACK_FAILED"): plot(drawing, overwrite=True)
    assert [p.read_bytes() for p in files] == before


def test_existing_export_requires_explicit_overwrite(drawing):
    result = plot(drawing)
    before = Path(result["path"]).read_bytes()
    with pytest.raises(FileExistsError, match="PLOT_OUTPUT_EXISTS"): plot(drawing)
    assert Path(result["path"]).read_bytes() == before


def test_external_source_edit_during_render_prevents_publication(drawing, monkeypatch):
    import engineering_agent.plotting as plotting
    render = plotting.render_drawing
    def change_source(source, target, arguments):
        report = render(source, target, arguments)
        source.write_bytes(source.read_bytes() + b"\n ")
        return report
    monkeypatch.setattr(plotting, "render_drawing", change_source)
    with pytest.raises(RuntimeError, match="PLOT_SOURCE_CONFLICT"): plot(drawing)
    assert not (drawing.parent / "exports/drawing.pdf").exists()


def test_cli_plot_and_mcp_discovery(drawing):
    from engineering_agent.mcp_server import MCPServer
    server = MCPServer(EngineeringAgent(), profile="full")
    tools = server.handle({"id": 1, "method": "tools/list", "params": {"limit": 500}})["result"]["tools"]
    spec = next(t for t in tools if t["name"] == "operation.cad.plot")
    assert spec["inputSchema"]["properties"]["scale"]["type"] == "string"
    assert spec["inputSchema"]["properties"]["pages"]["maxItems"] == 64
    result = subprocess.run([sys.executable, "-m", "engineering_agent.cli", "--project-dir", str(drawing.parent),
                             "cad-plot", drawing.name, "cli.pdf", "--paper", "A3"], capture_output=True, text=True, timeout=60, env=os.environ)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["validation"]["valid"]
    multi = subprocess.run([sys.executable, "-m", "engineering_agent.cli", "--project-dir", str(drawing.parent),
                           "cad-plot", drawing.name, "multi-cli.pdf", "--pages",
                           '[{"layout":"Model","paper":"A4","orientation":"portrait"},{"layout":"Model","paper":"A3"}]'],
                          capture_output=True, text=True, timeout=60, env=os.environ)
    assert multi.returncode == 0, multi.stderr
    assert json.loads(multi.stdout)["validation"]["pages"] == 2


def test_prepare_uses_current_batch_geometry_and_skips_nonplot_layers(tmp_path):
    path = tmp_path / 'batch.dxf'
    native = ezdxf.new('R2013'); native.units = 4
    line = native.modelspace().add_line((0, 0), (100, 60))
    native.layers.new('NO_PLOT', dxfattribs={'plot': 0})
    native.modelspace().add_circle((10000, 10000), 500, dxfattribs={'layer': 'NO_PLOT'})
    native.saveas(path)
    result = operation(path, 'cad.batch_apply', operations=[
        {'operation': 'cad.entity_move', 'entity_ids': [line.dxf.handle], 'delta': [500, 100, 0]},
        {'operation': 'cad.sheet_prepare', 'name': 'A3', 'fields': {'TITLE': 'Moved part'}},
    ])
    viewport = result['document']['viewports']['A3-MAIN']
    assert viewport['view_center'] == {'x': 550., 'y': 130., 'z': 0}
    assert viewport['scale'] > 1
    assert plot(path, layout='A3')['validation']['valid']


def test_plot_rejects_tilted_viewport_instead_of_silent_flattening(drawing):
    native = ezdxf.readfile(drawing)
    layout = native.layouts.new('Tilted')
    layout.page_setup(size=(420, 297), units='mm')
    viewport = layout.add_viewport((210, 150), (350, 200), (50, 30), 100)
    viewport.dxf.view_direction_vector = (1, 1, 1)
    native.saveas(drawing)
    with pytest.raises(ValueError, match='PLOT_UNSUPPORTED_ENTITIES'):
        plot(drawing, layout='Tilted')
    assert not (drawing.parent / 'exports/drawing.pdf').exists()


def test_empty_drawing_does_not_publish_blank_pdf(tmp_path):
    path = tmp_path / 'empty.dxf'
    ezdxf.new().saveas(path)
    with pytest.raises(ValueError, match='PLOT_NO_VISIBLE_GEOMETRY'): plot(path)
    assert not (tmp_path / 'exports/drawing.pdf').exists()


def test_missing_glyphs_fail_explicitly(tmp_path):
    path = tmp_path / 'font.dxf'
    native = ezdxf.new()
    native.modelspace().add_text('Missing \U0010ffff', dxfattribs={'height': 3})
    native.saveas(path)
    with pytest.raises(ValueError, match='PLOT_MISSING_GLYPHS'): plot(path)
    assert not (tmp_path / 'exports/drawing.pdf').exists()
    result = plot(path, strict=False)
    assert any(w['code'] == 'PLOT_MISSING_GLYPHS' for w in result['report']['warnings'])


def test_viewer_keeps_paper_entities_out_of_model_coordinates(drawing):
    from engineering_agent.viewer import render_interactive_dxf_viewer
    result = sheet(drawing)
    title_id = result['document']['sheets']['A3']['title_block']['entity_ids'][0]
    rendered = render_interactive_dxf_viewer(result['document'])
    from xml.etree import ElementTree as ET
    svg = ET.fromstring(rendered[rendered.index('<svg'):rendered.index('</svg>') + 6])
    assert svg.find(f".//*[@data-entity-id='{title_id}']") is None
    assert 'A3-MAIN' not in rendered
    assert svg.find(".//*[@data-entity-type='CIRCLE']") is not None


def test_pdf_rejects_geometry_entirely_outside_sheet(tmp_path):
    path = tmp_path / 'outside.dxf'
    native = ezdxf.new()
    paper = native.layouts.new('A3')
    paper.page_setup(size=(420, 297), units='mm')
    paper.add_circle((10000, 10000), 100)
    native.saveas(path)
    with pytest.raises(ValueError, match='PLOT_NO_VISIBLE_GEOMETRY_ON_PAGE'):
        plot(path, layout='A3')
    assert not (tmp_path / 'exports/drawing.pdf').exists()


def test_uppercase_pdf_extension_is_validated_as_pdf(drawing):
    result = plot(drawing, 'PDF')
    assert result['report']['format'] == 'pdf'
    assert result['validation']['pages'] == 1
