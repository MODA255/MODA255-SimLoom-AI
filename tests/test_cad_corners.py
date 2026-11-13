"""Corner-edit contracts checked against native DXF geometry, not the builder."""
import math
from pathlib import Path

import ezdxf
import pytest
from ezdxf.math import Vec3

from engineering_agent import EngineeringAgent


def make_corner(root, angle=60, rotation=0, reverse=(False, False), length=20, origin=(0, 0, 0), sheet=None):
    path = root / 'corner.dxf'
    drawing = ezdxf.new('R2010')
    drawing.layers.new('PROFILE', dxfattribs={'color': 3})
    space = drawing.layouts.new(sheet) if sheet else drawing.modelspace()
    corner = Vec3(origin)
    handles, outer = [], []
    for turn, flip in zip((rotation, rotation + angle), reverse):
        tip = corner + Vec3(math.cos(math.radians(turn)), math.sin(math.radians(turn)), 0) * length
        endpoints = (tip, corner) if flip else (corner, tip)
        line = space.add_line(*endpoints, dxfattribs={'layer': 'PROFILE', 'color': 3})
        handles.append(line.dxf.handle)
        outer.append(tip)
    drawing.saveas(path)
    return path, handles, outer


def apply(path, ids, operation='cad.entity_fillet', **kwargs):
    return EngineeringAgent({'tools': {}}).execute_operation(operation, {
        'project_dir': str(path.parent), 'input_file': str(path), 'output_file': str(path),
        'entity_ids': ids, **kwargs,
    })


def pair(path):
    meta = Path(str(path) + '.cad.json')
    return path.read_bytes(), meta.read_bytes() if meta.exists() else None


def assert_fillet(path, ids, outer, angle, radius, reverse=(False, False), sheet=None):
    drawing = ezdxf.readfile(path)
    space = drawing.layouts.get(sheet) if sheet else drawing.modelspace()
    arc, = space.query('ARC')
    assert arc.dxf.radius == pytest.approx(radius)
    assert (arc.dxf.end_angle - arc.dxf.start_angle) % 360 == pytest.approx(180 - angle)
    endpoints = [arc.start_point, arc.end_point]
    for handle, tip, flip in zip(ids, outer, reverse):
        line = drawing.entitydb[handle]
        retained = line.dxf.start if flip else line.dxf.end
        trimmed = line.dxf.end if flip else line.dxf.start
        assert retained.isclose(tip, rel_tol=0, abs_tol=1e-7)
        nearest = min(endpoints, key=lambda p: (p - trimmed).magnitude)
        assert (nearest - trimmed).magnitude < 1e-7
        endpoints.remove(nearest)
        radial = trimmed - arc.ocs().to_wcs(arc.dxf.center)
        direction = retained - trimmed
        assert abs(radial.normalize().dot(direction.normalize())) < 1e-8
        assert line.dxf.color == 3
    assert not endpoints


@pytest.mark.parametrize('angle,rotation,order', [(30, 0, False), (60, 37, True), (90, 0, False), (120, 208, True), (150, -71, False), (165, 350, True)])
@pytest.mark.parametrize('reverse', [(False, False), (False, True), (True, False), (True, True)])
def test_fillet_native_continuity_tangency_and_minor_sweep(tmp_path, angle, rotation, order, reverse):
    path, ids, outer = make_corner(tmp_path, angle, rotation, reverse)
    result = apply(path, ids[::-1] if order else ids, distance=2)
    assert result['persisted_validation']['valid']
    assert_fillet(path, ids, outer, angle, 2, reverse)


def test_fillet_radius_api_and_obtuse_corner_can_use_radius_above_arm_length(tmp_path):
    path, ids, outer = make_corner(tmp_path, 150, length=10)
    apply(path, ids, radius=12)
    assert_fillet(path, ids, outer, 150, 12)


@pytest.mark.parametrize('operation', ['cad.entity_fillet', 'cad.entity_chamfer'])
@pytest.mark.parametrize('selected', [[], [0], [0, 0], [0, 1, 0]])
def test_corner_rejects_incorrect_selection_without_writes(tmp_path, operation, selected):
    path, ids, _ = make_corner(tmp_path)
    before = pair(path)
    with pytest.raises(ValueError):
        apply(path, [ids[i] for i in selected], operation, distance=2)
    assert pair(path) == before
    assert not (tmp_path / '.engineering-agent/project.lock').exists()


