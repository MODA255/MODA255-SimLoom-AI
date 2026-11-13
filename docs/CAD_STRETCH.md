# CAD crossing-window stretch

`cad.entity_stretch` implements a transactional crossing-window edit. Entities
fully enclosed by the window move by the requested delta. For partially
selected geometry, LINE endpoints, polyline vertices, leader vertices, spline
fit/control points, hatch boundary points and dimension definition points move
individually. Dimensions are re-measured after their definition points change.

```python
result = agent.execute_operation("cad.entity_stretch", {
    "project_dir": project_dir,
    "input_file": "drawing.dxf",
    "output_file": "drawing-stretched.dxf",
    "window": [{"x": 0, "y": 0}, {"x": 40, "y": 25}],
    "delta": {"x": 0, "y": 10, "z": 0},
})
```

The optional `layout` limits the operation to Model or one paper-space layout.
Locked layers are rejected before the transaction can publish a result.
Associative HATCH partial-point edits are rejected because changing only part
of their boundary would break the source association; a fully enclosed hatch
can still move as one entity.
