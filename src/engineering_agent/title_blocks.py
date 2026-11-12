"""Editable paper-space title blocks using native ATTDEF/ATTRIB records."""
from __future__ import annotations

import math
import re
import uuid

from .cad_model import Block, CadDocument

TITLE_APPID = "SIMLOOM_TITLE"
LABELS = {"TITLE": "TITLE", "NUMBER": "DRAWING No.", "REV": "REVISION", "SCALE": "SCALE",
          "DRAWN_BY": "DRAWN BY", "CHECKED_BY": "CHECKED BY", "DATE": "DATE", "SHEET": "SHEET"}


def fields_payload(value):
    if not isinstance(value, dict) or not 1 <= len(value) <= 12:
        raise ValueError("TITLE_FIELDS_REQUIRED: provide 1 to 12 named fields")
    output = {}
    for name, text in value.items():
        name = str(name).upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", name) or name in output:
            raise ValueError("INVALID_TITLE_FIELD:" + name)
        text = str(text)
        if len(text) > 160 or any(ord(c) < 32 for c in text):
            raise ValueError("INVALID_TITLE_FIELD_TEXT:" + name)
        output[name] = text
    return output


def find_title(document, sheet):
    found = [e for e in document.entities if e.get("sheet") == sheet and e.get("title_block_id") and e["type"] == "INSERT"]
    if len(found) > 1:
        raise ValueError("AMBIGUOUS_TITLE_BLOCK:" + sheet)
    return found[0] if found else None


