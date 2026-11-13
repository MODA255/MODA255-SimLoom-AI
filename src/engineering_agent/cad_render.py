"""Native DXF-to-SVG paths with provenance for interactive CAD selection.

Each visible primitive retains its top-level model entity and effective layer
ancestry. Nested INSERTs, ATTRIBs and DIMENSION graphics remain one editable
object even though the renderer emits many paths for them.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET

from .plotting import _RENDER_LOCK


def _drawing(geometry, source_path):
    import ezdxf
    if source_path is not None:
        return ezdxf.readfile(source_path)
    from .native_dxf import build_native
    blocks = geometry.get('blocks') or {}
    layers = geometry.get('layer_styles') or geometry.get('layers') or {}
    layers = {name: value for name, value in layers.items() if isinstance(value, dict)}
    return build_native(entities=copy.deepcopy(geometry.get('entities', [])),
                        blocks=copy.deepcopy(list(blocks.values()) if isinstance(blocks, dict) else blocks),
                        layers=copy.deepcopy(layers), sheets=copy.deepcopy(geometry.get('sheets', {})),
                        viewports=copy.deepcopy(geometry.get('viewports', {})), units=geometry.get('units', 'mm'))


def native_model_scene(geometry, source_path=None):
    with _RENDER_LOCK:
        return _native_model_scene(geometry, source_path)


def _native_model_scene(geometry, source_path):
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy
    from ezdxf.addons.drawing.svg import SVGBackend, SVGRenderBackend
    from ezdxf.addons.drawing.layout import Page, Margins, Settings
    from ezdxf.math import BoundingBox2d
    from .native_dxf import entity_id

    drawing = _drawing(geometry, source_path)
    by_handle = {e.get('handle'): e for e in geometry.get('entities', []) if e.get('handle')} if source_path else {}
    entities = {}
    for entity in drawing.modelspace():
        model = by_handle.get(entity.dxf.handle, {})
        identifier = str(model.get('id') or entity_id(entity))
        entities[entity.dxf.handle] = {'id': identifier, 'type': entity.dxftype(), 'layer': entity.dxf.layer,
                                     'text': str(model.get('text') or entity.dxf.get('text', '')) if entity.dxf.is_supported('text') else '',
                                     'handle': entity.dxf.handle}
    layers = {layer.dxf.name: {'visible': layer.is_on() and not layer.is_frozen(), 'locked': layer.is_locked()}
              for layer in drawing.layers}
    # Visibility switches are preview-only. Include hidden/frozen geometry in
    # the SVG so switching a layer on never edits the supplier's DXF.
    for layer in drawing.layers:
        layer.on(); layer.thaw()
    provenance, warnings, warning_keys = {}, [], set()

    def warn(code, **detail):
        item = {'code': code, **detail}
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key not in warning_keys:
            warning_keys.add(key); warnings.append(item)

    class InteractiveSVG(SVGRenderBackend):
        def mark(self, before, properties, kind):
            if len(self.entities) == before:
                return
            element = self.entities[-1]
            info = provenance.get(properties.handle)
            if info is None:
                raise ValueError('CAD_VIEWER_MISSING_ENTITY_PROVENANCE')
            item = info['entity']
            element.set('class', element.get('class', '') + ' entity')
            element.set('data-entity-id', item['id'])
            element.set('data-entity-type', item['type'])
            element.set('data-layer', properties.layer)
            element.set('data-layers', json.dumps(info['layers'], ensure_ascii=False))
            element.set('data-draw-kind', kind)
            element.set('data-handle', item['handle'])
            # Keep the semantic entity close to its rendered primitive so the
            # browser can offer geometry-aware grips without reverse
            # engineering an SVG path.  The parent operation still validates
            # and commits the resulting patch; this is display metadata only.
            element.set('data-entity-json', json.dumps(item, ensure_ascii=False, separators=(',', ':')))
            label = item['text'] or item['type'] + ' ' + item['id']
            ET.SubElement(element, 'title').text = label
            if not all(layers.get(name, {'visible': True})['visible'] for name in info['layers']):
                element.set('style', 'display:none')
            if kind == 'stroke':
                # A transparent pick stroke improves thin-line selection while
                # retaining the native curve (including its gaps and angles).
                hit = copy.deepcopy(element)
                hit.set('class', 'entity pick-stroke')
                hit.set('data-draw-kind', 'hit')
                self.entities.append(hit)

        def add_strokes(self, d, properties):
            before = len(self.entities)
            super().add_strokes(d, properties)
            self.mark(before, properties, 'stroke')

        def add_filling(self, d, properties):
            before = len(self.entities)
            super().add_filling(d, properties)
            self.mark(before, properties, 'fill')

    class ProvenanceBackend(SVGBackend):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.keys = {}

        def enter_entity(self, entity, properties):
            self.stack.append((entity, properties))

        def exit_entity(self, entity):
            current, _ = self.stack.pop()
            if current is not entity:
                raise ValueError('CAD_VIEWER_ENTITY_STACK_MISMATCH')

        def store(self, record, properties):
            if len(self.records) >= 100_000:
                raise ValueError('CAD_VIEWER_PRIMITIVE_LIMIT: drawing exceeds 100000 paths')
            root = self.stack[0][0] if self.stack else None
            handle = root.dxf.handle if root is not None else properties.handle
            item = entities.get(handle)
            if item is None:
                raise ValueError('CAD_VIEWER_UNKNOWN_ENTITY:' + str(handle))
            ancestry = tuple(dict.fromkeys([item['layer'], *(p.layer for _, p in self.stack), properties.layer]))
            key = (handle, ancestry)
            token = self.keys.setdefault(key, str(len(self.keys)))
            provenance[token] = {'entity': item, 'layers': ancestry}
            super().store(record, properties._replace(handle=token))

        @staticmethod
        def make_backend(page, settings):
            return InteractiveSVG(page, settings)

    class ReportingFrontend(Frontend):
        def draw_entity(self, entity, properties):
            before = len(backend.records)
            super().draw_entity(entity, properties)
            if (not entity.is_virtual and len(backend.records) == before
                    and entity.dxftype() not in {'POINT', 'TEXT', 'MTEXT'}):
                self.skip_entity(entity, 'No renderable primitives were produced')

        def log_message(self, message):
            if message != 'relative point size is not supported':
                warn('RENDER_NOTE', message=str(message))

        def skip_entity(self, entity, msg):
            if msg != 'invisible':
                warn('VIEWER_ENTITY_SKIPPED', type=entity.dxftype(), handle=entity.dxf.get('handle'), reason=msg)

        def draw_image_entity(self, entity, properties):
            self.skip_entity(entity, 'Raster IMAGE is not supported by the SVG viewer')

        def check_font(self, entity, properties):
            from ezdxf.fonts import fonts
            face = self.get_font_face(properties)
            if Path(face.filename).suffix.lower() in {'.ttf', '.ttc', '.otf'}:
                cmap = fonts.font_manager.get_ttf_font(face.filename).getBestCmap() or {}
                missing = sorted({c for c in entity.plain_text() if not c.isspace() and ord(c) not in cmap})
                if missing:
                    warn('VIEWER_MISSING_GLYPHS', font=face.filename, characters=''.join(missing))

        def draw_text_entity(self, entity, properties):
            self.check_font(entity, properties)
            return super().draw_text_entity(entity, properties)

        def draw_mtext_entity(self, entity, properties):
            self.check_font(entity, properties)
            return super().draw_mtext_entity(entity, properties)

        def draw_point_entity(self, entity, properties):
            if drawing.header.get('$PDSIZE', 0) <= 0 and self.config.pdmode != 0 and properties.layer.lower() != 'defpoints':
                warn('RELATIVE_POINT_SIZE', message='Relative POINT sizes are rendered as 1 drawing unit')
            return super().draw_point_entity(entity, properties)

    backend = ProvenanceBackend()
    context = RenderContext(drawing)
    frontend = ReportingFrontend(context, backend, config=Configuration(background_policy=BackgroundPolicy.WHITE))
    frontend.draw_layout(drawing.modelspace(), finalize=True)
    visible_bounds, all_bounds = BoundingBox2d(), BoundingBox2d()
    for record in backend.records:
        bounds = record.bbox()
        if bounds.has_data:
            all_bounds.extend(bounds)
            info = provenance[record.handle]
            if all(layers.get(layer, {'visible': True})['visible'] for layer in info['layers']):
                visible_bounds.extend(bounds)
    bounds = visible_bounds if visible_bounds.has_data else all_bounds
    if not bounds.has_data:
        bounds = BoundingBox2d([(0, 0), (1, 1)])
        warn('VIEWER_NO_GEOMETRY', message='No supported model-space geometry to display')
    if not all(math.isfinite(v) for p in (bounds.extmin, bounds.extmax) for v in p):
        raise ValueError('CAD_VIEWER_NONFINITE_BOUNDS')
    # Expand zero-width/height bounds before the SVG placement calculation.
    center = bounds.center
    half_x, half_y = max(bounds.size.x / 2, .5), max(bounds.size.y / 2, .5)
    render_box = BoundingBox2d([(center.x-half_x, center.y-half_y), (center.x+half_x, center.y+half_y)])
    root = backend.get_xml_root_element(Page(297, 200, margins=Margins.all(10)),
                                       settings=Settings(output_coordinate_space=1_000_000), render_box=render_box)
    matrix = backend.transformation_matrix
    origin = matrix.transform((0, 0, 0))
    axis_x, axis_y = [matrix.transform_direction(p) for p in ((1, 0, 0), (0, 1, 0))]
    transform = [axis_x.x, axis_x.y, axis_y.x, axis_y.y, origin.x, origin.y]
    root.set('id', 'cad'); root.set('class', 'cad-canvas'); root.set('role', 'img')
    root.set('aria-label', '2D CAD native geometry viewer')
    root.set('data-renderer', 'ezdxf-svg')
    root.set('data-world-transform', json.dumps(transform))
    root.set('data-initial-viewbox', root.get('viewBox'))
    for key in ('width', 'height'):
        root.attrib.pop(key, None)
    svg = ET.tostring(root, encoding='unicode')
    if len(svg.encode('utf-8')) > 30_000_000:
        raise ValueError('CAD_VIEWER_OUTPUT_LIMIT: preview exceeds 30 MB')
    return {'svg': svg, 'layers': layers, 'warnings': warnings, 'entity_count': len(entities),
            'rendered_entities': len({info['entity']['id'] for info in provenance.values()}),
            'primitive_count': len(backend.records), 'world_bounds': [*bounds.extmin, *bounds.extmax],
            'world_to_svg': transform, 'schema_version': 'simloom-ai/cad-view/v1',
            'source_sha256': hashlib.sha256(Path(source_path).read_bytes()).hexdigest() if source_path else None}