@pytest.mark.parametrize('operation', ['cad.entity_fillet', 'cad.entity_chamfer'])
@pytest.mark.parametrize('value', [0, -2, float('nan'), float('inf'), True, '2', None])
def test_corner_rejects_invalid_size_without_writes(tmp_path, operation, value):
    path, ids, _ = make_corner(tmp_path)
    before = pair(path)
    with pytest.raises(ValueError):
        apply(path, ids, operation, **{'radius' if operation.endswith('fillet') else 'distance': value})
    assert pair(path) == before


@pytest.mark.parametrize('tolerance', [0, -1, float('nan'), float('inf'), True, '1e-6'])
def test_corner_rejects_invalid_tolerance(tmp_path, tolerance):
    path, ids, _ = make_corner(tmp_path)
    before = pair(path)
    with pytest.raises(ValueError, match='INVALID_CORNER_TOLERANCE'):
        apply(path, ids, radius=2, tolerance=tolerance)
    assert pair(path) == before


@pytest.mark.parametrize('operation', ['cad.entity_fillet', 'cad.entity_chamfer'])
@pytest.mark.parametrize('case,code', [
    ('collinear', 'COLLINEAR'), ('opposite', 'COLLINEAR'), ('disconnected', 'CONNECTED'),
    ('zero-length', 'ZERO_LENGTH'), ('nonplanar', 'XY_PLANE'), ('different-layout', 'LAYOUTS'),
    ('thickness', 'THICKNESS'), ('locked', 'LAYER_LOCKED'), ('circle', 'ONLY_SUPPORTS_LINES'),
])
def test_corner_rejects_unsupported_geometry_atomically(tmp_path, operation, case, code):
    path, ids, _ = make_corner(tmp_path)
    drawing = ezdxf.readfile(path)
    first, second = (drawing.entitydb[h] for h in ids)
    if case == 'collinear': second.dxf.end = (10, 0, 0)
    if case == 'opposite': second.dxf.end = (-10, 0, 0)
    if case == 'disconnected': second.dxf.start = (0, 2, 0)
    if case == 'zero-length': first.dxf.end = first.dxf.start
    if case == 'nonplanar': second.dxf.end = (10, 10, 1)
    if case == 'different-layout': drawing.modelspace().move_to_layout(second, drawing.layouts.new('Sheet A'))
    if case == 'thickness': first.dxf.thickness = 2
    if case == 'locked': drawing.layers.get('PROFILE').lock()
    if case == 'circle': ids[1] = drawing.modelspace().add_circle((0, 0), 2).dxf.handle
    drawing.saveas(path)
    before = pair(path)
    with pytest.raises(ValueError, match=code):
        apply(path, ids, operation, distance=2)
    assert pair(path) == before
    assert not (tmp_path / '.engineering-agent/project.lock').exists()


@pytest.mark.parametrize('operation', ['cad.entity_fillet', 'cad.entity_chamfer'])
def test_corner_rejects_exhausted_arms(tmp_path, operation):
    path, ids, _ = make_corner(tmp_path, 90, length=2)
    before = pair(path)
    with pytest.raises(ValueError, match='TOO_LARGE'):
        apply(path, ids, operation, distance=2)
    assert pair(path) == before


def test_fillet_conflicting_aliases_rejected(tmp_path):
    path, ids, _ = make_corner(tmp_path)
    before = pair(path)
    with pytest.raises(ValueError, match='RADIUS_DISTANCE_CONFLICT'):
        apply(path, ids, radius=2, distance=3)
    assert pair(path) == before


@pytest.mark.parametrize('angle,rotation,reverse', [(30, 17, (True, False)), (90, 232, (False, True)), (150, 0, (True, True))])
def test_chamfer_preserves_arms_and_equal_setbacks(tmp_path, angle, rotation, reverse):
    path, ids, outer = make_corner(tmp_path, angle, rotation, reverse)
    result = apply(path, ids, 'cad.entity_chamfer', distance=3)
    drawing = ezdxf.readfile(path)
    connector, = [e for e in drawing.modelspace() if e.dxf.handle not in ids]
    assert connector.dxftype() == 'LINE'
    endpoints = [connector.dxf.start, connector.dxf.end]
    for identifier, tip, flip in zip(ids, outer, reverse):
        line = drawing.entitydb[identifier]
        trimmed, retained = (line.dxf.end, line.dxf.start) if flip else (line.dxf.start, line.dxf.end)
        assert trimmed.magnitude == pytest.approx(3)
        assert retained.isclose(tip, rel_tol=0, abs_tol=1e-8)
        endpoint = min(endpoints, key=lambda p: (p-trimmed).magnitude)
        assert (endpoint-trimmed).magnitude < 1e-8
        endpoints.remove(endpoint)
    checked, = result['persisted_validation']['native']['corner_checks']
    assert checked['valid']
    assert checked['metrics']['setbacks'] == pytest.approx([3, 3])


