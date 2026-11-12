from __future__ import annotations

import csv
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from .object_model import ArtifactRef, AssetKind, EngineeringObject, EngineeringProject
from .dxf import read_dxf
from .cad_model import CadDocument
from .mesh import read_obj


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArtifactReader:
    """Read real project artifacts into the vendor-neutral object model.

    STEP parsing is intentionally conservative: it indexes entities, units and
    Cartesian points without claiming to be a B-Rep kernel. A licensed CAD or
    OpenCascade backend can enrich the same object later.
    """

    def read(self, path: str | Path, kind: str | None = None) -> EngineeringObject:
        path = Path(path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        suffix = path.suffix.lower()
        detected = kind or ({".step": "geometry", ".stp": "geometry", ".dxf": "geometry", ".obj": "geometry", ".csv": "table", ".json": "result", ".svg": "geometry", ".pdf": "result"}.get(suffix, "unknown"))
        source = ArtifactRef(str(path), _sha256(path), path.stat().st_size, self._media_type(suffix))
        if detected == "geometry":
            if suffix == ".dxf":
                return self._dxf(path, source)
            if suffix == ".svg":
                return self._svg(path, source)
            if suffix == ".obj":
                return self._obj(path, source)
            return self._step(path, source)
        if detected == "table":
            return self._csv(path, source)
        if detected == "result":
            if suffix == ".pdf":
                return self._pdf(path, source)
            return self._json(path, source)
        return EngineeringObject(object_id=str(uuid.uuid4()), name=path.stem, kind=AssetKind.UNKNOWN, source=source, metadata={"suffix": suffix})

    def project(self, root: str | Path) -> EngineeringProject:
        root = Path(root).resolve()
        assets = []
        for path in sorted(item for item in root.rglob("*") if item.is_file() and item.suffix.lower() in {".step", ".stp", ".dxf", ".obj", ".svg", ".pdf", ".csv", ".json"}):
            try:
                assets.append(self.read(path))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return EngineeringProject(project_id=str(uuid.uuid4()), name=root.name, root=str(root), assets=assets)

    def _step(self, path: Path, source: ArtifactRef) -> EngineeringObject:
        text = path.read_text(encoding="utf-8", errors="replace")
        entities = re.findall(r"#\d+\s*=\s*([A-Z0-9_]+)", text.upper())
        counts: dict[str, int] = {}
        for entity in entities:
            counts[entity] = counts.get(entity, 0) + 1
        points = []
        pattern = re.compile(r"CARTESIAN_POINT\s*\([^;]*?\(\s*([-+0-9.Ee]+)\s*,\s*([-+0-9.Ee]+)\s*,\s*([-+0-9.Ee]+)\s*\)\s*\)", re.I)
        for match in pattern.finditer(text):
            points.append([float(match.group(1)), float(match.group(2)), float(match.group(3))])
        bbox = {}
        if points:
            bbox = {axis: {"min": min(point[index] for point in points), "max": max(point[index] for point in points)} for index, axis in enumerate(("x", "y", "z"))}
        product = re.search(r"FILE_NAME\s*\(\s*'([^']+)'", text, re.I)
        return EngineeringObject(
            object_id=str(uuid.uuid4()), name=path.stem, kind=AssetKind.GEOMETRY, source=source,
            geometry={"format": "STEP", "entity_count": len(entities), "entity_types": counts, "cartesian_point_count": len(points), "bbox": bbox},
            metadata={"file_name": product.group(1) if product else path.name},
        )

    def _csv(self, path: Path, source: ArtifactRef) -> EngineeringObject:
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
        columns = reader.fieldnames or []
        numeric: dict[str, dict[str, float]] = {}
        for column in columns:
            values = []
            for row in rows:
                try:
                    values.append(float(row.get(column, "")))
                except (TypeError, ValueError):
                    pass
            if values:
                numeric[column] = {"min": min(values), "max": max(values), "mean": sum(values) / len(values), "count": len(values)}
        return EngineeringObject(object_id=str(uuid.uuid4()), name=path.stem, kind=AssetKind.TABLE, source=source, tables={"columns": columns, "row_count": len(rows), "numeric": numeric, "sample": rows[:20]}, metadata={"format": "CSV"})

    def _json(self, path: Path, source: ArtifactRef) -> EngineeringObject:
        value = json.loads(path.read_text(encoding="utf-8"))
        payload = value if isinstance(value, dict) else {"value": value}
        metrics = self._flatten_numeric(payload)
        return EngineeringObject(object_id=str(uuid.uuid4()), name=path.stem, kind=AssetKind.RESULT, source=source, metrics=metrics, metadata={"format": "JSON", "top_level_keys": list(payload) if isinstance(payload, dict) else []})

    def _dxf(self, path: Path, source: ArtifactRef) -> EngineeringObject:
        cad_document = CadDocument.open(path)
        document = cad_document.to_dxf()
        return EngineeringObject(
            object_id=str(uuid.uuid4()),
            name=path.stem,
            kind=AssetKind.GEOMETRY,
            source=source,
            geometry={
                "format": "DXF",
                "entity_types": document["entity_types"],
                "entity_count": len(document["entities"]),
                "coordinate_value_count": sum(len(_entity_points(item)) for item in document["entities"]),
                "layers": document["layers"],
                "entities": document["entities"],
                "bbox": document["bbox"],
                "blocks": document.get("blocks", {}),
                "coordinate_systems": document.get("coordinate_systems", []),
                "topology": document.get("topology", {}),
                "cad_document": cad_document.to_dict(),
            },
            metadata={"format": "DXF", "cad_schema_version": cad_document.schema_version},
        )

    def _svg(self, path: Path, source: ArtifactRef) -> EngineeringObject:
        text = path.read_text(encoding="utf-8", errors="replace")
        return EngineeringObject(object_id=str(uuid.uuid4()), name=path.stem, kind=AssetKind.GEOMETRY, source=source, geometry={"format": "SVG", "line_count": text.count("<line"), "circle_count": text.count("<circle"), "path_count": text.count("<path")}, metadata={"format": "SVG"})

    def _obj(self, path: Path, source: ArtifactRef) -> EngineeringObject:
        mesh = read_obj(path)
        return EngineeringObject(object_id=str(uuid.uuid4()), name=path.stem, kind=AssetKind.GEOMETRY, source=source, geometry=mesh, metadata={"format": "OBJ", "renderable": True})

    def _pdf(self, path: Path, source: ArtifactRef) -> EngineeringObject:
        data = path.read_bytes()
        return EngineeringObject(object_id=str(uuid.uuid4()), name=path.stem, kind=AssetKind.RESULT, source=source, metadata={"format": "PDF", "pdf_header": data[:8].decode("latin1", errors="replace"), "pages_hint": data.count(b"/Type /Page")})

    @staticmethod
    def _flatten_numeric(value: Any, prefix: str = "") -> dict[str, float]:
        result: dict[str, float] = {}
        if isinstance(value, dict):
            for key, child in value.items():
                result.update(ArtifactReader._flatten_numeric(child, f"{prefix}.{key}" if prefix else str(key)))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result[prefix] = float(value)
        return result

    @staticmethod
    def _media_type(suffix: str) -> str:
        return {".step": "model/step", ".stp": "model/step", ".dxf": "image/vnd.dxf", ".obj": "model/obj", ".svg": "image/svg+xml", ".csv": "text/csv", ".json": "application/json", ".pdf": "application/pdf"}.get(suffix, "application/octet-stream")


def _entity_points(entity: dict[str, Any]) -> list[dict[str, Any]]:
    kind = entity.get("type")
    if kind == "LINE":
        return [entity.get("start", {}), entity.get("end", {})]
    if kind in {"CIRCLE", "ARC"}:
        return [entity.get("center", {})]
    if kind == "POINT":
        return [entity.get("point", {})]
    if kind == "TEXT":
        return [entity.get("insert", {})]
    if kind == "DIMENSION":
        return [entity.get("definition", {})]
    if kind in {"LWPOLYLINE", "POLYLINE"}:
        return list(entity.get("vertices", []))
    return []
