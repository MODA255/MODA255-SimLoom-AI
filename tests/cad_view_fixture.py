"""An independent ezdxf drawing with analytically known geometry."""
import math
import ezdxf


def create_view_fixture(path):
    doc = ezdxf.new('R2013', setup=True); doc.units = 4
    for name, color in [('CURVES', 5), ('PART', 3), ('DETAIL', 1), ('FILL', 2), ('DIMS', 7), ('HIDDEN', 6)]:
        doc.layers.new(name, dxfattribs={'color': color})
    doc.layers.get('HIDDEN').off()
    model = doc.modelspace()
    arc = model.add_arc((0, 0), 10, 0, 90, dxfattribs={'layer': 'CURVES'})
    bulge = model.add_lwpolyline([(20, 0, 1), (40, 0, 0)], format='xyb', dxfattribs={'layer': 'CURVES'})
    ellipse = model.add_ellipse((60, 0), (8, 8), .5, dxfattribs={'layer': 'CURVES'})
    inner = doc.blocks.new('INNER')
    inner.add_lwpolyline([(0, 0), (8, 0), (8, 4), (0, 4)], close=True, dxfattribs={'layer': 'DETAIL'})
    outer = doc.blocks.new('OUTER')
    outer.add_blockref('INNER', (0, 0))
    outer.add_attdef('CODE', (1, 1), height=.8)
    insert = model.add_blockref('OUTER', (90, 0), dxfattribs={'layer': 'PART', 'rotation': 90, 'xscale': 2, 'yscale': 2})
    insert.add_auto_attribs({'CODE': 'B1'})
    hatch = model.add_hatch(color=3, dxfattribs={'layer': 'FILL'})
    hatch.paths.add_polyline_path([(110, 0), (130, 0), (130, 20), (110, 20)], is_closed=True, flags=1)
    hatch.paths.add_polyline_path([(115, 5), (115, 15), (125, 15), (125, 5)], is_closed=True, flags=0)
    dim = model.add_aligned_dim((0, -30), (60, -30), -10, dxfattribs={'layer': 'DIMS'})
    dim.render()
    hidden = model.add_circle((10000, 10000), 50, dxfattribs={'layer': 'HIDDEN'})
    text = model.add_text('Native geometry', dxfattribs={'insert': (70, -38), 'height': 3, 'rotation': 8})
    doc.saveas(path)
    return {key: entity.dxf.handle for key, entity in {'arc': arc, 'bulge': bulge, 'ellipse': ellipse,
            'insert': insert, 'hatch': hatch, 'dimension': dim.dimension, 'hidden': hidden, 'text': text}.items()}
