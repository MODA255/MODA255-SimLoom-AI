from __future__ import annotations

import json
import csv
import math
import os
from html import escape
from pathlib import Path
from typing import Any

from .ingest import ArtifactReader
from .brep import get_brep_backend


def artifact_listing(root: str | Path, limit: int = 500) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    if not root.exists():
        return []
    items = []
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = sorted(name for name in folders if not name.startswith(".") and name not in {"runs", "__pycache__", "node_modules"} and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if name.startswith(".") or path.suffix in {".db", ".db-wal", ".db-shm"} or path.is_symlink():
                continue
            items.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "suffix": path.suffix.lower()})
            if len(items) >= limit:
                return items
    return items


def artifact_preview(root: str | Path, relative: str, max_chars: int = 20000) -> dict[str, Any]:
    path = (Path(root).resolve() / relative).resolve()
    root = Path(root).resolve()
    if root not in path.parents and path != root:
        raise ValueError("ARTIFACT_PATH_OUTSIDE_ROOT")
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".json":
        return {"path": relative, "kind": "json", "content": json.loads(path.read_text(encoding="utf-8"))}
    return {"path": relative, "kind": "text", "content": path.read_text(encoding="utf-8", errors="replace")[:max_chars]}


def asset_view(root: str | Path, relative: str) -> dict[str, Any]:
    root_path = Path(root).resolve()
    path = (root_path / relative).resolve()
    if root_path not in path.parents:
        raise ValueError("ARTIFACT_PATH_OUTSIDE_ROOT")
    asset = ArtifactReader().read(path)
    payload = asset.to_dict()
    note = "工程数据预览"
    if path.suffix.lower() in {".step", ".stp"}:
        backend = get_brep_backend()
        if backend.name == "opencascade":
            try:
                payload["geometry"].update(backend.tessellate_step(path))
                note = "STEP 曲面网格 · 拖动旋转、滚轮缩放、双击重置；此网格仅用于显示。"
            except (RuntimeError, ValueError) as exc:
                note = f"STEP 曲面预览失败，显示包围盒：{exc}"
        else:
            note = "未安装 OpenCascade，仅显示 STEP 包围盒，不代表完整曲面。"
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        geometry = raw.get("geometry") if isinstance(raw, dict) else None
        if isinstance(geometry, dict) and geometry.get("vertices") and geometry.get("faces"):
            payload["geometry"] = geometry
            note = "JSON 网格 / 顶点结果场 · 拖动旋转、滚轮缩放。"
    svg = render_csv_svg(path) if path.suffix.lower() == ".csv" else render_asset_svg(payload)
    return {"asset": payload, "svg": svg, "viewer": render_interactive_viewer(payload), "preview_note": note}


