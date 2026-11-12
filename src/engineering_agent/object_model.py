from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import AssetKind
from .cad_model import Block, CadDocument, Constraint, EntityRef, Layer, SelectionSet


@dataclass
class ArtifactRef:
    path: str
    sha256: str = ""
    bytes: int = 0
    media_type: str = ""


@dataclass
class EngineeringObject:
    """Canonical representation shared by CAD, CAE, MBD and vehicle tools."""

    schema_version: str = "engineering-agent/object/v1"
    object_id: str = ""
    name: str = ""
    kind: AssetKind = AssetKind.UNKNOWN
    source: ArtifactRef | None = None
    geometry: dict[str, Any] = field(default_factory=dict)
    tables: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["kind"] = self.kind.value
        return result

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass
class EngineeringProject:
    schema_version: str = "engineering-agent/project/v1"
    project_id: str = ""
    name: str = ""
    root: str = "."
    assets: list[EngineeringObject] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "name": self.name,
            "root": self.root,
            "assets": [asset.to_dict() for asset in self.assets],
            "metadata": self.metadata,
        }
