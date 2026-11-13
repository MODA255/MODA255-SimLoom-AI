from __future__ import annotations

"""Vendor-neutral 2D CAD document objects and editing primitives.

The project deliberately keeps this layer independent from a commercial CAD
application.  A document can therefore be inspected, edited, validated and
diffed on any development machine, then exported to DXF for a Windows bridge.
"""

import copy
import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .dxf import bbox, diff_documents, read_dxf


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _point(value: Any) -> dict[str, float]:
    if isinstance(value, (list, tuple)):
        value = {"x": value[0] if len(value) > 0 else 0, "y": value[1] if len(value) > 1 else 0, "z": value[2] if len(value) > 2 else 0}
    value = value or {}
    return {"x": _number(value.get("x")), "y": _number(value.get("y")), "z": _number(value.get("z"))}


@dataclass(frozen=True)
class EntityRef:
    document_id: str
    entity_id: str

    def to_dict(self) -> dict[str, str]:
        return {"document_id": self.document_id, "entity_id": self.entity_id}


@dataclass
class Layer:
    name: str
    color: str = "#6b7280"
    visible: bool = True
    locked: bool = False
    linetype: str = "CONTINUOUS"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "color": self.color, "visible": self.visible, "locked": self.locked, "linetype": self.linetype}


@dataclass
class Block:
    name: str
    base_point: dict[str, float] = field(default_factory=dict)
    entities: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "base_point": _point(self.base_point), "entities": copy.deepcopy(self.entities)}


@dataclass
class Constraint:
    kind: str
    refs: list[str] = field(default_factory=list)
    value: float | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "refs": list(self.refs), "value": self.value, "parameters": copy.deepcopy(self.parameters)}


@dataclass
class SelectionSet:
    name: str
    refs: list[str] = field(default_factory=list)
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "refs": list(self.refs), "active": self.active}


