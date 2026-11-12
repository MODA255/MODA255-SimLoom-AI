"""Native SVG identity, rendering completeness and read-only preview checks."""
import hashlib
import json
from xml.etree import ElementTree as ET

import ezdxf
import pytest

from cad_view_fixture import create_view_fixture
from engineering_agent.cad_render import native_model_scene
from engineering_agent.cad_model import CadDocument
from engineering_agent.viewer import asset_view


def paths(scene, entity_id):
    return [e for e in ET.fromstring(scene['svg']).iter() if e.get('data-entity-id') == entity_id
            and e.get('data-draw-kind') != 'hit']


@pytest.fixture
def drawing(tmp_path):
    path = tmp_path / 'native.dxf'
    handles = create_view_fixture(path)
    before = path.read_bytes()
    scene = native_model_scene(CadDocument.open(path).to_dxf(), path)
    assert path.read_bytes() == before
    return path, handles, scene


def test_arc_and_bulged_polyline_keep_bezier_paths(drawing):
    _, handles, scene = drawing
    for name in ('arc', 'bulge', 'ellipse'):
        elements = paths(scene, handles[name])
        assert len(elements) == 1
        assert elements[0].tag.endswith('path')
        assert 'c ' in elements[0].get('d').lower()
    assert scene['rendered_entities'] == len(handles)
    assert not scene['warnings']


def test_nested_block_and_attributes_share_outer_reference(drawing):
    _, handles, scene = drawing
    elements = paths(scene, handles['insert'])
    assert len(elements) >= 2
    assert all(e.get('data-entity-type') == 'INSERT' for e in elements)
    layers = [json.loads(e.get('data-layers')) for e in elements]
    assert any('PART' in ancestry and 'DETAIL' in ancestry for ancestry in layers)
    assert any(e.get('data-draw-kind') == 'fill' for e in elements)  # native ATTRIB glyph paths


def test_hatch_has_outer_and_inner_loops_in_one_evenodd_path(drawing):
    _, handles, scene = drawing
    hatch = paths(scene, handles['hatch'])
    assert len(hatch) == 1
    assert hatch[0].get('data-draw-kind') == 'fill'
    assert hatch[0].get('d').lower().count('m ') == 2
    assert 'fill-rule="evenodd"' in scene['svg']


def test_native_dimensions_are_graphics_and_stay_selectable_as_one_object(drawing):
    _, handles, scene = drawing
    elements = paths(scene, handles['dimension'])
    assert len(elements) >= 4
    assert {e.get('data-draw-kind') for e in elements} == {'stroke', 'fill'}
    assert all(e.get('data-entity-type') == 'DIMENSION' for e in elements)


def test_off_layers_are_rendered_hidden_but_do_not_change_fit(drawing):
    path, handles, scene = drawing
    assert scene['layers']['HIDDEN']['visible'] is False
    assert scene['world_bounds'][2] < 200
    assert all(e.get('style') == 'display:none' for e in paths(scene, handles['hidden']))
    assert ezdxf.readfile(path).layers.get('HIDDEN').is_off()


def test_asset_api_provides_native_svg_and_source_identity(drawing):
    path, handles, _ = drawing
    result = asset_view(path.parent, path.name)
    info = result['asset']['cad_view']
    assert info['source_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result['svg'] in result['viewer']
    assert info['primitive_count'] > info['entity_count']
    assert 'data-renderer="ezdxf-svg"' in result['viewer']


def test_unsupported_entity_is_reported_instead_of_silently_removed(tmp_path):
    path = tmp_path / 'unsupported.dxf'
    native = ezdxf.new()
    native.modelspace().add_line((0, 0), (10, 10))
    native.modelspace().new_entity('ACAD_PROXY_ENTITY', dxfattribs={})
    native.saveas(path)
    scene = native_model_scene(CadDocument.open(path).to_dxf(), path)
    assert any(w['code'] == 'VIEWER_ENTITY_SKIPPED' for w in scene['warnings'])


def test_empty_model_has_usable_viewbox(tmp_path):
    path = tmp_path / 'empty.dxf'; ezdxf.new().saveas(path)
    scene = native_model_scene(CadDocument.open(path).to_dxf(), path)
    root = ET.fromstring(scene['svg'])
    assert all(float(x) > 0 for x in root.get('viewBox').split()[2:])
    assert scene['rendered_entities'] == 0
    assert any(w['code'] == 'VIEWER_NO_GEOMETRY' for w in scene['warnings'])


def test_unavailable_glyph_and_html_text_remain_visible_warnings(tmp_path):
    path = tmp_path / 'text.dxf'
    native = ezdxf.new()
    native.modelspace().add_text('<script>\U0010ffff</script>', dxfattribs={'height': 3})
    native.saveas(path)
    result = asset_view(tmp_path, path.name)
    assert any(w['code'] == 'VIEWER_MISSING_GLYPHS' for w in result['asset']['cad_view']['warnings'])
    assert '<script>' not in result['svg']
    assert '&lt;script&gt;' in result['svg']


def test_many_entities_keep_unique_identity_without_truncation(tmp_path):
    path = tmp_path / 'many.dxf'
    native = ezdxf.new(); native.units = 4
    for row in range(30):
        for column in range(40):
            native.modelspace().add_circle((column * 5, row * 5), 1)
    native.saveas(path)
    scene = native_model_scene(CadDocument.open(path).to_dxf(), path)
    nodes = [e for e in ET.fromstring(scene['svg']).iter() if e.get('data-draw-kind') == 'stroke']
    assert len({e.get('data-entity-id') for e in nodes}) == 1200
    assert scene['rendered_entities'] == 1200
    assert scene['primitive_count'] == 1200
    assert not scene['warnings']
