"""Render DXF drawings to physical PDF pages or PNG images under a transaction."""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path

from .transactions import RunTransaction, confined_path, file_hash

PAPERS = {"A4": (210., 297.), "A3": (297., 420.), "A2": (420., 594.), "A1": (594., 841.), "A0": (841., 1189.)}
_RENDER_LOCK = threading.RLock()  # Matplotlib/font caches are shared by HTTP workers.


def _number(value, name, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError("INVALID_PLOT_" + name)
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError("INVALID_PLOT_" + name)
    return result


def _options(arguments):
    layout = str(arguments.get("layout", "Model"))
    paper = str(arguments.get("paper", "A4" if layout == "Model" else "layout"))
    orientation = str(arguments.get("orientation", "landscape"))
    if paper not in {*PAPERS, "layout"} or orientation not in {"landscape", "portrait"}:
        raise ValueError("INVALID_PLOT_PAPER")
    if layout == "Model" and paper == "layout":
        raise ValueError("MODELSPACE_REQUIRES_PAPER_SIZE")
    scale = arguments.get("scale", "fit" if layout == "Model" else "1:1")
    if scale != "fit":
        if not isinstance(scale, str) or ":" not in scale:
            raise ValueError("INVALID_PLOT_SCALE: use fit or a ratio such as 1:2")
        numerator, denominator = scale.split(":", 1)
        scale = _number(numerator, "SCALE", 1e-6, 1e6) / _number(denominator, "SCALE", 1e-6, 1e6)
    if layout != "Model" and (paper != "layout" or scale != 1.):
        raise ValueError("PAPERSPACE_USES_OWN_PAGE_AT_1_TO_1")
    units = arguments.get("drawing_units")
    if units is not None and units not in {"mm", "cm", "m", "in", "ft"}:
        raise ValueError("INVALID_PLOT_DRAWING_UNITS")
    colors = arguments.get("color_mode", "monochrome")
    if colors not in {"monochrome", "color"}:
        raise ValueError("INVALID_PLOT_COLOR_MODE")
    for key in ("strict", "overwrite"):
        if key in arguments and not isinstance(arguments[key], bool):
            raise ValueError("INVALID_PLOT_" + key.upper())
    return {"layout": layout, "paper": paper, "orientation": orientation, "scale": scale,
            "margin_mm": _number(arguments.get("margin_mm", 10), "MARGIN", 0, 100),
            "dpi": _number(arguments.get("dpi", 150), "DPI", 72, 600),
            "drawing_units": units, "color_mode": colors, "strict": arguments.get("strict", True)}


def render_drawing(source: Path, target: Path, arguments):
    """Render primitives, native dimensions, INSERTs and paper-space viewports."""
    with _RENDER_LOCK:
        return _render_drawing(source, target, arguments)


def _render_drawing(source, target, arguments):
    import ezdxf
    from ezdxf import units
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    options = _options(arguments)
    drawing = ezdxf.readfile(source)
    if options["layout"] not in drawing.layouts:
        raise ValueError("PLOT_LAYOUT_NOT_FOUND:" + options["layout"])
    layout = drawing.layouts.get(options["layout"])
    paper_space = layout.name != "Model"
    notes, skipped = [], []
    if paper_space:
        dxf = layout.dxf_layout.dxf
        width, height = dxf.paper_width, dxf.paper_height
        unit_mm = 25.4 if dxf.plot_paper_units == 0 else 1.
        # Paper-space coordinates use the layout's paper units, independent of
        # model units. Print the full physical sheet, preserving viewport scale.
        limits = (0., width / unit_mm, 0., height / unit_mm)
    else:
        width, height = PAPERS[options["paper"]]
        if options["orientation"] == "landscape": width, height = height, width
        unit_code = {"mm": 4, "cm": 5, "m": 6, "in": 1, "ft": 2}.get(options["drawing_units"], drawing.units)
        unit_mm = units.conversion_factor(unit_code, units.MM) if unit_code else None
        if unit_mm is None and options["scale"] != "fit":
            raise ValueError("PLOT_DRAWING_UNITS_REQUIRED: choose units for an unscaled DXF")
        limits = None
    for value in (width, height):
        _number(value, "PAGE_SIZE", 10, 2000)
    if width * height * (options["dpi"] / 25.4) ** 2 > 40_000_000:
        raise ValueError("PLOT_IMAGE_TOO_LARGE: lower the resolution or paper size")
    if min(width, height) <= options["margin_mm"] * 2:
        raise ValueError("PLOT_MARGIN_EXCEEDS_PAGE")

    class ReportingFrontend(Frontend):
        def check_text(self, entity, properties):
            from ezdxf.fonts import fonts
            face = self.get_font_face(properties)
            if Path(face.filename).suffix.lower() in {".ttf", ".ttc", ".otf"}:
                cmap = fonts.font_manager.get_ttf_font(face.filename).getBestCmap() or {}
                text = entity.plain_text()
                missing = sorted({char for char in text if not char.isspace() and ord(char) not in cmap})
                if missing:
                    detail = {"code": "PLOT_MISSING_GLYPHS", "font": face.filename, "characters": "".join(missing)}
                    if options["strict"]:
                        raise ValueError("PLOT_MISSING_GLYPHS:" + json.dumps(detail, ensure_ascii=False))
                    if detail not in notes:
                        notes.append(detail)

        def draw_text_entity(self, entity, properties):
            self.check_text(entity, properties)
            return super().draw_text_entity(entity, properties)

        def draw_mtext_entity(self, entity, properties):
            self.check_text(entity, properties)
            return super().draw_mtext_entity(entity, properties)

        def log_message(self, message):
            # Frontend announces this for every file at initialization, even
            # with no POINT entities. Report it only when a sized point uses it.
            if message == "relative point size is not supported":
                return
            notes.append({"code": "RENDER_NOTE", "message": str(message)})

        def draw_point_entity(self, point, properties):
            if (drawing.header.get("$PDSIZE", 0) <= 0 and self.config.pdmode != 0
                    and properties.layer.lower() != "defpoints"):
                note = {"code": "RELATIVE_POINT_SIZE", "message": "Relative POINT sizes are rendered as 1 drawing unit"}
                if note not in notes:
                    notes.append(note)
            return super().draw_point_entity(point, properties)

        def skip_entity(self, entity, msg):
            if msg != "invisible":
                skipped.append({"type": entity.dxftype(), "handle": entity.dxf.get("handle"), "reason": msg})

        def draw_viewport(self, viewport):
            if not viewport.is_top_view:
                self.skip_entity(viewport, "Only top-view viewports are supported")
                return
            return super().draw_viewport(viewport)

    figure = Figure(figsize=(width / 25.4, height / 25.4), dpi=options["dpi"], facecolor="white")
    FigureCanvasAgg(figure)
    axes = figure.add_axes((0, 0, 1, 1))
    backend = MatplotlibBackend(axes, adjust_figure=False)
    context = RenderContext(drawing, export_mode=True)
    config = Configuration(background_policy=BackgroundPolicy.WHITE,
                           color_policy=ColorPolicy.BLACK if options["color_mode"] == "monochrome" else ColorPolicy.COLOR,
                           # DXF lineweights are millimetres; matplotlib uses
                           # typographic points, including for vector PDF.
                           lineweight_scaling=72 / 25.4, min_lineweight=.1 * 72 / 25.4)
    try:
        frontend = ReportingFrontend(context, backend, config=config)
        frontend.draw_layout(layout, finalize=True)
        primitive_count = len(axes.lines) + len(axes.patches) + len(axes.collections) + len(axes.images)
        if skipped and options["strict"]:
            raise ValueError("PLOT_UNSUPPORTED_ENTITIES:" + json.dumps(skipped, ensure_ascii=False))
        if not primitive_count or not all(math.isfinite(v) for v in axes.dataLim.extents):
            raise ValueError("PLOT_NO_VISIBLE_GEOMETRY")
        if skipped: notes.append({"code": "PLOT_SKIPPED_ENTITIES", "entities": skipped})
        x0, y0, x1, y1 = [float(v) for v in axes.dataLim.extents]
        span_x, span_y = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
        if not paper_space:
            usable_width = width - 2 * options["margin_mm"]
            usable_height = height - 2 * options["margin_mm"]
            mm_per_unit = min(usable_width / span_x, usable_height / span_y) if options["scale"] == "fit" else unit_mm * options["scale"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            limits = (cx - width / mm_per_unit / 2, cx + width / mm_per_unit / 2,
                      cy - height / mm_per_unit / 2, cy + height / mm_per_unit / 2)
            if options["scale"] != "fit" and (span_x * mm_per_unit > usable_width + 1e-7 or span_y * mm_per_unit > usable_height + 1e-7):
                raise ValueError("PLOT_SCALE_DOES_NOT_FIT: choose a smaller scale or larger paper")
        else:
            mm_per_unit = unit_mm
            if x0 < limits[0] - 1e-6 or x1 > limits[1] + 1e-6 or y0 < limits[2] - 1e-6 or y1 > limits[3] + 1e-6:
                notes.append({"code": "PAPERSPACE_CONTENT_CLIPPED", "message": "Content outside the physical sheet is clipped"})
        axes.set_autoscale_on(False)
        axes.set_xlim(limits[0], limits[1]); axes.set_ylim(limits[2], limits[3])
        axes.set_aspect("equal", adjustable="box")
        axes.set_axis_off()
        # Visible geometry may still lie entirely outside a paper-space page.
        # Check the composed page so even vector PDF cannot pass as all white.
        import numpy as np
        figure.canvas.draw()
        if np.asarray(figure.canvas.buffer_rgba())[:, :, :3].min() == 255:
            raise ValueError("PLOT_NO_VISIBLE_GEOMETRY_ON_PAGE")
        target.parent.mkdir(parents=True, exist_ok=True)
        metadata = {"Title": source.stem, "Creator": "SimLoom AI"} if target.suffix.lower() == ".pdf" else {"Software": "SimLoom AI"}
        # No bbox_inches='tight': trimming would silently change paper size and scale.
        figure.savefig(target, format=target.suffix[1:].lower(), dpi=options["dpi"], facecolor="white", metadata=metadata)
        return {"layout": layout.name, "page_mm": {"width": width, "height": height}, "dpi": options["dpi"],
                "color_mode": options["color_mode"], "scale": arguments.get("scale", "1:1" if paper_space else "fit"),
                "model_to_paper_scale": mm_per_unit / unit_mm if unit_mm else None,
                "mm_per_drawing_unit": mm_per_unit, "world_bounds": list(limits),
                "primitive_count": primitive_count, "warnings": notes,
                "renderer": "ezdxf/matplotlib", "format": target.suffix[1:].lower()}
    finally:
        figure.clear()


def validate_plot(path: Path, report):
    """Decode the exported artifact, independently from the drawing renderer."""
    width, height = report["page_mm"]["width"], report["page_mm"]["height"]
    if report["format"] == "pdf":
        from pypdf import PdfReader
        pdf = PdfReader(path, strict=True)
        if len(pdf.pages) != 1:
            raise ValueError("PLOT_PDF_PAGE_COUNT_MISMATCH")
        page = pdf.pages[0]
        actual = (float(page.mediabox.width) * 25.4 / 72, float(page.mediabox.height) * 25.4 / 72)
        if not all(math.isclose(a, b, abs_tol=.01) for a, b in zip(actual, (width, height))):
            raise ValueError("PLOT_PDF_PAGE_SIZE_MISMATCH")
        contents = page.get_contents()
        if contents is None or not contents.get_data():
            raise ValueError("PLOT_PDF_EMPTY")
        return {"valid": True, "pages": 1, "page_mm": list(actual), "bytes": path.stat().st_size}
    from PIL import Image, ImageChops
    with Image.open(path) as image:
        image.load()
        if image.format != "PNG": raise ValueError("PLOT_PNG_FORMAT_MISMATCH")
        expected = (width / 25.4 * report["dpi"], height / 25.4 * report["dpi"])
        if any(abs(a - b) > 1 for a, b in zip(image.size, expected)):
            raise ValueError("PLOT_PNG_SIZE_MISMATCH")
        if ImageChops.difference(image.convert("RGB"), Image.new("RGB", image.size, "white")).getbbox() is None:
            raise ValueError("PLOT_PNG_BLANK")
        return {"valid": True, "pixels": list(image.size), "bytes": path.stat().st_size}


def plot_operation(arguments):
    input_path = Path(arguments["input_file"]).expanduser()
    root = Path(arguments.get("project_dir") or input_path.parent).expanduser().resolve()
    source = (root / input_path if arguments.get("project_dir") and not input_path.is_absolute() else input_path).resolve()
    output = Path(arguments["output_file"]).expanduser()
    output = root / output if not output.is_absolute() else output
    if root not in source.parents or root not in output.resolve().parents:
        raise ValueError("PATH_OUTSIDE_PROJECT")
    relative = str(output.relative_to(root))
    target = confined_path(root, relative)
    if target.suffix.lower() not in {".pdf", ".png"} or source.suffix.lower() != ".dxf":
        raise ValueError("PLOT_REQUIRES_DXF_TO_PDF_OR_PNG")
    if relative.startswith((".engineering-agent/", "artifacts/runs/")):
        raise ValueError("PLOT_RESERVED_OUTPUT_PATH")
    _options(arguments)
    with RunTransaction(root) as transaction:
        transaction.begin({"operation": "cad.plot", "input_file": str(source), "output_file": str(target)})
        if not source.is_file(): raise FileNotFoundError(source)
        report_target = Path(str(target) + ".plot.json")
        if not arguments.get("overwrite", False) and (target.exists() or report_target.exists()):
            raise FileExistsError("PLOT_OUTPUT_EXISTS:" + str(target))
        source_hash = file_hash(source)
        transaction.checkpoint("before-plot", {"source_sha256": source_hash})
        staged = transaction.stage_dir / target.name
        report = render_drawing(source, staged, arguments)
        report.update(schema_version="simloom-ai/plot/v1", source=str(source.relative_to(root)), source_sha256=source_hash,
                      output=relative, output_sha256=file_hash(staged), run_id=transaction.run_id)
        validation = validate_plot(staged, report)
        if file_hash(source) != source_hash:
            raise RuntimeError("PLOT_SOURCE_CONFLICT")
        staged_report = Path(str(staged) + ".plot.json")
        staged_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        transaction.checkpoint("after-plot-validation", validation)

        def validate(_files):
            if file_hash(source) != source_hash: raise RuntimeError("PLOT_SOURCE_CONFLICT")
            if file_hash(target) != report["output_sha256"]: raise ValueError("PLOT_OUTPUT_HASH_MISMATCH")
            validation.update(validate_plot(target, report))
            transaction.record("plot_validated", validation)

        transaction.commit({relative: str(staged), relative + ".plot.json": str(staged_report)}, destination_root=root, validate=validate)
        return {"operation": "cad.plot", "path": str(target), "relative_path": relative,
                "report_path": str(report_target), "report": report, "validation": validation, "run_id": transaction.run_id}
