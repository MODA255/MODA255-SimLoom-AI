# CAD hatch editing

`cad.edit_hatch` updates an existing HATCH in one transaction. It accepts an
entity ID and can change its layer, solid/pattern fill, pattern name, angle,
scale, hatch style, double-line flag and (for non-associative fills) a closed
polyline boundary or complete boundary-path list.

```json
{
  "operation": "cad.edit_hatch",
  "project_dir": ".",
  "input_file": "drawing.dxf",
  "output_file": "drawing-filled.dxf",
  "entity_id": "e00023",
  "pattern": "ANSI37",
  "solid": false,
  "pattern_angle": 30,
  "pattern_scale": 0.75,
  "double": true
}
```

The operation checks finite angles, positive scales, valid closed boundaries
and locked layers, then performs the regular independent persisted read-back.
Associative HATCH boundary geometry is protected: style changes are allowed,
but changing its boundary through this operation fails explicitly so the
source entities remain authoritative.