def create_title(document: CadDocument, arguments):
    sheet = str(arguments.get("sheet", ""))
    if sheet not in document.sheets:
        raise ValueError("SHEET_NOT_FOUND:" + sheet)
    if find_title(document, sheet) or document.sheets[sheet].get("title_block"):
        raise ValueError("TITLE_BLOCK_EXISTS:" + sheet)
    fields = fields_payload(arguments.get("fields"))
    properties = document.sheets[sheet]
    factor = 25.4 if properties.get("units") == "in" else 1.
    width = float(arguments.get("width", 180 / factor))
    height = float(arguments.get("height", max(35, math.ceil(len(fields) / 3) * 12 + 18) / factor))
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        raise ValueError("INVALID_TITLE_BLOCK_SIZE")
    origin = arguments.get("origin") or {"x": properties["width"] - width - 10 / factor, "y": 10 / factor}
    origin = {axis: float(origin.get(axis, 0)) for axis in ("x", "y", "z")}
    if not all(math.isfinite(v) for v in origin.values()) or origin["z"] != 0:
        raise ValueError("INVALID_TITLE_BLOCK_ORIGIN")
    if min(origin["x"], origin["y"]) < 0 or origin["x"] + width > properties["width"] + 1e-7 or origin["y"] + height > properties["height"] + 1e-7:
        raise ValueError("TITLE_BLOCK_OUTSIDE_SHEET")
    layer = str(arguments.get("layer", "TITLE_BLOCK"))
    document.ensure_layer(layer)
    title_id = str(arguments.get("title_block_id") or "tb-" + sheet)
    if not title_id or len(title_id) > 100:
        raise ValueError("INVALID_TITLE_BLOCK_ID")
    block_name = "SIMLOOM_TITLE_" + uuid.uuid4().hex[:12].upper()
    entities, attributes, field_widths = [], [], {}

    def line(x1, y1, x2, y2):
        entities.append({"type": "LINE", "layer": "0", "start": {"x": x1, "y": y1, "z": 0}, "end": {"x": x2, "y": y2, "z": 0}})

    def cell(tag, text, x, y, w, h):
        label_height = min(1.8 / factor, h * .19)
        value_height = min(float(arguments.get("text_height", 3 / factor)), h * .30)
        if not math.isfinite(value_height) or value_height <= 0:
            raise ValueError("INVALID_TITLE_TEXT_HEIGHT")
        inset = min(2 / factor, w * .08)
        entities.append({"type": "TEXT", "layer": "0", "insert": {"x": x + inset, "y": y + h - 1.4 * label_height, "z": 0},
                         "height": label_height, "text": LABELS.get(tag, tag), "color_index": 8})
        position = {"x": x + inset, "y": y + h * .22, "z": 0}
        available = w - 2 * inset
        common = {"tag": tag, "height": value_height, "width_factor": min(1., available / max(value_height * len(text), value_height)), "flags": 0}
        entities.append({"type": "ATTDEF", "layer": "0", "insert": position, "text": "", **common})
        attributes.append({"type": "ATTRIB", "layer": layer, "insert": {a: position[a] + origin[a] for a in origin}, "text": text, **common})
        field_widths[tag] = available

    line(0, 0, width, 0); line(width, 0, width, height)
    line(width, height, 0, height); line(0, height, 0, 0)
    rest = [(k, v) for k, v in fields.items() if k != "TITLE"]
    title_height = height * .36 if "TITLE" in fields and rest else height if "TITLE" in fields else 0.
    if "TITLE" in fields:
        cell("TITLE", fields["TITLE"], 0, height - title_height, width, title_height)
        if rest: line(0, height - title_height, width, height - title_height)
    if rest:
        rows = math.ceil(len(rest) / 3)
        row_height = (height - title_height) / rows
        for row in range(rows):
            entries = rest[row * 3:(row + 1) * 3]
            y = (rows - row - 1) * row_height
            for col, (tag, value) in enumerate(entries):
                cell_width = width / len(entries)
                if col: line(col * cell_width, y, col * cell_width, y + row_height)
                cell(tag, value, col * cell_width, y, cell_width, row_height)
            if row: line(0, y + row_height, width, y + row_height)
    document.blocks[block_name] = Block(block_name, {"x": 0, "y": 0, "z": 0}, entities)
    insert = {"id": document.next_id("tb"), "type": "INSERT", "layer": layer, "sheet": sheet,
              "block_name": block_name, "insert": origin, "scale": {"x": 1, "y": 1, "z": 1}, "rotation": 0,
              "attributes": attributes, "title_block_id": title_id, "title_field_widths": field_widths}
    document.entities.append(insert)
    info = {"id": title_id, "block_name": block_name, "origin": origin, "width": width, "height": height,
            "fields": fields, "entity_ids": [insert["id"]], "template": "simloom-grid/v1"}
    document.sheets[sheet]["title_block"] = info
    return {"title_block": info, "entities": [insert["id"]]}


def update_title(document: CadDocument, arguments):
    sheet = str(arguments.get("sheet", ""))
    entity = find_title(document, sheet)
    if entity is None:
        raise ValueError("NATIVE_TITLE_BLOCK_NOT_FOUND:" + sheet)
    fields = fields_payload(arguments.get("fields"))
    attrs = {item["tag"]: item for item in entity["attributes"]}
    unknown = set(fields) - set(attrs)
    if unknown:
        raise ValueError("TITLE_FIELD_NOT_FOUND:" + ",".join(sorted(unknown)))
    for tag, text in fields.items():
        attr = attrs[tag]
        width = entity.get("title_field_widths", {}).get(tag)
        if width is None:
            # When a sidecar is unavailable, retain the native text's previous
            # width factor; changing fields never reconstructs the block.
            width = max(1, len(attr.get("text", ""))) * attr.get("height", 3) * attr.get("width_factor", 1)
        attr["text"] = text
        attr["width_factor"] = min(1., width / max(attr.get("height", 3) * len(text), attr.get("height", 3)))
    info = document.sheets[sheet].setdefault("title_block", {"id": entity["title_block_id"], "block_name": entity["block_name"], "entity_ids": [entity["id"]]})
    info["fields"] = {tag: item["text"] for tag, item in attrs.items()}
    return {"title_block": info, "entities": [entity["id"]]}
