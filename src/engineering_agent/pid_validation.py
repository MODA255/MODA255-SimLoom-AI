"""Independent connectivity checks for the lightweight P&ID object model."""
from __future__ import annotations

import math
from typing import Any


def _point(value: Any) -> tuple[float, float]:
    if isinstance(value, (list, tuple)):
        return float(value[0] if len(value) > 0 else 0), float(value[1] if len(value) > 1 else 0)
    value = value or {}
    return float(value.get("x", 0)), float(value.get("y", 0))


def audit_pid(document: dict[str, Any], *, tolerance: float = 1e-6,
              requirements: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a deterministic P&ID graph from tagged symbols and process lines."""
    requirements = requirements or {}
    tolerance = max(float(tolerance), 1e-9)
    entities = list(document.get("entities") or [])
    symbols = []
    lines = []
    for entity in entities:
        if entity.get("pid_symbol") and str(entity.get("type", "")).upper() in {"CIRCLE", "INSERT", "POINT", "BLOCK_REFERENCE"}:
            center = _point(entity.get("center") or entity.get("insert"))
            symbols.append({"id": str(entity.get("id")), "symbol": str(entity.get("pid_symbol")),
                            "label": str(entity.get("label") or entity.get("text") or entity.get("pid_symbol")),
                            "center": {"x": center[0], "y": center[1]},
                            "ports": copy_points_meta(entity.get("pid_ports") or (entity.get("pid_valve") or {}).get("ports") or [])})
        kind = str(entity.get("type", "")).upper()
        if kind == "LINE" and entity.get("process_line"):
            start, end = _point_dict(entity.get("start")), _point_dict(entity.get("end"))
            lines.append({"id": str(entity.get("id")), "type": kind,
                          "service": entity.get("process_line", {}).get("service", ""),
                          "line_type": entity.get("process_line", {}).get("line_type", "CONTINUOUS"),
                          "route": copy_points([start, end]), "start": start, "end": end,
                          "process_route": dict(entity.get("process_route") or {}),
                          "connector": dict(entity.get("pid_connector") or {})})
        elif kind in {"LWPOLYLINE", "POLYLINE"} and entity.get("process_line"):
            vertices = [_point_dict(value) for value in entity.get("vertices", [])]
            if len(vertices) >= 2:
                lines.append({"id": str(entity.get("id")), "type": kind,
                              "service": entity.get("process_line", {}).get("service", ""),
                              "line_type": entity.get("process_line", {}).get("line_type", "CONTINUOUS"),
                              "route": copy_points(vertices), "start": vertices[0], "end": vertices[-1],
                              "vertices": copy_points(vertices),
                              "process_route": dict(entity.get("process_route") or {}),
                              "connector": dict(entity.get("pid_connector") or {})})

    def distance(left, right):
        return math.hypot(left[0] - right[0], left[1] - right[1])

    # Cluster line endpoints and symbols into graph junctions.
    points: list[tuple[float, float, str, str]] = []
    for item in symbols:
        points.append((*_point(item["center"]), "symbol", item["id"]))
    for item in lines:
        route = item.get("route") or [item["start"], item["end"]]
        for index, point in enumerate(route):
            points.append((point["x"], point["y"], "endpoint", f"{item['id']}:vertex:{index}"))
    clusters: list[dict[str, Any]] = []
    for x, y, kind, ref in points:
        cluster = next((item for item in clusters if distance((x, y), (item["x"], item["y"])) <= tolerance), None)
        if cluster is None:
            cluster = {"x": x, "y": y, "refs": [], "symbols": [], "endpoints": []}
            clusters.append(cluster)
        cluster["refs"].append(ref)
        (cluster["symbols"] if kind == "symbol" else cluster["endpoints"]).append(ref)

    def node_for(point):
        candidate = min(clusters, key=lambda item: distance(point, (item["x"], item["y"])), default=None)
        if candidate is None or distance(point, (candidate["x"], candidate["y"])) > tolerance:
            return {"id": f"junction:{point[0]:.9g},{point[1]:.9g}", "kind": "junction", "x": point[0], "y": point[1]}
        if candidate["symbols"]:
            symbol_id = candidate["symbols"][0]
            return {"id": symbol_id, "kind": "symbol", "x": candidate["x"], "y": candidate["y"]}
        return {"id": f"junction:{candidate['x']:.9g},{candidate['y']:.9g}", "kind": "junction", "x": candidate["x"], "y": candidate["y"]}

    graph_nodes: dict[str, dict[str, Any]] = {}
    graph_edges = []
    for symbol in symbols:
        node = node_for(_point(symbol["center"]))
        graph_nodes[node["id"]] = {**node, "symbol": symbol["symbol"], "label": symbol["label"]}
    for line in lines:
        route = line.get("route") or [line["start"], line["end"]]
        route_nodes = [node_for(_point(point)) for point in route]
        for node in route_nodes:
            graph_nodes.setdefault(node["id"], node)
        for index, (start, end) in enumerate(zip(route_nodes, route_nodes[1:])):
            edge_id = line["id"] if len(route) == 2 else f"{line['id']}:segment:{index + 1}"
            graph_edges.append({"id": edge_id, "process_line_id": line["id"],
                                "from": start["id"], "to": end["id"],
                                "service": line["service"], "line_type": line["line_type"],
                                "segment_index": index, "vertices": [route[index], route[index + 1]],
                                **({"connector": line["connector"]} if line.get("connector") else {})})

    degree = {node_id: 0 for node_id in graph_nodes}
    for edge in graph_edges:
        degree[edge["from"]] = degree.get(edge["from"], 0) + 1
        degree[edge["to"]] = degree.get(edge["to"], 0) + 1
    orphan_symbols = [node_id for node_id, node in graph_nodes.items() if node.get("kind") == "symbol" and degree.get(node_id, 0) == 0]
    dangling_junctions = [node_id for node_id, node in graph_nodes.items() if node.get("kind") == "junction" and degree.get(node_id, 0) == 1]
    errors = []
    warnings = [{"code": "PID_DANGLING_JUNCTION", "node_id": node_id} for node_id in dangling_junctions]
    for node_id in orphan_symbols:
        errors.append({"code": "PID_ORPHAN_SYMBOL", "node_id": node_id})
    symbol_by_id = {item["id"]: item for item in symbols}
    ports = []
    for symbol in symbols:
        for port in symbol.get("ports", []):
            ports.append({"symbol_id": symbol["id"], **port})
    for line in lines:
        connector = line.get("connector") or {}
        if not connector:
            continue
        valve_id = str(connector.get("valve_id", ""))
        if valve_id not in symbol_by_id:
            errors.append({"code": "PID_CONNECTOR_VALVE_MISSING", "line_id": line["id"], "valve_id": valve_id})
            continue
        port_name = str(connector.get("port", ""))
        if port_name and port_name not in {str(port.get("name")) for port in symbol_by_id[valve_id].get("ports", [])}:
            errors.append({"code": "PID_CONNECTOR_PORT_MISSING", "line_id": line["id"], "valve_id": valve_id, "port": port_name})
    required_symbols = [str(item) for item in requirements.get("required_symbols", [])]
    actual_symbols = [str(item.get("symbol")) for item in symbols]
    for symbol in required_symbols:
        if symbol not in actual_symbols:
            errors.append({"code": "PID_REQUIRED_SYMBOL_MISSING", "symbol": symbol})
    required_connections = requirements.get("required_connections") or []
    for connection in required_connections:
        left, right = str(connection.get("from")), str(connection.get("to"))
        if not any({edge["from"], edge["to"]} == {left, right} for edge in graph_edges):
            errors.append({"code": "PID_REQUIRED_CONNECTION_MISSING", "from": left, "to": right})
    return {"schema_version": "simloom-ai/pid-audit/v1", "valid": not errors,
            "errors": errors, "warnings": warnings, "nodes": list(graph_nodes.values()),
            "edges": graph_edges, "symbols": symbols, "ports": ports, "process_lines": lines,
            "orphan_symbols": orphan_symbols, "dangling_junctions": dangling_junctions}


def copy_points(points: list[dict[str, float]]) -> list[dict[str, float]]:
    """Return plain point dictionaries so audit output cannot alias the document."""
    return [_point_dict(point) for point in points]


def _point_dict(value: Any) -> dict[str, float]:
    point = _point(value)
    z = value.get("z", 0) if isinstance(value, dict) else (value[2] if isinstance(value, (list, tuple)) and len(value) > 2 else 0)
    return {"x": float(point[0]), "y": float(point[1]), "z": float(z or 0)}


def copy_points_meta(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize tagged P&ID ports without changing their semantic fields."""
    output = []
    for value in values:
        if not isinstance(value, dict):
            continue
        item = {key: value[key] for key in value if key not in {"point", "vector"}}
        if "point" in value:
            item["point"] = _point_dict(value["point"])
        if "vector" in value:
            item["vector"] = _point_dict(value["vector"])
        output.append(item)
    return output