@dataclass
class CadDocument:
    document_id: str = field(default_factory=lambda: "cad-" + uuid.uuid4().hex[:12])
    name: str = "untitled"
    units: str = "mm"
    entities: list[dict[str, Any]] = field(default_factory=list)
    layers: dict[str, Layer] = field(default_factory=dict)
    blocks: dict[str, Block] = field(default_factory=dict)
    coordinate_systems: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    selection_sets: dict[str, SelectionSet] = field(default_factory=dict)
    sheets: dict[str, dict[str, Any]] = field(default_factory=dict)
    viewports: dict[str, dict[str, Any]] = field(default_factory=dict)
    text_styles: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "simloom-ai/cad-document/v1"

    _source_path: str | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_dxf(cls, payload: dict[str, Any], *, name: str = "untitled", document_id: str | None = None) -> "CadDocument":
        entities = []
        for index, original in enumerate(payload.get("entities", []), 1):
            entity = copy.deepcopy(original)
            entity["id"] = str(entity.get("id") or entity.get("handle") or f"e{index:05d}")
            entity.setdefault("layer", "0")
            entities.append(entity)
        layer_names = set(payload.get("layers", {})) | {str(item.get("layer", "0")) for item in entities}
        layers = {}
        for layer_name in sorted(layer_names or {"0"}):
            style = payload.get("layer_styles", {}).get(layer_name, {})
            layers[layer_name] = Layer(layer_name, style.get("color", "#6b7280"), style.get("visible", True), style.get("locked", False), style.get("linetype", "CONTINUOUS"))
        blocks = {}
        for block_name, original in (payload.get("blocks") or {}).items():
            block_entities = []
            for index, entity in enumerate(original.get("entities", []), 1):
                item = copy.deepcopy(entity)
                item["id"] = str(item.get("id") or item.get("handle") or f"block:{block_name}:e{index:04d}")
                block_entities.append(item)
            blocks[str(block_name)] = Block(str(block_name), _point(original.get("base_point")), block_entities)
        metadata = {"source_format": "DXF", "source_entity_count": len(entities), "dxf_backend": payload.get("backend", "legacy-ascii")}
        if payload.get("annotation_scale") is not None:
            metadata["annotation_scale"] = payload.get("annotation_scale")
        return cls(document_id=document_id or "cad-" + uuid.uuid4().hex[:12], name=name,
                   entities=entities, layers=layers, blocks=blocks,
                   coordinate_systems=copy.deepcopy(payload.get("coordinate_systems", [])),
                   units=payload.get("units", "mm"), sheets=copy.deepcopy(payload.get("sheets", {})), viewports=copy.deepcopy(payload.get("viewports", {})),
                   text_styles=copy.deepcopy(payload.get("text_styles", {})),
                   metadata=metadata)

    @classmethod
    def open(cls, path: str | Path) -> "CadDocument":
        path = Path(path)
        sidecar_path = Path(str(path) + ".cad.json")
        sidecar_payload = None
        stale_sidecar = False
        if sidecar_path.is_file():
            sidecar_payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
            expected_hash = sidecar_payload.get("source_sha256")
            stale_sidecar = bool(expected_hash and hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash)
        native = read_dxf(path, respect_redraw_order=not stale_sidecar)
        document = cls.from_dxf(native, name=path.name)
        document._source_path = str(path) if native.get("backend") == "ezdxf" else None
        if sidecar_payload is not None:
            payload = sidecar_payload
            expected_hash = payload.get("source_sha256")
            if stale_sidecar:
                document.metadata["sidecar_warning"] = "CAD_SIDECAR_STALE"
                return document
            document.document_id = str(payload.get("document_id", document.document_id))
            document.units = str(payload.get("units", document.units))
            document.metadata.update(payload.get("metadata", {}))
            document.coordinate_systems = copy.deepcopy(payload.get("coordinate_systems", document.coordinate_systems))
            document.sheets = copy.deepcopy(payload.get("sheets", document.sheets))
            document.viewports = copy.deepcopy(payload.get("viewports", document.viewports))
            document.text_styles = copy.deepcopy(payload.get("text_styles", document.text_styles))
            # Native DXFs carry IDs in application data; never assign by file
            # order after an external program has reordered entities.
            if document._source_path is None:
                for entity, entity_id in zip(document.entities, payload.get("entity_ids", [])):
                    entity["id"] = str(entity_id)
            for entity in document.entities:
                metadata = (payload.get("entity_metadata") or {}).get(str(entity.get("id")))
                if isinstance(metadata, dict):
                    entity.update(copy.deepcopy(metadata))
            document.selection_sets = {key: SelectionSet(key, list(item.get("refs", [])), bool(item.get("active", True))) for key, item in (payload.get("selection_sets") or {}).items()}
            document.constraints = [Constraint(str(item.get("kind", "unknown")), list(item.get("refs", [])), item.get("value"), item.get("parameters", {})) for item in payload.get("constraints", [])]
            for key, item in (payload.get("layers") or {}).items():
                document.layers[key] = Layer(key, item.get("color", "#6b7280"), bool(item.get("visible", True)), bool(item.get("locked", False)), item.get("linetype", "CONTINUOUS"))
        return document

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CadDocument":
        """Rehydrate a canonical document snapshot without reading a file.

        Batch checkpoints are deliberately file-independent JSON snapshots.  A
        small constructor here lets the read-only replay viewer use the same
        object model and validators as a live DXF document.
        """
        if not isinstance(payload, dict):
            raise ValueError("CAD_DOCUMENT_SNAPSHOT_INVALID")
        document = cls.from_dxf(payload, name=str(payload.get("name", "untitled")),
                                document_id=str(payload.get("document_id") or ""))
        document.schema_version = str(payload.get("schema_version", document.schema_version))
        document.units = str(payload.get("units", document.units))
        document.metadata = copy.deepcopy(payload.get("metadata") or {})
        document.coordinate_systems = copy.deepcopy(payload.get("coordinate_systems") or [])
        document.sheets = copy.deepcopy(payload.get("sheets") or {})
        document.viewports = copy.deepcopy(payload.get("viewports") or {})
        document.text_styles = copy.deepcopy(payload.get("text_styles") or {})
        document.layers = {
            str(name): Layer(str(name), str(item.get("color", "#6b7280")),
                             bool(item.get("visible", True)), bool(item.get("locked", False)),
                             str(item.get("linetype", "CONTINUOUS")))
            for name, item in (payload.get("layers") or {}).items()
            if isinstance(item, dict)
        }
        for entity in document.entities:
            document.ensure_layer(str(entity.get("layer", "0")))
        document.blocks = {
            str(name): Block(str(name), _point(item.get("base_point")), copy.deepcopy(item.get("entities") or []))
            for name, item in (payload.get("blocks") or {}).items()
            if isinstance(item, dict)
        }
        document.selection_sets = {
            str(name): SelectionSet(str(name), list(item.get("refs") or []), bool(item.get("active", True)))
            for name, item in (payload.get("selection_sets") or {}).items()
            if isinstance(item, dict)
        }
        document.constraints = [
            Constraint(str(item.get("kind", "unknown")), list(item.get("refs") or []), item.get("value"),
                       copy.deepcopy(item.get("parameters") or {}))
            for item in (payload.get("constraints") or [])
            if isinstance(item, dict)
        ]
        return document

    def clone(self) -> "CadDocument":
        return copy.deepcopy(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "document_id": self.document_id,
            "name": self.name,
            "units": self.units,
            "entities": copy.deepcopy(self.entities),
            "layers": {name: layer.to_dict() for name, layer in sorted(self.layers.items())},
            "blocks": {name: block.to_dict() for name, block in sorted(self.blocks.items())},
            "coordinate_systems": copy.deepcopy(self.coordinate_systems),
            "constraints": [item.to_dict() for item in self.constraints],
            "selection_sets": {name: item.to_dict() for name, item in sorted(self.selection_sets.items())},
            "sheets": copy.deepcopy(self.sheets),
            "viewports": copy.deepcopy(self.viewports),
            "text_styles": copy.deepcopy(self.text_styles),
            "bbox": bbox(self.entities),
            "entity_count": len(self.entities),
            "entity_types": self._entity_counts(),
            "format": "CAD_DOCUMENT",
            "metadata": copy.deepcopy(self.metadata),
        }

    def to_dxf(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload["format"] = "DXF"
        payload["layers"] = {name: sum(1 for entity in self.entities if str(entity.get("layer", "0")) == name) for name in self.layers}
        payload["blocks"] = {name: block.to_dict() | {"entity_count": len(block.entities)} for name, block in self.blocks.items()}
        return payload

    def sidecar(self, source_path: str | Path | None = None) -> dict[str, Any]:
        entity_metadata = {}
        for entity in self.entities:
            native_keys = {"width_factor", "oblique", "text_generation_flag", "associative", "row_count", "column_count", "row_spacing", "column_spacing", "flags", "arrowhead", "tag", "prompt", "native_angles", "pattern_lines", "sheet", "extrusion", "style", "halign", "valign", "align_point", "attachment_point", "widths", "elevation", "const_width", "attributes", "dimension_type", "dimension_code", "dimstyle", "axis", "angle", "native_definition", "boundary_paths", "boundary_path_count", "id", "handle", "owner", "type", "layer", "start", "end", "unit_vector", "center", "radius", "start_angle", "end_angle", "point", "insert", "text", "height", "width", "rotation", "measurement", "block_name", "definition", "vertices", "closed", "pattern", "solid", "boundary_vertices", "major_axis", "ratio", "start_param", "end_param", "scale", "data", "raw_groups", "raw_tags", "linetype", "color_index", "true_color", "lineweight", "thickness", "bulges", "hatch_style", "pattern_type", "pattern_angle", "pattern_scale", "double", "degree", "fit_points", "control_points", "knots", "weights"}
            extras = {key: copy.deepcopy(value) for key, value in entity.items() if key not in native_keys}
            if extras:
                entity_metadata[str(entity.get("id"))] = extras
        return {"schema_version": self.schema_version, "document_id": self.document_id, "units": self.units,
                "source_sha256": hashlib.sha256(Path(source_path).read_bytes()).hexdigest() if source_path else None,
                "entity_handles": {str(item["handle"]): str(item["id"]) for item in self.entities if item.get("handle")},
                "entity_ids": [str(item.get("id")) for item in self.entities],
                "entity_metadata": entity_metadata,
                "layers": {name: item.to_dict() for name, item in self.layers.items()},
                "coordinate_systems": copy.deepcopy(self.coordinate_systems),
                "sheets": copy.deepcopy(self.sheets),
                "viewports": copy.deepcopy(self.viewports),
                "text_styles": copy.deepcopy(self.text_styles),
                "constraints": [item.to_dict() for item in self.constraints],
                "selection_sets": {name: item.to_dict() for name, item in self.selection_sets.items()},
                "metadata": copy.deepcopy(self.metadata)}

    def _entity_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entity in self.entities:
            kind = str(entity.get("type", "UNKNOWN")).upper()
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    def entity(self, entity_id: str) -> dict[str, Any]:
        for item in self.entities:
            if str(item.get("id")) == str(entity_id) or str(item.get("handle")) == str(entity_id):
                return item
        raise KeyError(f"ENTITY_NOT_FOUND:{entity_id}")

    def refs(self, ids: Iterable[str]) -> list[dict[str, Any]]:
        return [self.entity(str(item)) for item in ids]

    def next_id(self, prefix: str = "e") -> str:
        used = {str(item.get("id")) for item in self.entities}
        index = len(used) + 1
        candidate = f"{prefix}{index:05d}"
        while candidate in used:
            index += 1
            candidate = f"{prefix}{index:05d}"
        return candidate

    def ensure_layer(self, name: str) -> Layer:
        name = str(name or "0")
        if name not in self.layers:
            self.layers[name] = Layer(name)
        return self.layers[name]

    def diff(self, other: "CadDocument") -> dict[str, Any]:
        return diff_documents(self.to_dxf(), other.to_dxf())


def _transform_point(point: dict[str, Any], *, dx: float = 0, dy: float = 0, dz: float = 0, scale: float = 1.0, angle: float = 0, center: dict[str, Any] | None = None) -> dict[str, float]:
    center = _point(center)
    x, y, z = (_number(point.get("x")) - center["x"]) * scale, (_number(point.get("y")) - center["y"]) * scale, (_number(point.get("z")) - center["z"]) * scale
    radians = math.radians(angle)
    return {"x": x * math.cos(radians) - y * math.sin(radians) + center["x"] + dx,
            "y": x * math.sin(radians) + y * math.cos(radians) + center["y"] + dy,
            "z": z + center["z"] + dz}


def transform_entity(entity: dict[str, Any], *, dx: float = 0, dy: float = 0, dz: float = 0,
                     scale: float = 1.0, angle: float = 0, center: dict[str, Any] | None = None,
                     mirror_axis: str | None = None, allow_associative: bool = False) -> dict[str, Any]:
    from .native_dxf import transform_model
    return transform_model(entity, dx=dx, dy=dy, dz=dz, scale=scale, angle=angle, center=center,
                           mirror_axis=mirror_axis, allow_associative=allow_associative)


def entity_points(entity: dict[str, Any]) -> list[dict[str, float]]:
    kind = str(entity.get("type", "")).upper()
    if kind == "LINE":
        return [_point(entity.get("start")), _point(entity.get("end"))]
    if kind in {"RAY", "XLINE"}:
        return [_point(entity.get("start"))]
    if kind in {"CIRCLE", "ARC"}:
        center, radius = _point(entity.get("center")), abs(_number(entity.get("radius")))
        return [{"x": center["x"] - radius, "y": center["y"] - radius, "z": center["z"]}, {"x": center["x"] + radius, "y": center["y"] + radius, "z": center["z"]}]
    if kind == "POINT":
        return [_point(entity.get("point"))]
    if kind in {"TEXT", "MTEXT", "INSERT"}:
        return [_point(entity.get("insert"))]
    if kind == "DIMENSION":
        return [_point(entity.get("definition"))]
    if kind == "HATCH":
        return [_point(item) for item in entity.get("boundary_vertices", [])]
    if kind == "LEADER":
        return [_point(item) for item in entity.get("vertices", [])]
    if kind == "ELLIPSE":
        center, axis = _point(entity.get("center")), _point(entity.get("major_axis"))
        radius = math.sqrt(axis["x"] ** 2 + axis["y"] ** 2 + axis["z"] ** 2)
        return [{"x": center["x"] - radius, "y": center["y"] - radius, "z": center["z"]}, {"x": center["x"] + radius, "y": center["y"] + radius, "z": center["z"]}]
    if kind == "SPLINE":
        return [_point(item) for item in (entity.get("fit_points") or entity.get("control_points") or [])]
    if kind in {"LWPOLYLINE", "POLYLINE"}:
        return [_point(item) for item in entity.get("vertices", [])]
    return []


def _canonical_geometry(value: Any) -> Any:
    """Normalize an entity for duplicate-geometry checks without using IDs."""
    if isinstance(value, dict):
        ignored = {"id", "handle", "owner", "raw_tags", "raw_groups", "metadata"}
        return {key: _canonical_geometry(item) for key, item in sorted(value.items()) if key not in ignored}
    if isinstance(value, (list, tuple)):
        return [_canonical_geometry(item) for item in value]
    if isinstance(value, float):
        return round(value, 9)
    return value


def _polygon_area(points: list[Any]) -> float:
    values = [_point(point) for point in points]
    if len(values) < 3:
        return 0.0
    return 0.5 * sum(values[index]["x"] * values[(index + 1) % len(values)]["y"]
                     - values[(index + 1) % len(values)]["x"] * values[index]["y"]
                     for index in range(len(values)))


def _close_to_geometry(point: dict[str, float], entities: list[dict[str, Any]], tolerance: float) -> bool:
    for entity in entities:
        for candidate in entity_points(entity):
            if math.dist((point["x"], point["y"], point["z"]), (candidate["x"], candidate["y"], candidate["z"])) <= tolerance:
                return True
    return False


def _orientation(a: dict[str, Any], b: dict[str, Any], c: dict[str, Any]) -> float:
    return ((float(b.get("x", 0)) - float(a.get("x", 0))) * (float(c.get("y", 0)) - float(a.get("y", 0)))
            - (float(b.get("y", 0)) - float(a.get("y", 0))) * (float(c.get("x", 0)) - float(a.get("x", 0))))


def _on_segment(a: dict[str, Any], b: dict[str, Any], point: dict[str, Any], tolerance: float) -> bool:
    return (min(float(a.get("x", 0)), float(b.get("x", 0))) - tolerance <= float(point.get("x", 0)) <= max(float(a.get("x", 0)), float(b.get("x", 0))) + tolerance
            and min(float(a.get("y", 0)), float(b.get("y", 0))) - tolerance <= float(point.get("y", 0)) <= max(float(a.get("y", 0)), float(b.get("y", 0))) + tolerance)


def _segment_intersection_kind(first: tuple[dict[str, Any], dict[str, Any]], second: tuple[dict[str, Any], dict[str, Any]], tolerance: float) -> str | None:
    a, b = first
    c, d = second
    ab = _orientation(a, b, c)
    ad = _orientation(a, b, d)
    ca = _orientation(c, d, a)
    cb = _orientation(c, d, b)
    if abs(ab) <= tolerance and abs(ad) <= tolerance and abs(ca) <= tolerance and abs(cb) <= tolerance:
        axis = "x" if abs(float(b.get("x", 0)) - float(a.get("x", 0))) >= abs(float(b.get("y", 0)) - float(a.get("y", 0))) else "y"
        left = sorted((float(a.get(axis, 0)), float(b.get(axis, 0))))
        right = sorted((float(c.get(axis, 0)), float(d.get(axis, 0))))
        overlap = min(left[1], right[1]) - max(left[0], right[0])
        return "overlap" if overlap > tolerance else None
    if ((ab > tolerance and ad < -tolerance) or (ab < -tolerance and ad > tolerance)) and ((ca > tolerance and cb < -tolerance) or (ca < -tolerance and cb > tolerance)):
        return "crossing"
    if abs(ab) <= tolerance and _on_segment(a, b, c, tolerance):
        return "touching"
    if abs(ad) <= tolerance and _on_segment(a, b, d, tolerance):
        return "touching"
    if abs(ca) <= tolerance and _on_segment(c, d, a, tolerance):
        return "touching"
    if abs(cb) <= tolerance and _on_segment(c, d, b, tolerance):
        return "touching"
    return None


def _drafting_warnings(dimensions: list[dict[str, Any]], tolerance: float) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for index, first in enumerate(dimensions):
        first_id = str(first.get("id"))
        first_start, first_end = first.get("start"), first.get("end")
        if not isinstance(first_start, dict) or not isinstance(first_end, dict):
            continue
        for second in dimensions[index + 1:]:
            second_id = str(second.get("id"))
            second_start, second_end = second.get("start"), second.get("end")
            if not isinstance(second_start, dict) or not isinstance(second_end, dict):
                continue
            kind = _segment_intersection_kind((first_start, first_end), (second_start, second_end), tolerance)
            if kind in {"crossing", "overlap"}:
                findings.append({"code": "DIMENSION_" + kind.upper(), "entity_ids": [first_id, second_id]})
            first_definition, second_definition = first.get("definition"), second.get("definition")
            if isinstance(first_definition, dict) and isinstance(second_definition, dict):
                if math.dist((float(first_definition.get("x", 0)), float(first_definition.get("y", 0))),
                             (float(second_definition.get("x", 0)), float(second_definition.get("y", 0)))) <= tolerance:
                    findings.append({"code": "DIMENSION_LABEL_OVERLAP", "entity_ids": [first_id, second_id]})
    return findings


def validate_cad_document(document: CadDocument, requirements: dict[str, Any] | None = None) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[str] = []
    duplicate_geometry: list[dict[str, Any]] = []
    requirements = requirements or {}
    ids: set[str] = set()
    geometry_entities: list[dict[str, Any]] = []
    geometry_keys: dict[str, list[str]] = {}
    dimension_entities: list[dict[str, Any]] = []
    association_tolerance = max(_number(requirements.get("dimension_tolerance"), 1e-6), 1e-9)
    for index, entity in enumerate(document.entities):
        entity_id = str(entity.get("id") or "")
        if not entity_id:
            errors.append({"code": "ENTITY_WITHOUT_ID", "index": index})
        elif entity_id in ids:
            errors.append({"code": "DUPLICATE_ENTITY_ID", "entity_id": entity_id, "index": index})
        ids.add(entity_id)
        layer = str(entity.get("layer", "0"))
        if layer not in document.layers:
            errors.append({"code": "MISSING_LAYER", "entity_id": entity_id, "layer": layer})
        kind = str(entity.get("type", "")).upper()
        if kind not in {"TEXT", "MTEXT", "DIMENSION", "HATCH", "INSERT"}:
            signature = json.dumps(_canonical_geometry(entity), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            geometry_keys.setdefault(signature, []).append(entity_id)
            geometry_entities.append(entity)
        if kind == "LINE":
            start, end = _point(entity.get("start")), _point(entity.get("end"))
            if start == end:
                errors.append({"code": "ZERO_LENGTH_LINE", "entity_id": entity_id})
        elif kind in {"CIRCLE", "ARC"} and _number(entity.get("radius")) <= 0:
            errors.append({"code": "INVALID_RADIUS", "entity_id": entity_id})
        elif kind in {"LWPOLYLINE", "POLYLINE"}:
            points = entity.get("vertices") or []
            if len(points) < 2:
                errors.append({"code": "POLYLINE_TOO_SHORT", "entity_id": entity_id})
            if entity.get("closed") and (len(points) < 2 or (len(points) == 2 and not any(entity.get("bulges", [])))):
                errors.append({"code": "CLOSED_POLYLINE_TOO_SHORT", "entity_id": entity_id})
            if entity.get("closed") and len(points) >= 3 and abs(_polygon_area(points)) <= association_tolerance ** 2:
                errors.append({"code": "CLOSED_POLYLINE_DEGENERATE", "entity_id": entity_id})
        elif kind == "INSERT" and str(entity.get("block_name", "")) not in document.blocks:
            errors.append({"code": "INSERT_WITHOUT_BLOCK", "entity_id": entity_id, "block_name": entity.get("block_name")})
        elif kind == "HATCH" and not entity.get("boundary_paths") and len(entity.get("boundary_vertices") or []) < 3:
            errors.append({"code": "HATCH_BOUNDARY_TOO_SHORT", "entity_id": entity_id})
        elif kind == "LEADER" and len(entity.get("vertices") or []) < 2:
            errors.append({"code": "LEADER_TOO_SHORT", "entity_id": entity_id})
        elif kind == "ELLIPSE" and not 0 < _number(entity.get("ratio")) <= 1:
            errors.append({"code": "INVALID_ELLIPSE_RATIO", "entity_id": entity_id})
        elif kind == "SPLINE":
            points = entity.get("fit_points") or entity.get("control_points") or []
            degree = int(_number(entity.get("degree"), 3))
            if len(points) < 2:
                errors.append({"code": "SPLINE_TOO_SHORT", "entity_id": entity_id})
            if degree < 1 or degree > 11:
                errors.append({"code": "INVALID_SPLINE_DEGREE", "entity_id": entity_id, "degree": degree})
        elif kind in {"TEXT", "MTEXT"}:
            has_height = "height" in entity
            height = _number(entity.get("height"), 0)
            if has_height and (not math.isfinite(height) or height <= 0):
                errors.append({"code": "INVALID_TEXT_HEIGHT", "entity_id": entity_id, "height": height})
            elif not has_height:
                warnings.append("TEXT_HEIGHT_MISSING:" + entity_id)
            minimum_height = requirements.get("min_text_height")
            if minimum_height is not None and (not has_height or height < float(minimum_height)):
                errors.append({"code": "TEXT_TOO_SMALL", "entity_id": entity_id,
                               "height": height, "minimum": float(minimum_height)})
            if not str(entity.get("text", "")).strip():
                warnings.append("EMPTY_TEXT:" + entity_id)
        elif kind == "DIMENSION":
            dimension_entities.append(entity)
            measurement = entity.get("measurement")
            if measurement is not None and (not math.isfinite(_number(measurement)) or _number(measurement) < 0):
                errors.append({"code": "INVALID_DIMENSION_MEASUREMENT", "entity_id": entity_id, "measurement": measurement})
            refs = entity.get("refs") or entity.get("entity_ids") or entity.get("associative_refs") or []
            missing_refs = [str(ref) for ref in refs if str(ref) not in ids and not any(str(item.get("id")) == str(ref) for item in document.entities)]
            if missing_refs:
                errors.append({"code": "DIMENSION_REF_NOT_FOUND", "entity_id": entity_id, "refs": missing_refs})
        for point in entity_points(entity):
            if not all(math.isfinite(_number(point.get(axis))) for axis in ("x", "y", "z")):
                errors.append({"code": "NON_FINITE_COORDINATE", "entity_id": entity_id})
                break
    handles = {str(item.get("handle")): str(item.get("id")) for item in document.entities if item.get("handle")}
    for entity in document.entities:
        if str(entity.get("type", "")).upper() != "HATCH" or not entity.get("associative"):
            continue
        hatch_id = str(entity.get("id"))
        source_handles = [str(handle)
                          for path in entity.get("boundary_paths", [])
                          for handle in path.get("source_handles", [])]
        missing = sorted({handle for handle in source_handles if handle not in handles})
        if missing:
            errors.append({"code": "HATCH_ASSOCIATIVE_REF_NOT_FOUND", "entity_id": hatch_id, "handles": missing})
    for signature, entity_ids in geometry_keys.items():
        if len(entity_ids) > 1:
            item = {"entity_ids": entity_ids, "count": len(entity_ids)}
            duplicate_geometry.append(item)
            if requirements.get("reject_duplicate_geometry"):
                errors.append({"code": "DUPLICATE_GEOMETRY", **item})
            else:
                warnings.append("DUPLICATE_GEOMETRY:" + ",".join(entity_ids))
    drafting_warnings = _drafting_warnings(dimension_entities, association_tolerance)
    for finding in drafting_warnings:
        if requirements.get("reject_drafting_warnings"):
            errors.append(finding)
        else:
            warnings.append(f"{finding['code']}:{','.join(finding['entity_ids'])}")
    if requirements.get("require_dimension_association"):
        for entity in dimension_entities:
            entity_id = str(entity.get("id"))
            refs = entity.get("refs") or entity.get("entity_ids") or entity.get("associative_refs") or []
            if refs:
                continue
            points = entity_points(entity)
            if not points or not any(_close_to_geometry(point, geometry_entities, association_tolerance) for point in points):
                errors.append({"code": "DIMENSION_NOT_ASSOCIATED", "entity_id": entity_id,
                               "tolerance": association_tolerance})
    for constraint in document.constraints:
        missing = [ref for ref in constraint.refs if ref not in ids]
        if missing:
            errors.append({"code": "CONSTRAINT_REF_NOT_FOUND", "refs": missing, "kind": constraint.kind})
    for sheet_name, sheet in document.sheets.items():
        if not str(sheet_name).strip():
            errors.append({"code": "SHEET_NAME_EMPTY"})
        for key in ("width", "height"):
            if _number(sheet.get(key), 0) <= 0:
                errors.append({"code": "INVALID_SHEET_SIZE", "sheet": sheet_name, "field": key})
    for viewport_name, viewport in document.viewports.items():
        sheet_name = str(viewport.get("sheet", ""))
        if sheet_name not in document.sheets:
            errors.append({"code": "VIEWPORT_SHEET_NOT_FOUND", "viewport": viewport_name, "sheet": sheet_name})
        for key in ("width", "height", "scale"):
            if _number(viewport.get(key), 0) <= 0:
                errors.append({"code": "INVALID_VIEWPORT_VALUE", "viewport": viewport_name, "field": key})
    for layer in requirements.get("required_layers", []):
        if str(layer) not in document.layers:
            errors.append({"code": "REQUIRED_LAYER_MISSING", "layer": str(layer)})
    entity_types = {str(item.get("type", "")).upper() for item in document.entities}
    for entity_type in requirements.get("required_entity_types", []):
        if str(entity_type).upper() not in entity_types:
            errors.append({"code": "REQUIRED_ENTITY_TYPE_MISSING", "entity_type": str(entity_type).upper()})
    for text in requirements.get("required_text", []):
        needle = str(text)
        if not any(needle in str(item.get("text", "")) for item in document.entities):
            errors.append({"code": "REQUIRED_TEXT_MISSING", "text": needle})
    minimum = requirements.get("min_entity_count")
    maximum = requirements.get("max_entity_count")
    if minimum is not None and len(document.entities) < int(minimum):
        errors.append({"code": "ENTITY_COUNT_BELOW_MINIMUM", "minimum": int(minimum), "actual": len(document.entities)})
    if maximum is not None and len(document.entities) > int(maximum):
        errors.append({"code": "ENTITY_COUNT_ABOVE_MAXIMUM", "maximum": int(maximum), "actual": len(document.entities)})
    for sheet in requirements.get("required_sheets", []):
        if str(sheet) not in document.sheets:
            errors.append({"code": "REQUIRED_SHEET_MISSING", "sheet": str(sheet)})
    for viewport in requirements.get("required_viewports", []):
        if str(viewport) not in document.viewports:
            errors.append({"code": "REQUIRED_VIEWPORT_MISSING", "viewport": str(viewport)})
    required_bbox = requirements.get("bbox_contains") or {}
    actual_bbox = bbox(document.entities)
    for axis in ("x", "y", "z"):
        bounds = required_bbox.get(axis) or {}
        if not bounds:
            continue
        actual = actual_bbox.get(axis)
        if not actual or ("min" in bounds and actual["min"] > float(bounds["min"])) or ("max" in bounds and actual["max"] < float(bounds["max"])):
            errors.append({"code": "BBOX_REQUIREMENT_FAILED", "axis": axis, "required": bounds, "actual": actual or {}})
    if not document.entities:
        warnings.append("DOCUMENT_HAS_NO_ENTITIES")
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "duplicate_geometry": duplicate_geometry, "entity_count": len(document.entities),
            "layer_count": len(document.layers), "dimension_count": len(dimension_entities),
            "drafting_warnings": drafting_warnings}

