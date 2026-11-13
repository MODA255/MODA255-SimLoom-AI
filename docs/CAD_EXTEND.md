# CAD Extend

`cad.entity_extend` extends one LINE endpoint to the nearest intersection with
one or more finite boundary entities. The operation is transactional and uses
the same validation, diff, checkpoint, history and rollback path as the other
CAD writes.

```python
result = agent.execute_operation("cad.entity_extend", {
    "project_dir": project_dir,
    "input_file": "drawing.dxf",
    "output_file": "drawing-extended.dxf",
    "target": "e00001",
    "edges": ["e00004", "e00007"],
    "end": "auto",  # start, end or auto
})
```

Supported finite boundaries are `LINE`, `CIRCLE`, `ARC`, `ELLIPSE`, `SPLINE`,
`LWPOLYLINE` and `POLYLINE`. A polyline's straight and bulge arc segments are
treated as finite edges. The target is extended along its infinite supporting
line, while an intersection already inside the current segment is rejected so
that the operation cannot silently shorten the line.

`target`, `edges`, `end` and optional `layout` are the public operation names.
`entity_id`, `boundary_entity_id` and `boundary_entity_ids` remain accepted as
compatibility aliases. `auto` chooses the closest valid extension on either
side. A locked target layer, mismatched layouts, parallel boundaries, missing
intersections and zero-length targets return stable `EXTEND_*` errors and leave
the source and output files unchanged.
