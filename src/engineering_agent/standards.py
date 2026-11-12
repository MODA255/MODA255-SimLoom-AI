"""Small, explicit annotation catalog used for planning and input validation.

The catalog is intentionally a capability contract, not a claim of ISO/ASME
compliance. Native CAD software remains authoritative for standard libraries.
"""
from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "simloom-ai/standards/v1"

_CATALOG: dict[str, dict[str, Any]] = {
    "ISO": {
        "symbols": ["ARROW", "CENTER", "DATUM", "SECTION", "SURFACE", "WELD"],
        "fasteners": {"bolt", "nut", "washer", "screw"},
        "notes": "Built-in annotation vocabulary for planning and preview; native software validates the final standard.",
    },
    "ASME": {
        "symbols": ["ARROW", "CENTER", "DATUM", "SECTION", "SURFACE", "WELD"],
        "fasteners": {"bolt", "nut", "washer", "screw"},
        "notes": "Built-in annotation vocabulary for planning and preview; native software validates the final standard.",
    },
}

_PID_SYMBOLS = [
    "ACTUATOR", "COMPRESSOR", "CONTROL_VALVE", "ELBOW", "FAN", "FILTER",
    "FLANGE", "FLOW_METER", "HEAT_EXCHANGER", "LEVEL_GAUGE", "MIXER",
    "NOZZLE", "PUMP", "PRESSURE_GAUGE", "REDUCER", "RELIEF_VALVE",
    "SENSOR", "STRAINER", "TANK", "TEE", "TEMPERATURE_GAUGE", "VALVE",
    "VESSEL", "CHECK_VALVE",
]
_GDT_CHARACTERISTICS = [
    "STRAIGHTNESS", "FLATNESS", "CIRCULARITY", "CYLINDRICITY", "LINE_PROFILE",
    "SURFACE_PROFILE", "ANGULARITY", "PERPENDICULARITY", "PARALLELISM", "POSITION",
    "CONCENTRICITY", "SYMMETRY", "CIRCULAR_RUNOUT", "TOTAL_RUNOUT",
]
_FASTENER_KINDS = ["bolt", "nut", "washer", "screw"]
_METRIC_SIZES = [f"M{size}" for size in (3, 4, 5, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24)]
_BEARING_SERIES = ["6000", "6200", "6300"]
_HOLE_TYPES = ["clearance", "tapped", "counterbored", "countersunk", "spotface"]


def catalog(standard: str | None = None) -> dict[str, Any]:
    names = [standard.upper()] if standard else sorted(_CATALOG)
    unknown = [name for name in names if name not in _CATALOG]
    if unknown:
        raise ValueError("STANDARD_NOT_FOUND:" + ",".join(unknown))
    return {
        "schema_version": SCHEMA_VERSION,
        "standards": {
            name: {
                "symbols": list(_CATALOG[name]["symbols"]),
                "fasteners": sorted(_CATALOG[name]["fasteners"]),
                "notes": _CATALOG[name]["notes"],
            }
            for name in names
        },
    }


def standard_library(category: str | None = None, query: str | None = None) -> dict[str, Any]:
    """Return a deterministic capability library for planning and validation."""
    groups: dict[str, Any] = {
        "pid_symbols": list(_PID_SYMBOLS),
        "gdt_characteristics": list(_GDT_CHARACTERISTICS),
        "fasteners": [{"kind": kind, "sizes": list(_METRIC_SIZES)} for kind in _FASTENER_KINDS],
        "bearings": [{"series": series, "size_range": "common metric series"} for series in _BEARING_SERIES],
        "holes": list(_HOLE_TYPES),
    }
    if category:
        category = str(category).lower()
        if category not in groups:
            raise ValueError("STANDARD_LIBRARY_CATEGORY_NOT_FOUND:" + category)
        groups = {category: groups[category]}
    if query:
        needle = str(query).lower()
        groups = {name: [item for item in values if needle in str(item).lower()]
                  if isinstance(values, list) else values for name, values in groups.items()}
    return {"schema_version": SCHEMA_VERSION, "library_version": "capability-v1", "categories": groups,
            "notes": "Capability catalog for agent planning and local validation; native CAD software remains authoritative."}


def validate_pid_symbol(symbol: str) -> None:
    value = str(symbol or "").upper()
    if value not in _PID_SYMBOLS:
        raise ValueError("PID_SYMBOL_NOT_FOUND:" + value)


def validate_gdt_characteristic(characteristic: str) -> None:
    value = str(characteristic or "").upper()
    if value not in _GDT_CHARACTERISTICS:
        raise ValueError("GDT_CHARACTERISTIC_NOT_FOUND:" + value)


def drawing_parts(document: dict[str, Any]) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    for entity in document.get("entities", []):
        if entity.get("fastener"):
            parts.append({"entity_id": str(entity.get("id")), "category": "fasteners", **dict(entity["fastener"])})
        if entity.get("pid_symbol"):
            parts.append({"entity_id": str(entity.get("id")), "category": "pid_symbols", "symbol": str(entity["pid_symbol"])})
        if entity.get("standard_symbol"):
            parts.append({"entity_id": str(entity.get("id")), "category": "standard_symbol", **dict(entity["standard_symbol"])})
        if entity.get("gdt"):
            parts.append({"entity_id": str(entity.get("id")), "category": "gdt", **dict(entity["gdt"])})
        if entity.get("surface_texture"):
            parts.append({"entity_id": str(entity.get("id")), "category": "surface_texture", **dict(entity["surface_texture"])})
    counts: dict[str, int] = {}
    for item in parts:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return {"schema_version": SCHEMA_VERSION, "parts": parts, "counts": counts, "part_count": len(parts)}


def validate_symbol(standard: str, symbol: str) -> None:
    standard_name, symbol_name = str(standard or "ISO").upper(), str(symbol or "").upper()
    if standard_name not in _CATALOG:
        raise ValueError("STANDARD_NOT_FOUND:" + standard_name)
    if symbol_name not in _CATALOG[standard_name]["symbols"]:
        raise ValueError(f"STANDARD_SYMBOL_NOT_FOUND:{standard_name}:{symbol_name}")


def validate_fastener(standard: str, kind: str) -> None:
    standard_name, kind_name = str(standard or "ISO").upper(), str(kind or "bolt").lower()
    if standard_name not in _CATALOG:
        raise ValueError("STANDARD_NOT_FOUND:" + standard_name)
    if kind_name not in _CATALOG[standard_name]["fasteners"]:
        raise ValueError(f"STANDARD_FASTENER_NOT_FOUND:{standard_name}:{kind_name}")


def validate_fastener_size(size: str) -> None:
    if str(size or "").upper() not in _METRIC_SIZES:
        raise ValueError("FASTENER_SIZE_NOT_FOUND:" + str(size))
