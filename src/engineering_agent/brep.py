from __future__ import annotations

import math
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class BRepUnavailable(RuntimeError):
    """Raised when no OpenCascade-compatible kernel is installed."""


def _number(value: Any) -> float:
    return float(value)


@dataclass
class ConstraintResult:
    converged: bool
    iterations: int
    parameters: dict[str, float]
    residuals: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"converged": self.converged, "iterations": self.iterations, "parameters": self.parameters, "residuals": self.residuals}


def solve_constraints(parameters: dict[str, Any], constraints: list[dict[str, Any]], *, tolerance: float = 1e-8, max_iterations: int = 100) -> ConstraintResult:
    """Solve simple deterministic parametric constraints without a CAD kernel.

    Supported constraints are ``equal``, ``distance``, ``min``, ``max`` and
    ``range``. This solves the parameter layer; a kernel-backed feature graph
    can consume the resulting values to rebuild actual B-Rep features.
    """
    values = {str(key): _number(value) for key, value in parameters.items()}
    residuals: list[dict[str, Any]] = []

    def ref(value: Any) -> float:
        if isinstance(value, str) and value in values:
            return values[value]
        return _number(value)

    def set_value(key: str, value: float):
        values[key] = float(value)

    for iteration in range(1, max_iterations + 1):
        changed = False
        for constraint in constraints:
            kind = str(constraint.get("type", constraint.get("kind", "equal"))).lower()
            left = str(constraint.get("left", constraint.get("parameter", "")))
            if not left:
                continue
            if kind == "equal":
                right = ref(constraint.get("right", constraint.get("value", 0.0)))
                if left not in values or abs(values.get(left, right) - right) > tolerance:
                    set_value(left, right)
                    changed = True
            elif kind == "distance":
                right = ref(constraint.get("right", 0.0))
                target = _number(constraint.get("value", 0.0))
                desired = right + target
                if left not in values or abs(values.get(left, desired) - desired) > tolerance:
                    set_value(left, desired)
                    changed = True
            elif kind == "min":
                bound = ref(constraint.get("value", constraint.get("right", 0.0)))
                if left not in values or values.get(left, bound) < bound:
                    set_value(left, bound)
                    changed = True
            elif kind == "max":
                bound = ref(constraint.get("value", constraint.get("right", 0.0)))
                if left not in values or values.get(left, bound) > bound:
                    set_value(left, bound)
                    changed = True
            elif kind == "range":
                lower = _number(constraint.get("min", constraint.get("lower", -math.inf)))
                upper = _number(constraint.get("max", constraint.get("upper", math.inf)))
                clamped = min(max(values.get(left, lower), lower), upper)
                if left not in values or abs(values.get(left, clamped) - clamped) > tolerance:
                    set_value(left, clamped)
                    changed = True
        residuals = evaluate_constraints(values, constraints)
        if not any(abs(float(item.get("residual", 0.0))) > tolerance for item in residuals):
            return ConstraintResult(True, iteration, values, residuals)
        if not changed:
            return ConstraintResult(False, iteration, values, residuals)
    return ConstraintResult(False, max_iterations, values, residuals)