def test_paperspace_fillet_retains_layout_and_elevation(tmp_path):
    path, ids, outer = make_corner(tmp_path, 120, 32, origin=(120, -70, 8), sheet='Sheet A')
    result = apply(path, ids, radius=3)
    assert not len(ezdxf.readfile(path).modelspace())
    assert_fillet(path, ids, outer, 120, 3, sheet='Sheet A')
    assert result['persisted_validation']['native']['corner_checks'][0]['valid']


def test_near_coincident_endpoints_stay_on_original_supporting_lines(tmp_path):
    path, ids, _ = make_corner(tmp_path, 90)
    drawing = ezdxf.readfile(path)
    drawing.entitydb[ids[1]].dxf.start = (0.0000002, 0.0000002, 0)
    original_directions = [e.dxf.end-e.dxf.start for e in (drawing.entitydb[h] for h in ids)]
    drawing.saveas(path)
    apply(path, ids, radius=2, tolerance=1e-6)
    drawing = ezdxf.readfile(path)
    for identifier, direction in zip(ids, original_directions):
        line = drawing.entitydb[identifier]
        assert direction.normalize().cross((line.dxf.end-line.dxf.start).normalize()).magnitude < 1e-10


def test_independent_native_checks_reject_bad_geometry_without_sidecar(tmp_path):
    from engineering_agent.dxf_validation import inspect_native
    path = tmp_path / 'independent.dxf'
    drawing = ezdxf.new('R2010')
    m = drawing.modelspace()
    a = m.add_line((2, 0), (10, 0)); b = m.add_line((0, 2), (0, 10))
    arc = m.add_arc((2, 2), 2, 180, 270)
    spec = {'kind': 'fillet', 'line_ids': [a.dxf.handle, b.dxf.handle], 'connector_id': arc.dxf.handle, 'radius': 2}
    drawing.saveas(path)
    assert inspect_native(path, corner_connections=[spec])['valid']
    cases = [('gap', 'CORNER_DISCONNECTED'), ('angle', 'CORNER_NOT_TANGENT'), ('major', 'CORNER_ARC_NOT_MINOR'), ('radius', 'CORNER_RADIUS_MISMATCH')]
    for case, code in cases:
        a.dxf.start, a.dxf.end = (2, 0), (10, 0)
        arc.dxf.start_angle, arc.dxf.end_angle = 180, 270
        check = dict(spec)
        if case == 'gap': a.dxf.start = (3, 0)
        if case == 'angle': a.dxf.end = (10, 3)
        if case == 'major': arc.dxf.start_angle, arc.dxf.end_angle = 270, 180
        if case == 'radius': check['radius'] = 3
        drawing.saveas(path)
        before = path.read_bytes()
        report = inspect_native(path, corner_connections=[check])
        assert not report['valid']
        assert code in {e['code'] for e in report['errors']}
        assert path.read_bytes() == before
        assert not Path(str(path) + '.cad.json').exists()


def test_independent_chamfer_check_detects_wrong_setback(tmp_path):
    from engineering_agent.dxf_validation import inspect_native
    path = tmp_path / 'independent.dxf'
    drawing = ezdxf.new('R2010'); m = drawing.modelspace()
    a=m.add_line((3, 0), (10, 0)); b=m.add_line((0, 3), (0, 10)); c=m.add_line((3, 0), (0, 3))
    drawing.saveas(path)
    spec = {'kind': 'chamfer', 'line_ids': [a.dxf.handle, b.dxf.handle], 'connector_id': c.dxf.handle, 'distance': 2}
    report = inspect_native(path, corner_connections=[spec])
    assert not report['valid']
    assert 'CORNER_SETBACK_MISMATCH' in {e['code'] for e in report['errors']}


def test_builder_defect_is_rejected_before_any_files_change(tmp_path, monkeypatch):
    import engineering_agent.cad_operations as engine
    path, ids, _ = make_corner(tmp_path)
    before = pair(path)
    original = engine._fillet_geometry
    def wrong_sweep(*args, **kwargs):
        a, b, center, start, end = original(*args, **kwargs)
        return a, b, center, end, start
    monkeypatch.setattr(engine, '_fillet_geometry', wrong_sweep)
    with pytest.raises(ValueError, match='CAD_CORNER_VALIDATION_FAILED.*CORNER_ARC_NOT_MINOR'):
        apply(path, ids, radius=2)
    assert pair(path) == before
    assert not (tmp_path / '.engineering-agent/project.lock').exists()


