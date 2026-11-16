"""Engineering quantity parsing for vendor-neutral CAD operations.

CAD clients commonly send values such as ``25mm`` or ``3deg`` even when the
underlying JSON operation schema uses numbers.  This module keeps that
convenience at the operation boundary while storing canonical drawing values
as ordinary finite floats.  Lengths are normalized to millimetres and angles
to degrees, matching the DXF model used by SimLoom.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
import re
from typing import Any


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_QUANTITY = re.compile(rf"^\s*({_NUMBER})\s*([^\s]+)\s*$")

# factor to the canonical unit for the dimension.  The parser deliberately
# does not guess unknown units; a typo must reach the caller as a structured
# CAD argument error.
_UNITS: dict[str, tuple[str, float]] = {
    "1": ("dimensionless", 1.0),
    "%": ("dimensionless", 0.01),
    "mm": ("length", 1.0),
    "um": ("length", 0.001),
    "µm": ("length", 0.001),
    "cm": ("length", 10.0),
    "m": ("length", 1000.0),
    "in": ("length", 25.4),
    "ft": ("length", 304.8),
    "mil": ("length", 0.0254),
    "deg": ("angle", 1.0),
    "°": ("angle", 1.0),
    "rad": ("angle", 180.0 / math.pi),
}

_ANGLE_KEYS = {
    "angle", "angle_deg", "angle_step", "total_angle", "rotation",
    "start_angle", "end_angle", "oblique", "pattern_angle",
    "start_param", "end_param", "sweep_angle",
}
_DIMENSIONLESS_KEYS = {
    "factor", "ratio", "scale", "pattern_scale", "width_factor", "opacity",
    "percent", "normalized", "weight", "parameter", "u", "v", "w",
}
_LENGTH_KEYS = {
    "x", "y", "z", "radius", "inner_radius", "outer_radius", "diameter",
    "width", "height", "length", "distance", "distance1", "distance2",
    "deflection", "chord_height", "depth", "size", "gap", "margin",
    "cell_width", "cell_height", "row_spacing", "column_spacing",
    "text_height", "major_radius", "minor_radius",
}
_VECTOR_LENGTH_KEYS = {
    "start", "end", "center", "origin", "point", "insert", "base_point",
    "corner1", "corner2", "first_corner", "second_corner", "major_axis",
    "delta", "translate", "translation", "window", "bounds", "p1", "p2",
    "vertices", "boundary", "boundary_points", "waypoints", "points",
    "margins", "position", "offset_vector",
}
_VECTOR_DIMENSIONLESS_KEYS = {
    "direction", "vector", "unit_vector", "axis", "x_axis", "y_axis", "z_axis",
}
_INTEGER_KEYS = {
    "count", "rows", "columns", "sides", "degree", "index", "limit", "flags",
    "offset", "step_index", "pattern_type", "hatch_style", "since",
}


def dimension_for_key(key: str | None, parent: str | None = None) -> str | None:
    """Return the expected quantity dimension for a schema/property key."""
    if not key:
        return None
    key = str(key)
    parent = str(parent or "")
    if key in {"x", "y", "z"}:
        if parent in _VECTOR_DIMENSIONLESS_KEYS or parent in _DIMENSIONLESS_KEYS:
            return "dimensionless"
        return "length"
    if key in _ANGLE_KEYS:
        return "angle"
    if key in _DIMENSIONLESS_KEYS:
        return "dimensionless"
    if key in _LENGTH_KEYS:
        # Pagination offset is an integer when it belongs to cad.query.  A
        # plain numeric string still normalizes correctly, while a unit is
        # rejected by the integer schema after normalization.
        if key == "offset" and parent == "query":
            return None
        return "length"
    if key in _VECTOR_LENGTH_KEYS:
        return "length"
    if key in _VECTOR_DIMENSIONLESS_KEYS:
        return "dimensionless"
    if key in _INTEGER_KEYS:
        return None
    # These fields carry a scalar engineering value in constraints and
    # dimension checks.  Unit-bearing values are accepted and canonicalized
    # according to the unit's own dimension; plain numbers remain unchanged.
    if key in {"value", "expected", "actual", "default", "measurement",
               "tolerance", "lower", "upper", "minimum", "maximum", "min", "max"}:
        return "auto"
    return None


def _finite_number(value: Any, code: str) -> float:
    if isinstance(value, bool):
        raise ValueError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(code) from exc
    if not math.isfinite(number):
        raise ValueError(code)
    return number


def parse_quantity(value: Any, *, dimension: str | None = None,
                   code: str = "CAD_QUANTITY_INVALID") -> float:
    """Parse a finite CAD number or an explicit quantity string/dict.

    ``dimension`` may be ``length``, ``angle``, ``dimensionless`` or ``auto``.
    Plain numbers have no unit and are retained in the model's canonical
    units.  A quantity with an incompatible or unknown unit is rejected.
    """
    unit: str | None = None
    raw = value
    if isinstance(value, Mapping) and "value" in value:
        raw, unit = value.get("value"), value.get("unit")
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError("CAD_UNIT_REQUIRED")
        unit = unit.strip()
    elif isinstance(value, str):
        match = _QUANTITY.fullmatch(value)
        if not match:
            raise ValueError("CAD_QUANTITY_INVALID")
        raw, unit = match.group(1), match.group(2)
    if unit is None:
        return _finite_number(raw, code)
    unit_info = _UNITS.get(unit)
    if unit_info is None:
        raise ValueError("CAD_UNIT_UNKNOWN:" + unit)
    actual_dimension, factor = unit_info
    if dimension not in (None, "auto", actual_dimension):
        raise ValueError(f"CAD_UNIT_DIMENSION_MISMATCH:{unit}:{dimension}")
    number = _finite_number(raw, code)
    return number * factor


def _is_quantity_mapping(value: Mapping[str, Any]) -> bool:
    return "value" in value and "unit" in value


def _schema_types(schema: Any) -> set[str]:
    if not isinstance(schema, Mapping):
        return set()
    expected = schema.get("type")
    if isinstance(expected, str):
        return {expected}
    if isinstance(expected, (list, tuple)):
        return {str(item) for item in expected}
    return set()


def _schema_allows_quantity(schema: Any) -> bool:
    if not isinstance(schema, Mapping):
        return True
    types = _schema_types(schema)
    if "string" in types and not ("number" in types or "integer" in types):
        return False
    if "number" in types or "integer" in types:
        return True
    for option in (schema.get("oneOf"), schema.get("anyOf")):
        if isinstance(option, list) and any(_schema_allows_quantity(item) for item in option):
            return True
    return not types


def _looks_like_quantity(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if re.fullmatch(rf"\s*{_NUMBER}\s*", value):
        return False
    return bool(_QUANTITY.fullmatch(value))


def _child_schema(schema: Any, key: str) -> Any:
    if not isinstance(schema, Mapping):
        return None
    # OperationSpec stores a direct ``{field: schema}`` mapping, while nested
    # JSON-schema objects use ``properties``.  Support both shapes.
    direct = schema.get(key)
    if isinstance(direct, Mapping) and ("type" in direct or "oneOf" in direct or "anyOf" in direct):
        return direct
    properties = schema.get("properties")
    if isinstance(properties, Mapping) and key in properties:
        return properties[key]
    additional = schema.get("additionalProperties")
    return additional if isinstance(additional, Mapping) else None


def normalize_cad_arguments(value: Any, *, key: str | None = None,
                            parent: str | None = None, schema: Any = None) -> Any:
    """Deep-copy and normalize quantity-bearing CAD arguments.

    The traversal is intentionally conservative: ordinary text, file paths,
    ids and labels remain untouched.  Geometry vectors are handled by their
    field name and their ``x/y/z`` children inherit the vector dimension.
    """
    if isinstance(value, Mapping):
        if _is_quantity_mapping(value):
            return parse_quantity(value, dimension=dimension_for_key(key, parent))
        result: dict[Any, Any] = {}
        for child_key, child_value in value.items():
            child_name = str(child_key)
            child_dimension = dimension_for_key(child_name, key)
            child_schema = _child_schema(schema, child_name)
            if isinstance(child_value, (Mapping, list, tuple)):
                result[child_key] = normalize_cad_arguments(
                    child_value, key=child_name, parent=key, schema=child_schema)
            elif (child_dimension is not None and _schema_allows_quantity(child_schema)
                  and _looks_like_quantity(child_value)):
                result[child_key] = parse_quantity(
                    child_value, dimension=child_dimension,
                    code=f"CAD_{child_name.upper()}_INVALID")
            elif (child_name in _INTEGER_KEYS and isinstance(child_value, str)
                  and _schema_allows_quantity(child_schema) and _looks_like_quantity(child_value)):
                # Form and MCP clients often serialize integer controls as
                # text.  Preserve the integer contract and reject units.
                parsed = parse_quantity(child_value, dimension="dimensionless",
                                        code=f"CAD_{child_name.upper()}_INVALID")
                if not parsed.is_integer():
                    raise ValueError(f"CAD_{child_name.upper()}_INVALID")
                result[child_key] = int(parsed)
            else:
                result[child_key] = child_value
        return result
    if isinstance(value, (list, tuple)):
        dimension = dimension_for_key(key, parent)
        if dimension in {"length", "dimensionless"}:
            return [parse_quantity(item, dimension=dimension,
                                    code=f"CAD_{str(key or 'VALUE').upper()}_INVALID")
                    if _looks_like_quantity(item) else
                    normalize_cad_arguments(item, key=key, parent=parent, schema=(schema or {}).get("items") if isinstance(schema, Mapping) else None)
                    if isinstance(item, (Mapping, list, tuple)) else item
                    for item in value]
        return [normalize_cad_arguments(item, key=key, parent=parent)
                if isinstance(item, (Mapping, list, tuple)) else item for item in value]
    if (isinstance(value, str) and dimension_for_key(key, parent) is not None
            and _schema_allows_quantity(schema) and _looks_like_quantity(value)):
        return parse_quantity(value, dimension=dimension_for_key(key, parent),
                              code=f"CAD_{str(key or 'VALUE').upper()}_INVALID")
    return value


def quantity_schema(schema: Any, *, key: str | None = None,
                    parent: str | None = None) -> Any:
    """Annotate a JSON schema with accepted CAD quantity strings."""
    if not isinstance(schema, dict):
        return schema
    result = {name: quantity_schema(value, key=name, parent=key)
              for name, value in schema.items()
              if name not in {"properties", "items", "additionalProperties", "oneOf", "anyOf"}}
    for name in ("properties", "additionalProperties"):
        if name in schema:
            child = schema[name]
            if isinstance(child, dict) and name == "properties":
                result[name] = {child_key: quantity_schema(child_schema, key=str(child_key), parent=key)
                                 for child_key, child_schema in child.items()}
            else:
                result[name] = quantity_schema(child, key=key, parent=parent)
    for name in ("items",):
        if name in schema:
            result[name] = quantity_schema(schema[name], key=key, parent=parent)
    for name in ("oneOf", "anyOf"):
        if name in schema and isinstance(schema[name], list):
            result[name] = [quantity_schema(item, key=key, parent=parent) for item in schema[name]]
    expected = schema.get("type")
    dimension = dimension_for_key(key, parent)
    if expected == "number" and dimension is not None:
        result["type"] = ["number", "string"]
        result["x-quantity-dimension"] = dimension
        result["x-quantity-format"] = "number or '<value><unit>'"
    return result


__all__ = ["dimension_for_key", "normalize_cad_arguments", "parse_quantity", "quantity_schema"]