def evaluate_constraints(parameters: dict[str, float], constraints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values = {str(key): float(value) for key, value in parameters.items()}

    def ref(value: Any) -> float:
        return values[str(value)] if isinstance(value, str) and str(value) in values else float(value)

    output = []
    for index, constraint in enumerate(constraints):
        kind = str(constraint.get("type", constraint.get("kind", "equal"))).lower()
        left = str(constraint.get("left", constraint.get("parameter", "")))
        current = values.get(left, 0.0)
        if kind == "equal":
            residual = current - ref(constraint.get("right", constraint.get("value", 0.0)))
        elif kind == "distance":
            residual = current - ref(constraint.get("right", 0.0)) - float(constraint.get("value", 0.0))
        elif kind == "min":
            residual = min(0.0, current - ref(constraint.get("value", constraint.get("right", 0.0))))
        elif kind == "max":
            residual = max(0.0, current - ref(constraint.get("value", constraint.get("right", 0.0))))
        elif kind == "range":
            lower = float(constraint.get("min", constraint.get("lower", -math.inf)))
            upper = float(constraint.get("max", constraint.get("upper", math.inf)))
            residual = min(0.0, current - lower) + max(0.0, current - upper)
        else:
            residual = 0.0
        output.append({"index": index, "type": kind, "parameter": left, "residual": float(residual), "satisfied": abs(float(residual)) <= 1e-8})
    return output


class BRepBackend:
    name = "abstract"

    def inspect_step(self, path: str | Path) -> dict[str, Any]:
        raise NotImplementedError

    def boolean(self, operation: str, left: str | Path, right: str | Path, output: str | Path) -> dict[str, Any]:
        raise NotImplementedError

    def create_primitive(self, primitive: str, parameters: dict[str, Any], output: str | Path) -> dict[str, Any]:
        raise NotImplementedError

    def tessellate_step(self, path: str | Path, deflection: float = 0.5) -> dict[str, Any]:
        raise BRepUnavailable("OPENCASCADE_BACKEND_UNAVAILABLE")


class UnavailableBRepBackend(BRepBackend):
    name = "unavailable"

    def _raise(self):
        raise BRepUnavailable("OPENCASCADE_BACKEND_UNAVAILABLE")

    def inspect_step(self, path: str | Path) -> dict[str, Any]:
        self._raise()

    def boolean(self, operation: str, left: str | Path, right: str | Path, output: str | Path) -> dict[str, Any]:
        self._raise()

    def create_primitive(self, primitive: str, parameters: dict[str, Any], output: str | Path) -> dict[str, Any]:
        self._raise()


class OpenCascadeBackend(BRepBackend):
    """OpenCascade adapter supporting pythonocc-core or cadquery-ocp's OCP package."""

    name = "opencascade"

    def __init__(self):
        self._load_bindings()

    def _load_bindings(self):
        try:
            from OCP.BRepCheck import BRepCheck_Analyzer  # type: ignore
            from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse  # type: ignore
            from OCP import BRepBndLib as _BRepBndLibModule  # type: ignore
            from OCP import BRepGProp as _BRepGPropModule  # type: ignore
            from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder  # type: ignore
            from OCP.Bnd import Bnd_Box  # type: ignore
            from OCP.GProp import GProp_GProps  # type: ignore
            from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer  # type: ignore
            from OCP.TopAbs import TopAbs_COMPSOLID, TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX  # type: ignore
            from OCP.TopExp import TopExp_Explorer  # type: ignore
            # cadquery-ocp 8.x exposes the global helpers as static methods on
            # BRepGProp/BRepBndLib, while older OCP/pythonocc releases expose
            # module-level functions. Keep one normalized adapter surface.
            self._api = locals()
            gprop_class = getattr(_BRepGPropModule, "BRepGProp", None)
            bnd_class = getattr(_BRepBndLibModule, "BRepBndLib", None)
            self._api["brepgprop_SurfaceProperties"] = getattr(_BRepGPropModule, "brepgprop_SurfaceProperties", None) or getattr(gprop_class, "SurfaceProperties_s", None)
            self._api["brepgprop_VolumeProperties"] = getattr(_BRepGPropModule, "brepgprop_VolumeProperties", None) or getattr(gprop_class, "VolumeProperties_s", None)
            self._api["brepbndlib_Add"] = getattr(_BRepBndLibModule, "brepbndlib_Add", None) or getattr(bnd_class, "Add_s", None) or getattr(bnd_class, "Add", None)
            self._api["BRepBndLib"] = bnd_class
            if self._api["brepgprop_SurfaceProperties"] is None or self._api["brepgprop_VolumeProperties"] is None or self._api["brepbndlib_Add"] is None:
                raise ImportError("unsupported OCP BRep helper API")
            return
        except ImportError:
            pass
        try:
            from OCC.Core.BRepCheck import BRepCheck_Analyzer  # type: ignore
            from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse  # type: ignore
            from OCC.Core.BRepBndLib import brepbndlib_Add  # type: ignore
            from OCC.Core.BRepGProp import brepgprop_SurfaceProperties, brepgprop_VolumeProperties  # type: ignore
            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder  # type: ignore
            from OCC.Core.Bnd import Bnd_Box  # type: ignore
            from OCC.Core.GProp import GProp_GProps  # type: ignore
            from OCC.Core.STEPControl import STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer  # type: ignore
            from OCC.Core.TopAbs import TopAbs_COMPSOLID, TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX  # type: ignore
            from OCC.Core.TopExp import TopExp_Explorer  # type: ignore
            self._api = locals()
            return
        except ImportError as exc:
            raise BRepUnavailable("OPENCASCADE_BACKEND_UNAVAILABLE") from exc

    def _read(self, path: str | Path):
        reader = self._api["STEPControl_Reader"]()
        status = reader.ReadFile(str(path))
        if int(status) != 1:
            raise RuntimeError(f"STEP_READ_FAILED:{status}")
        reader.TransferRoots()
        shape = reader.OneShape()
        if shape.IsNull():
            raise RuntimeError("STEP_EMPTY_SHAPE")
        return shape

    def _write(self, shape: Any, path: str | Path):
        writer = self._api["STEPControl_Writer"]()
        writer.Transfer(shape, self._api["STEPControl_AsIs"])
        status = writer.Write(str(path))
        if int(status) != 1:
            raise RuntimeError(f"STEP_WRITE_FAILED:{status}")

    def inspect_step(self, path: str | Path) -> dict[str, Any]:
        shape = self._read(path)
        props = self._api["GProp_GProps"]()
        self._api["brepgprop_VolumeProperties"](shape, props)
        volume = float(props.Mass())
        surface = self._api["GProp_GProps"]()
        self._api["brepgprop_SurfaceProperties"](shape, surface)
        topology = {}
        for name in ("COMPSOLID", "SOLID", "SHELL", "FACE", "EDGE", "VERTEX"):
            explorer = self._api["TopExp_Explorer"](shape, self._api[f"TopAbs_{name}"])
            count = 0
            while explorer.More():
                count += 1
                explorer.Next()
            topology[name.lower()] = count
        bounds = self._bbox(shape)
        valid = bool(self._api["BRepCheck_Analyzer"](shape).IsValid())
        return {"backend": self.name, "path": str(path), "is_null": bool(shape.IsNull()), "valid": valid, "volume": volume, "surface_area": float(surface.Mass()), "shape_type": str(shape.ShapeType()), "topology": topology, "bbox": bounds}

    def _bbox(self, shape: Any) -> dict[str, dict[str, float]]:
        box = self._api["Bnd_Box"]()
        if "BRepBndLib" in self._api and hasattr(self._api["BRepBndLib"], "Add"):
            self._api["BRepBndLib"].Add(shape, box)
        else:
            self._api["brepbndlib_Add"](shape, box)
        # OCP 8.x binds ``Bnd_Box::Get`` to an opaque Limits return type;
        # scalar accessors are available across both OCP and pythonocc.
        try:
            minimum = (box.GetXMin(), box.GetYMin(), box.GetZMin())
            maximum = (box.GetXMax(), box.GetYMax(), box.GetZMax())
        except (AttributeError, TypeError):
            values = box.Get()
            minimum = values[:3]
            maximum = values[3:]
        return {axis: {"min": float(minimum[index]), "max": float(maximum[index])} for index, axis in enumerate(("x", "y", "z"))}

    def tessellate_step(self, path: str | Path, deflection: float = 0.5) -> dict[str, Any]:
        """Triangulate actual B-Rep faces, applying face locations and orientation."""
        if not math.isfinite(deflection) or deflection <= 0:
            raise ValueError("INVALID_MESH_DEFLECTION")
        if self._api["STEPControl_Reader"].__module__.startswith("OCP"):
            from OCP.BRep import BRep_Tool
            from OCP.BRepMesh import BRepMesh_IncrementalMesh
            from OCP.TopLoc import TopLoc_Location
            from OCP.TopoDS import TopoDS
            from OCP.TopAbs import TopAbs_REVERSED
            cast_face = getattr(TopoDS, "Face_s", None) or TopoDS.Face
            triangulation = getattr(BRep_Tool, "Triangulation_s", None) or BRep_Tool.Triangulation
        else:
            from OCC.Core.BRep import BRep_Tool
            from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
            from OCC.Core.TopLoc import TopLoc_Location
            from OCC.Core.TopoDS import topods
            from OCC.Core.TopAbs import TopAbs_REVERSED
            cast_face, triangulation = topods.Face, BRep_Tool.Triangulation
        shape = self._read(path)
        mesh = BRepMesh_IncrementalMesh(shape, deflection, False, 0.35, False)
        if not mesh.IsDone():
            raise RuntimeError("STEP_TESSELLATION_FAILED")
        vertices, faces = [], []
        explorer = self._api["TopExp_Explorer"](shape, self._api["TopAbs_FACE"])
        while explorer.More():
            face = cast_face(explorer.Current())
            location = TopLoc_Location()
            triangles = triangulation(face, location)
            if triangles:
                if len(faces) + triangles.NbTriangles() > 200_000 or len(vertices) + triangles.NbNodes() > 600_000:
                    raise ValueError("模型超过预览网格上限，请先简化模型")
                offset = len(vertices)
                for i in range(1, triangles.NbNodes() + 1):
                    point = triangles.Node(i).Transformed(location.Transformation())
                    vertices.append({"x": point.X(), "y": point.Y(), "z": point.Z()})
                for i in range(1, triangles.NbTriangles() + 1):
                    indices = [offset + n - 1 for n in triangles.Triangle(i).Get()]
                    if face.Orientation() == TopAbs_REVERSED:
                        indices.reverse()
                    faces.append(indices)
            explorer.Next()
        if not faces:
            raise ValueError("STEP 文件没有可预览的曲面")
        return {"format": "STEP", "backend": self.name, "vertices": vertices, "faces": faces,
                "vertex_count": len(vertices), "triangle_count": len(faces), "bbox": self._bbox(shape),
                "mesh_purpose": "display", "deflection": deflection}

    def boolean(self, operation: str, left: str | Path, right: str | Path, output: str | Path) -> dict[str, Any]:
        left_shape, right_shape = self._read(left), self._read(right)
        operation = operation.lower()
        if operation == "fuse":
            shape = self._api["BRepAlgoAPI_Fuse"](left_shape, right_shape).Shape()
        elif operation == "cut":
            shape = self._api["BRepAlgoAPI_Cut"](left_shape, right_shape).Shape()
        elif operation in {"common", "intersect", "intersection"}:
            shape = self._api["BRepAlgoAPI_Common"](left_shape, right_shape).Shape()
        else:
            raise ValueError(f"UNSUPPORTED_BOOLEAN_OPERATION:{operation}")
        self._write(shape, output)
        return {"backend": self.name, "operation": operation, "output": str(output), "inspection": self.inspect_step(output)}

    def create_primitive(self, primitive: str, parameters: dict[str, Any], output: str | Path) -> dict[str, Any]:
        primitive = primitive.lower()
        if primitive == "box":
            shape = self._api["BRepPrimAPI_MakeBox"](float(parameters["length"]), float(parameters["width"]), float(parameters["height"])).Shape()
        elif primitive == "cylinder":
            shape = self._api["BRepPrimAPI_MakeCylinder"](float(parameters["radius"]), float(parameters["height"])).Shape()
        else:
            raise ValueError(f"UNSUPPORTED_PRIMITIVE:{primitive}")
        self._write(shape, output)
        return {"backend": self.name, "primitive": primitive, "output": str(output), "inspection": self.inspect_step(output)}


def get_brep_backend() -> BRepBackend:
    try:
        return OpenCascadeBackend()
    except BRepUnavailable:
        return UnavailableBRepBackend()


def brep_doctor() -> dict[str, Any]:
    backend = get_brep_backend()
    return {"backend": backend.name, "available": backend.name == "opencascade", "platform": platform.system(), "pythonocc_or_ocp": backend.name == "opencascade", "message": "OpenCascade B-Rep operations available" if backend.name == "opencascade" else "Install cadquery-ocp or pythonocc-core to enable true STEP B-Rep operations"}