def test_native_readback_failure_rolls_back_drawing_sidecar_and_history(tmp_path, monkeypatch):
    import engineering_agent.cad_operations as engine
    path, ids, _ = make_corner(tmp_path)
    apply(path, ids, 'cad.selection_create', name='corner')
    history_file, = (tmp_path / '.engineering-agent/cad-history').glob('*.json')
    before, old_history = pair(path), history_file.read_bytes()
    original = engine.write_native
    def broken_writer(output, **kwargs):
        result = original(output, **kwargs)
        drawing = ezdxf.readfile(output)
        arc, = drawing.modelspace().query('ARC')
        arc.dxf.center = arc.dxf.center + Vec3(1, 0, 0)
        drawing.saveas(output)
        return result
    monkeypatch.setattr(engine, 'write_native', broken_writer)
    with pytest.raises(ValueError, match='CAD_PERSISTED_VALIDATION_FAILED.*CORNER_DISCONNECTED'):
        apply(path, ids, radius=2)
    assert pair(path) == before
    assert history_file.read_bytes() == old_history
    assert not (tmp_path / '.engineering-agent/project.lock').exists()


def test_fillet_is_undoable_and_redoable_with_exact_original_bytes(tmp_path):
    path, ids, _ = make_corner(tmp_path)
    before = pair(path)
    apply(path, ids, radius=2)
    after = pair(path)
    agent = EngineeringAgent({'tools': {}})
    for op, expected in [('undo', before), ('redo', after)]:
        agent.execute_operation('cad.'+op, {'project_dir': str(tmp_path), 'input_file': str(path)})
        assert pair(path) == expected


def test_later_batch_edit_supersedes_automatic_creation_check(tmp_path):
    path, ids, _ = make_corner(tmp_path)
    result = apply(path, ids, 'cad.batch_apply', operations=[
        {'operation': 'cad.entity_fillet', 'entity_ids': ids, 'radius': 2},
        {'operation': 'cad.entity_move', 'entity_ids': [ids[0]], 'delta': [20, 0, 0]},
    ])
    assert result['results'][0]['geometry_validation']['valid']
    assert result['results'][0]['corner_check_status'] == 'superseded_by_later_edit'
    assert not result['persisted_validation']['native']['corner_checks']
    check = result['results'][0]['corner_check']
    audited = EngineeringAgent().execute_operation('cad.audit', {'project_dir': str(tmp_path), 'input_file': str(path), 'requirements': {'corner_connections': [check]}})
    assert not audited['validation']['valid']
    assert 'CORNER_DISCONNECTED' in {e['code'] for e in audited['validation']['errors']}
    before = pair(path)
    with pytest.raises(ValueError, match='CAD_PERSISTED_VALIDATION_FAILED.*CORNER_DISCONNECTED'):
        apply(path, ids, 'cad.selection_create', name='still disconnected', requirements={'corner_connections': [check]})
    assert pair(path) == before


def test_mcp_exposes_radius_alias_and_reports_invalid_geometry(tmp_path):
    import json
    from engineering_agent.mcp_server import MCPServer
    server = MCPServer(EngineeringAgent({'tools': {}}), profile='full')
    tools = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {'limit': 500}})['result']['tools']
    schema = next(t['inputSchema'] for t in tools if t['name'] == 'operation.cad.entity_fillet')
    assert schema['x-engineering-schema-version'] == '1.1'
    assert schema['allOf'] == [{'anyOf': [{'required': ['radius']}, {'required': ['distance']}]}]
    assert schema['properties']['entity_ids']['uniqueItems']
    path, ids, outer = make_corner(tmp_path)
    def call(radius):
        return server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'operation.cad.entity_fillet', 'arguments': {'project_dir': str(tmp_path), 'input_file': str(path), 'output_file': str(path), 'entity_ids': ids, 'radius': radius}}})
    before = pair(path)
    invalid = call(-2)
    assert invalid['result']['isError'] is True and 'INVALID_FILLET_RADIUS' in json.dumps(invalid)
    assert pair(path) == before
    result = call(2)
    assert 'error' not in result
    assert_fillet(path, ids, outer, 60, 2)
