from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ingest import ArtifactReader


def artifact_listing(root: str | Path, limit: int = 500) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    if not root.exists():
        return []
    items = []
    for path in sorted(item for item in root.rglob("*") if item.is_file())[:limit]:
        items.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size, "suffix": path.suffix.lower()})
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
    return {"asset": asset.to_dict(), "svg": render_asset_svg(asset.to_dict())}


def render_asset_svg(asset: dict[str, Any]) -> str:
    geometry = asset.get("geometry", {})
    if geometry.get("format") == "DXF" and geometry.get("entities"):
        return render_dxf_svg(geometry)
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
            bars.append(f"<text x='8' y='{y+14}' font-size='11'>{name[:28]}</text><rect x='190' y='{y}' width='{bar_width:.1f}' height='16' fill='#37a169'/><text x='{195+bar_width:.1f}' y='{y+13}' font-size='11'>{float(value):.3g}</text>")
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


def render_timeline_svg(events: list[dict[str, Any]]) -> str:
    width = max(640, 180 * len(events))
    body = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} 180'><rect width='100%' height='100%' fill='#f7f9fc'/><line x1='50' y1='90' x2='{width-50}' y2='90' stroke='#8492a6' stroke-width='3'/>"]
    for index, event in enumerate(events):
        x = 70 + index * 160
        body.append(f"<circle cx='{x}' cy='90' r='12' fill='#2855c5'/><text x='{x-55}' y='45' font-size='11'>{str(event.get('event', 'event'))[:18]}</text><text x='{x-55}' y='125' font-size='10'>{str(event.get('label', event.get('version_id', '')))[:18]}</text>")
    body.append("</svg>")
    return "".join(body)
