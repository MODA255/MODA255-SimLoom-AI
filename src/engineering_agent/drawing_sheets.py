"""Prepare a printable engineering sheet in one undoable CAD transaction."""
import math

from .plotting import PAPERS
from .title_blocks import create_title


def prepare_sheet(document, arguments):
    from ezdxf import bbox, units
    from ezdxf.addons.drawing import RenderContext
    from .native_dxf import build_native
    name = str(arguments.get("name", "A3"))
    paper = str(arguments.get("paper", "A3"))
    orientation = arguments.get("orientation", "landscape")
    if not name or len(name) > 100 or name == "Model" or name in document.sheets:
        raise ValueError("SHEET_NAME_INVALID_OR_EXISTS:" + name)
    if paper not in PAPERS or orientation not in {"landscape", "portrait"}:
        raise ValueError("INVALID_SHEET_PAPER")
    if not document._source_path:
        raise ValueError("SHEET_PREPARE_REQUIRES_NATIVE_DXF")
    # Include preceding operations in the same batch, while keeping generated
    # handles out of the live document until the normal write/validation step.
    draft = document.clone()
    source = build_native(entities=draft.entities, blocks=[b.to_dict() for b in draft.blocks.values()],
                          coordinate_systems=draft.coordinate_systems, layers={n: l.to_dict() for n, l in draft.layers.items()},
                          sheets=draft.sheets, viewports=draft.viewports, source=draft._source_path, units=draft.units)
    context = RenderContext(source, export_mode=True)
    visible = [e for e in source.modelspace() if context.resolve_all(e).is_visible]
    bounds = bbox.extents(visible, fast=False)
    if not bounds.has_data:
        raise ValueError("SHEET_PREPARE_NO_MODEL_GEOMETRY")
    width, height = PAPERS[paper]
    if orientation == "landscape": width, height = height, width
    fields = dict(arguments.get("fields") or {"TITLE": document.name, "NUMBER": "", "REV": "A"})
    fields.setdefault("SCALE", "FIT")
    fields.setdefault("SHEET", name)
    title_height = max(35., math.ceil((len(fields) - 1) / 3) * 12 + 18)
    vp_width, vp_height = width - 20, height - title_height - 28
    if vp_height <= 0: raise ValueError("SHEET_TOO_SMALL_FOR_TITLE_BLOCK")
    size = bounds.size
    scale = min(vp_width / max(size.x * 1.12, 1), vp_height / max(size.y * 1.12, 1))
    if source.units:
        physical_scale = scale / units.conversion_factor(source.units, units.MM)
        fields["SCALE"] = f"1:{1 / physical_scale:.4g}" if physical_scale < 1 else f"{physical_scale:.4g}:1"
    document.sheets[name] = {"name": name, "width": width, "height": height, "units": "mm", "title_block": None}
    viewport_name = name + "-MAIN"
    if viewport_name in document.viewports: raise ValueError("VIEWPORT_EXISTS:" + viewport_name)
    document.viewports[viewport_name] = {
        "name": viewport_name, "sheet": name, "width": vp_width, "height": vp_height, "scale": scale, "locked": True,
        "center": {"x": width / 2, "y": title_height + 18 + vp_height / 2, "z": 0},
        "view_center": {"x": bounds.center.x, "y": bounds.center.y, "z": 0},
    }
    title = create_title(document, {"sheet": name, "fields": fields, "width": min(180., width - 20), "height": title_height})
    return {"sheet": document.sheets[name], "viewport": document.viewports[viewport_name], **title}
