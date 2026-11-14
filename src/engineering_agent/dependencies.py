"""Conservative project dependency graph extraction.

Native vendor files can be opaque or version specific.  The scanner therefore
reports only explicit manifest links, readable text references, and known
sidecar relationships.  It never claims that an opaque CATIA/Ansys/Adams/
VI-grade file has been semantically parsed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .transactions import RunTransaction, confined_path


SCHEMA_VERSION = "engineering-agent/dependencies/v1"
TEXT_SUFFIXES = {".json", ".xml", ".csv", ".txt", ".step", ".stp", ".dxf", ".svg", ".py", ".js", ".m", ".cmd", ".acf", ".adm", ".sim"}
REFERENCE_SUFFIXES = ("catpart", "catproduct", "catdrawing", "catfct", "step", "stp", "dxf", "obj", "vtk", "vtu", "glb", "csv", "json", "xml", "pdf", "rst", "rth", "res", "adm", "acf", "sim", "mat", "mechdb", "wbpj", "agdb")
COMPANIONS = {".dxf": ".cad.json", ".step": ".brep.json", ".stp": ".brep.json", ".pdf": ".plot.json", ".png": ".plot.json", ".svg": ".plot.json"}
REFERENCE_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z0-9_./\\ -]+\.(?:" + "|".join(REFERENCE_SUFFIXES) + r"))(?![A-Za-z0-9_])", re.IGNORECASE)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".catpart", ".catproduct", ".catdrawing", ".step", ".stp", ".dxf", ".obj", ".vtk", ".vtu", ".glb"}:
        return "geometry"
    if suffix in {".rst", ".rth", ".res", ".adm", ".acf", ".sim", ".mat", ".csv", ".json", ".xml", ".pdf"}:
        return "result_or_project"
    return "file"


class DependencyScanner:
    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).expanduser().resolve()

    def _relative(self, value: str | Path) -> str:
        path = Path(value).expanduser()
        if path.is_absolute():
            if not path.is_relative_to(self.project_dir):
                raise ValueError("DEPENDENCY_PATH_OUTSIDE_PROJECT")
            path = path.relative_to(self.project_dir)
        if not path.parts or path.parts[0] in {".engineering-agent", ".git"} or ".." in path.parts:
            raise ValueError("DEPENDENCY_INTERNAL_OR_TRAVERSAL_PATH")
        confined_path(self.project_dir, path.as_posix())
        return path.as_posix()

    def _files(self, paths: list[str] | None) -> list[str]:
        if paths is None:
            candidates = (item for item in self.project_dir.rglob("*") if item.is_file())
        else:
            candidates = []
            for value in paths:
                relative = self._relative(value)
                target = confined_path(self.project_dir, relative)
                if target.is_dir():
                    candidates.extend(item for item in target.rglob("*") if item.is_file())
                else:
                    candidates.append(target)
        result = set()
        for path in candidates:
            if path.is_symlink() or not path.is_file() or path.is_relative_to(self.project_dir / ".engineering-agent") or path.is_relative_to(self.project_dir / ".git"):
                continue
            result.add(path.relative_to(self.project_dir).as_posix())
        if len(result) > 5000:
            raise ValueError("DEPENDENCY_TOO_MANY_FILES")
        return sorted(result)

    def _node(self, relative: str) -> dict[str, Any]:
        path = confined_path(self.project_dir, relative)
        data = {"path": relative, "exists": path.is_file(), "kind": _kind(relative), "suffix": path.suffix.lower()}
        if not path.is_file():
            data["parse_status"] = "missing"
            return data
        data.update({"bytes": path.stat().st_size, "sha256": _sha256(path)})
        if path.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8")
                data["parse_status"] = "text"
                data["line_count"] = text.count("\n") + (1 if text else 0)
            except (UnicodeDecodeError, OSError):
                data["parse_status"] = "opaque"
        else:
            data["parse_status"] = "opaque"
        return data

    def _resolve_reference(self, source: str, value: str) -> tuple[str | None, bool]:
        cleaned = value.strip().strip("'\"`;,()[]{}").replace("\\", "/")
        if not cleaned or cleaned == source:
            return None, False
        candidate = Path(cleaned).expanduser()
        if candidate.is_absolute():
            try:
                return candidate.resolve().relative_to(self.project_dir).as_posix(), False
            except ValueError:
                return cleaned, True
        local = (self.project_dir / Path(source).parent / candidate).resolve()
        if local.is_relative_to(self.project_dir):
            return local.relative_to(self.project_dir).as_posix(), False
        root_candidate = (self.project_dir / candidate).resolve()
        if root_candidate.is_relative_to(self.project_dir):
            return root_candidate.relative_to(self.project_dir).as_posix(), False
        return cleaned, True

    def _text_references(self, source: str, text: str) -> list[tuple[str, str, bool]]:
        references = []
        for match in REFERENCE_RE.finditer(text):
            target, external = self._resolve_reference(source, match.group(1))
            if target:
                references.append((target, "text_reference", external))
        return references

    def _manifest_references(self, files: set[str]) -> tuple[list[tuple[str, str, str, bool]], list[str]]:
        manifest = self.project_dir / "simloom.dependencies.json"
        if not manifest.is_file():
            return [], []
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return [], ["MANIFEST_PARSE_FAILED:" + str(exc)[:200]]
        rows = payload.get("dependencies", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return [], ["MANIFEST_DEPENDENCIES_MUST_BE_ARRAY"]
        edges, warnings = [], []
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get("source"), str):
                warnings.append(f"MANIFEST_ENTRY_INVALID:{index}")
                continue
            source = self._relative(row["source"])
            targets = row.get("targets", row.get("target", []))
            if isinstance(targets, str):
                targets = [targets]
            if not isinstance(targets, list):
                warnings.append(f"MANIFEST_TARGETS_INVALID:{index}")
                continue
            for target_value in targets:
                if not isinstance(target_value, str):
                    warnings.append(f"MANIFEST_TARGET_INVALID:{index}")
                    continue
                target, external = self._resolve_reference(source, target_value)
                if target:
                    edges.append((source, target, str(row.get("relation", "declared")), external))
        return edges, warnings

    def scan(self, paths: list[str] | None = None) -> dict[str, Any]:
        with RunTransaction.read_lock(self.project_dir):
            selected = self._files(paths)
            nodes = {relative: self._node(relative) for relative in selected}
            edges: list[dict[str, Any]] = []
            warnings: list[str] = []
            for source in selected:
                path = confined_path(self.project_dir, source)
                if path.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    nodes[source]["parse_status"] = "opaque"
                    continue
                for target, relation, external in self._text_references(source, text):
                    if target == source:
                        continue
                    if not external and target not in nodes:
                        nodes[target] = self._node(target)
                    edges.append({"source": source, "target": target, "relation": relation, "external": external,
                                  "exists": False if external else (target in nodes and nodes[target].get("exists", False))})
            manifest_edges, manifest_warnings = self._manifest_references(set(selected))
            warnings.extend(manifest_warnings)
            for source, target, relation, external in manifest_edges:
                if source not in nodes:
                    nodes[source] = self._node(source)
                if not external and target not in nodes:
                    nodes[target] = self._node(target)
                edges.append({"source": source, "target": target, "relation": relation, "external": external,
                              "exists": False if external else nodes[target].get("exists", False)})
            for source in list(nodes):
                companion = COMPANIONS.get(Path(source).suffix.lower())
                if companion:
                    target = source + companion
                    target_path = confined_path(self.project_dir, target)
                    if target_path.is_file():
                        nodes.setdefault(target, self._node(target))
                        edges.append({"source": source, "target": target, "relation": "companion", "external": False, "exists": True})
            # Keep one deterministic edge per relationship.
            unique = {(item["source"], item["target"], item["relation"], item["external"]): item for item in edges}
            missing = sorted({item["target"] for item in unique.values() if not item["external"] and not item["exists"]})
            external = sorted({item["target"] for item in unique.values() if item["external"]})
            return {"schema_version": SCHEMA_VERSION, "project_dir": str(self.project_dir),
                    "paths": selected, "nodes": [nodes[key] for key in sorted(nodes)],
                    "edges": [unique[key] for key in sorted(unique)], "missing": missing,
                    "external": external, "warnings": sorted(set(warnings)),
                    "complete": not missing and not warnings}