def render_csv_svg(path: Path) -> str:
    """Plot numeric series against the first column, with independent labeled scales."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        rows = []
        for row in reader:
            if len(rows) >= 100_000:
                raise ValueError("CSV 超过 100,000 行预览上限，请先抽样")
            rows.append(row)
    if len(columns) < 2 or not rows:
        return "<svg xmlns='http://www.w3.org/2000/svg'><text x='20' y='40'>CSV 需要至少两列数值数据</text></svg>"
    plots = []
    for column in columns[1:5]:
        points = []
        for row in rows:
            try:
                x, y = float(row[columns[0]]), float(row[column])
                if math.isfinite(x) and math.isfinite(y):
                    points.append((x, y))
            except (TypeError, ValueError):
                continue
        if not points:
            continue
        xmin, xmax = min(x for x, _ in points), max(x for x, _ in points)
        ymin, ymax = min(y for _, y in points), max(y for _, y in points)
        offset = len(plots) * 155
        stride = max(1, math.ceil(len(points)/1800))
        sampled = points[::stride]
        if sampled[-1] != points[-1]:
            sampled.append(points[-1])
        line = " ".join(f"{80+(x-xmin)/(xmax-xmin or 1)*570:.2f},{offset+112-(y-ymin)/(ymax-ymin or 1)*75:.2f}" for x, y in sampled)
        plots.append(f"<text x='80' y='{offset+22}' font-size='13'>{escape(column)}</text><text x='8' y='{offset+42}' font-size='10'>{ymax:.4g}</text><text x='8' y='{offset+112}' font-size='10'>{ymin:.4g}</text><path d='M80 {offset+35}V{offset+116}H650' fill='none' stroke='#c7d2e5'/><polyline points='{line}' fill='none' stroke='#3d62ff' stroke-width='2'/><text x='80' y='{offset+136}' font-size='10'>{xmin:.4g}</text><text x='610' y='{offset+136}' font-size='10'>{xmax:.4g}</text>")
    height = max(160, 155*len(plots)+30)
    return f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 710 {height}'><rect width='100%' height='100%' fill='#f7f9fc'/>" + "".join(plots) + f"<text x='80' y='{height-10}' font-size='11'>横轴：{escape(columns[0])} · {len(rows)} 行 · 各曲线独立纵轴</text></svg>"


def mesh_payload(asset: dict[str, Any]) -> dict[str, Any]:
    """Prepare a centered, finite mesh with explicit scalar normalization."""
    geometry = asset.get("geometry", {})
    vertices, faces = geometry.get("vertices") or [], geometry.get("faces") or []
    if not vertices or not faces:
        return {}
    if len(vertices) > 600_000:
        raise ValueError("模型超过预览顶点上限")
    points = [[float(item.get(axis, 0)) for axis in ("x", "y", "z")] for item in vertices]
    if not all(math.isfinite(value) for point in points for value in point):
        raise ValueError("模型包含非有限坐标")
    lows = [min(p[i] for p in points) for i in range(3)]
    highs = [max(p[i] for p in points) for i in range(3)]
    center = [(lo + hi) / 2 for lo, hi in zip(lows, highs)]
    radius = math.sqrt(sum(((hi-lo)/2)**2 for lo, hi in zip(lows, highs))) or 1.0
    normalized = [[(point[i]-center[i]) * .8/radius for i in range(3)] for point in points]
    indices = []
    for face in faces:
        if any(not isinstance(i, int) or i < 0 or i >= len(points) for i in face):
            raise ValueError("模型面索引越界")
        for j in range(1, len(face)-1):
            indices.extend((face[0], face[j], face[j+1]))
            if len(indices) > 600_000:
                raise ValueError("模型超过预览三角形上限")
    values = geometry.get("scalars") or geometry.get("point_values") or []
    scalar_range = None
    colors = [[.36, .62, .96] for _ in points]
    if values:
        scalars = [float(v) for v in values]
        if len(scalars) != len(points) or not all(math.isfinite(v) for v in scalars):
            raise ValueError("结果场必须为每个顶点提供一个有限数值")
        low, high = min(scalars), max(scalars)
        scalar_range = [low, high]
        colors = []
        for value in scalars:
            t = (value-low)/(high-low) if high > low else .5
            colors.append([min(1, 2*t), min(1, 2*(1-abs(t-.5))), min(1, 2*(1-t))])
    return {"points": normalized, "indices": indices, "colors": colors, "scalar_range": scalar_range}


def render_interactive_viewer(asset: dict[str, Any]) -> str:
    data = mesh_payload(asset)
    if not data or not data["indices"]:
        return "<div data-engineering-viewer='unavailable'>没有可用于交互式预览的网格曲面。</div>"
    template = Path(__file__).with_name("static").joinpath("mesh_viewer.html").read_text(encoding="utf-8")
    return template.replace("__MESH_DATA__", json.dumps(data, allow_nan=False))


def render_asset_svg(asset: dict[str, Any]) -> str:
    geometry = asset.get("geometry", {})
    if geometry.get("format") == "DXF" and geometry.get("entities"):
        return render_dxf_svg(geometry)
    if geometry.get("format") == "OBJ" and geometry.get("vertices"):
        return render_mesh_svg(geometry)
    bbox = geometry.get("bbox") or {}
    if bbox:
        x0, x1 = float(bbox.get("x", {}).get("min", 0)), float(bbox.get("x", {}).get("max", 1))
        y0, y1 = float(bbox.get("y", {}).get("min", 0)), float(bbox.get("y", {}).get("max", 1))
        width, height = max(abs(x1 - x0), 1.0), max(abs(y1 - y0), 1.0)
        scale = min(520 / width, 320 / height)
        rect_w, rect_h = width * scale, height * scale
        return f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 560 360' role='img'><rect width='560' height='360' fill='#f7f9fc'/><rect x='{(560-rect_w)/2:.1f}' y='{(360-rect_h)/2:.1f}' width='{rect_w:.1f}' height='{rect_h:.1f}' fill='#b8d7ff' stroke='#2855c5' stroke-width='3'/><text x='20' y='30' font-family='system-ui'>STEP bounding box</text><text x='20' y='350' font-family='system-ui'>{width:.2f} × {height:.2f} (x/y units)</text></svg>"
    metrics = asset.get("metrics", {})
    if metrics:
        pairs = list(metrics.items())[:12]
        max_value = max(abs(float(value)) for _, value in pairs) or 1
        bars = []
        for index, (name, value) in enumerate(pairs):
            bar_width = 360 * abs(float(value)) / max_value
            y = 28 + index * 24
            bars.append(f"<text x='8' y='{y+14}' font-size='11'>{escape(name[:28])}</text><rect x='190' y='{y}' width='{bar_width:.1f}' height='16' fill='#37a169'/><text x='{195+bar_width:.1f}' y='{y+13}' font-size='11'>{float(value):.3g}</text>")
        return "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 600 330' role='img'><rect width='600' height='330' fill='#f7f9fc'/>" + "".join(bars) + "</svg>"
    return "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 560 120'><text x='20' y='60' font-family='system-ui'>No visualizable geometry or metrics</text></svg>"


def render_dxf_svg(geometry: dict[str, Any], width: int = 720, height: int = 460) -> str:
    """Render semantic DXF entities as a lightweight, dependency-free 2D view."""
    bbox = geometry.get("bbox") or {}
    x_axis, y_axis = bbox.get("x", {}), bbox.get("y", {})
    x_min, x_max = float(x_axis.get("min", 0)), float(x_axis.get("max", 1))
    y_min, y_max = float(y_axis.get("min", 0)), float(y_axis.get("max", 1))
    span_x, span_y = max(x_max - x_min, 1.0), max(y_max - y_min, 1.0)
    scale = min((width - 80) / span_x, (height - 80) / span_y)

    def point(value: dict[str, Any]) -> tuple[float, float]:
        return 40 + (float(value.get("x", 0)) - x_min) * scale, height - 40 - (float(value.get("y", 0)) - y_min) * scale

    body = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' role='img'><rect width='100%' height='100%' fill='#f7f9fc'/><text x='18' y='24' font-family='system-ui' font-size='14'>DXF semantic view</text>"]
    colors = {"0": "#2855c5", "FRAME": "#2855c5", "WHEEL": "#37a169", "ANNOTATION": "#b35c00"}
    for entity in geometry.get("entities", []):
        kind, color = str(entity.get("type", "")), colors.get(str(entity.get("layer", "0")), "#6b7280")
        if kind == "LINE":
            start, end = point(entity.get("start", {})), point(entity.get("end", {}))
            body.append(f"<line x1='{start[0]:.2f}' y1='{start[1]:.2f}' x2='{end[0]:.2f}' y2='{end[1]:.2f}' stroke='{color}' stroke-width='2'/>")
        elif kind in {"CIRCLE", "ARC"}:
            center = point(entity.get("center", {}))
            radius = abs(float(entity.get("radius", 0))) * scale
            body.append(f"<circle cx='{center[0]:.2f}' cy='{center[1]:.2f}' r='{radius:.2f}' fill='none' stroke='{color}' stroke-width='2'/>")
        elif kind == "POINT":
            x, y = point(entity.get("point", {}))
            body.append(f"<circle cx='{x:.2f}' cy='{y:.2f}' r='3' fill='{color}'/>")
        elif kind in {"LWPOLYLINE", "POLYLINE"}:
            vertices = [point(item) for item in entity.get("vertices", [])]
            if len(vertices) >= 2:
                points = " ".join(f"{x:.2f},{y:.2f}" for x, y in vertices)
                tag = "polygon" if entity.get("closed") else "polyline"
                body.append(f"<{tag} points='{points}' fill='none' stroke='{color}' stroke-width='2'/>")
        elif kind == "TEXT":
            x, y = point(entity.get("insert", {}))
            text = str(entity.get("text", ""))[:40].replace("&", "&amp;").replace("<", "&lt;")
            body.append(f"<text x='{x:.2f}' y='{y:.2f}' font-size='12' fill='{color}'>{text}</text>")
    body.append("</svg>")
    return "".join(body)


def render_mesh_svg(geometry: dict[str, Any], width: int = 720, height: int = 460) -> str:
    """Render an OBJ mesh as an oblique wireframe preview without a 3D dependency."""
    vertices = geometry.get("vertices") or []
    bbox = geometry.get("bbox") or {}
    x_axis, y_axis, z_axis = bbox.get("x", {}), bbox.get("y", {}), bbox.get("z", {})
    x_min, x_max = float(x_axis.get("min", 0)), float(x_axis.get("max", 1))
    y_min, y_max = float(y_axis.get("min", 0)), float(y_axis.get("max", 1))
    z_min, z_max = float(z_axis.get("min", 0)), float(z_axis.get("max", 1))
    span = max(x_max - x_min, y_max - y_min, z_max - z_min, 1.0)
    scale = min((width - 80) / span, (height - 80) / span)

    def project(index: int) -> tuple[float, float]:
        point = vertices[index]
        x = float(point.get("x", 0)) - x_min
        y = float(point.get("y", 0)) - y_min
        z = float(point.get("z", 0)) - z_min
        return 40 + (x - 0.45 * y) * scale, height - 40 - (0.75 * y + 0.5 * z) * scale

    body = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' role='img'><rect width='100%' height='100%' fill='#f7f9fc'/><text x='18' y='24' font-family='system-ui' font-size='14'>OBJ mesh wireframe</text>"]
    for face in geometry.get("faces", []):
        if len(face) < 2:
            continue
        points = [project(index) for index in face if 0 <= index < len(vertices)]
        if len(points) < 2:
            continue
        points.append(points[0])
        body.append("<polyline points='" + " ".join(f"{x:.2f},{y:.2f}" for x, y in points) + "' fill='none' stroke='#2855c5' stroke-width='1.3'/>")
    body.append(f"<text x='18' y='{height-18}' font-family='system-ui' font-size='12'>{len(vertices)} vertices · {len(geometry.get('faces', []))} faces · area {float(geometry.get('surface_area', 0)):.3g}</text></svg>")
    return "".join(body)


def render_timeline_svg(events: list[dict[str, Any]]) -> str:
    width = max(640, 180 * len(events))
    body = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} 180'><rect width='100%' height='100%' fill='#f7f9fc'/><line x1='50' y1='90' x2='{width-50}' y2='90' stroke='#8492a6' stroke-width='3'/>"]
    for index, event in enumerate(events):
        x = 70 + index * 160
        body.append(f"<circle cx='{x}' cy='90' r='12' fill='#2855c5'/><text x='{x-55}' y='45' font-size='11'>{str(event.get('event', 'event'))[:18]}</text><text x='{x-55}' y='125' font-size='10'>{str(event.get('label', event.get('version_id', '')))[:18]}</text>")
    body.append("</svg>")
    return "".join(body)
