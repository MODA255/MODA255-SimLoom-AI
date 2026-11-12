"""Independent geometric postconditions for line fillets and equal-setback chamfers.

No construction helpers, CadDocument, or sidecar data are used here. Disk
callers supply coordinates read directly from the native DXF entities.
"""
from __future__ import annotations

import math
from typing import Any


def _point(value):
    if isinstance(value, dict):
        return tuple(float(value.get(axis, 0)) for axis in ('x', 'y', 'z'))
    return tuple(float(v) for v in value)


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def inspect_corner(entities: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    errors, metrics = [], {}
    def fail(code, **details):
        errors.append({'code': code, **details})
    try:
        kind = spec['kind']
        ids = spec['line_ids']
        connector_id = str(spec['connector_id'])
        tolerance = float(spec.get('tolerance', 1e-6))
        wanted = float(spec['radius' if kind == 'fillet' else 'distance'])
        if (kind not in {'fillet', 'chamfer'} or not isinstance(ids, list) or len(ids) != 2
                or len(set(ids + [connector_id])) != 3 or not math.isfinite(tolerance)
                or tolerance <= 0 or not math.isfinite(wanted) or wanted <= 0):
            raise ValueError
        indexed = {str(item['id']): item for item in entities}
        first, second, connector = (indexed[str(i)] for i in [*ids, connector_id])
    except (ValueError, TypeError, KeyError, OverflowError):
        return {'valid': False, 'errors': [{'code': 'INVALID_OR_UNRESOLVED_CORNER_CHECK'}], 'metrics': metrics}
    if (first['type'] != 'LINE' or second['type'] != 'LINE'
            or connector['type'] != ('ARC' if kind == 'fillet' else 'LINE')):
        return {'valid': False, 'errors': [{'code': 'CORNER_ENTITY_TYPE_MISMATCH'}], 'metrics': metrics}
    if len({e.get('sheet', 'Model') for e in (first, second, connector)}) != 1:
        fail('CORNER_LAYOUT_MISMATCH')
    try:
        lines = [[_point(e[key]) for key in ('start', 'end')] for e in (first, second)]
        if kind == 'fillet':
            center, radius = _point(connector['center']), float(connector['radius'])
            start, end = float(connector['start_angle']), float(connector['end_angle'])
            tips = connector.get('_world_endpoints') or [
                (center[0] + radius * math.cos(math.radians(a)),
                 center[1] + radius * math.sin(math.radians(a)), center[2]) for a in (start, end)]
            tips = [_point(p) for p in tips]
            if not all(math.isfinite(n) for n in [*center, radius, start, end]):
                raise ValueError
            metrics['radius_error'] = abs(radius - wanted)
            metrics['sweep_degrees'] = (end - start) % 360
            if radius <= 0 or metrics['radius_error'] > tolerance:
                fail('CORNER_RADIUS_MISMATCH', actual=radius, expected=wanted)
            if not 0 < metrics['sweep_degrees'] <= 180 + 1e-8:
                fail('CORNER_ARC_NOT_MINOR', sweep=metrics['sweep_degrees'])
        else:
            tips = [_point(connector[key]) for key in ('start', 'end')]
        if len(tips) != 2 or not all(len(p) == 3 and all(math.isfinite(n) for n in p)
                                    for p in [*lines[0], *lines[1], *tips]):
            raise ValueError
    except (ValueError, TypeError, KeyError, OverflowError):
        return {'valid': False, 'errors': [{'code': 'CORNER_INVALID_GEOMETRY'}], 'metrics': metrics}
    # Match each connector endpoint to a different arm. This does not assume
    # entity order, LINE direction, or which endpoint is the corner.
    def assignment(order):
        return sum(min(math.dist(tips[j], p) for p in line) for line, j in zip(lines, order))
    order = min(((0, 1), (1, 0)), key=assignment)
    joints, retained, lengths, gaps = [], [], [], []
    for line, index, identifier in zip(lines, order, ids):
        tip = tips[index]
        near = min(range(2), key=lambda i: math.dist(tip, line[i]))
        joint, other = line[near], line[1 - near]
        gap, length = math.dist(joint, tip), math.dist(joint, other)
        joints.append(joint); retained.append(other); lengths.append(length); gaps.append(gap)
        if gap > tolerance:
            fail('CORNER_DISCONNECTED', entity_id=identifier, gap=gap)
        if length <= tolerance:
            fail('CORNER_ZERO_LENGTH_ARM', entity_id=identifier)
    metrics['endpoint_gaps'] = gaps
    if min(lengths) > tolerance:
        if kind == 'fillet':
            deviations = [abs(_dot(_sub(joint, center), _sub(other, joint))) / length
                          for joint, other, length in zip(joints, retained, lengths)]
            metrics['tangency_deviations'] = deviations
            for identifier, deviation in zip(ids, deviations):
                if deviation > tolerance:
                    fail('CORNER_NOT_TANGENT', entity_id=identifier, deviation=deviation)
        else:
            a, b = joints
            u, v = (_sub(other, joint) for joint, other in zip(joints, retained))
            cross = u[0] * v[1] - u[1] * v[0]
            if abs(cross) / (lengths[0] * lengths[1]) <= 1e-12:
                fail('CORNER_COLLINEAR_ARMS')
            else:
                t = ((b[0] - a[0]) * v[1] - (b[1] - a[1]) * v[0]) / cross
                corner = tuple(a[i] + t * u[i] for i in range(3))
                setbacks = [math.dist(joint, corner) for joint in joints]
                metrics['setbacks'] = setbacks
                for identifier, setback in zip(ids, setbacks):
                    if abs(setback - wanted) > tolerance:
                        fail('CORNER_SETBACK_MISMATCH', entity_id=identifier, actual=setback, expected=wanted)
    if math.dist(*tips) <= tolerance:
        fail('CORNER_DEGENERATE_CONNECTOR')
    return {'valid': not errors, 'errors': errors, 'metrics': metrics, 'connector_id': connector_id}
