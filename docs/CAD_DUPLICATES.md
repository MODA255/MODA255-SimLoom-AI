# CAD duplicate cleanup

`cad.entity_remove_duplicates` removes geometrically duplicate entities in one
transaction. With no selection it scans the whole drawing; `entity_ids` or a
`selection_set` limits the scan. The first entity in drawing order keeps its
stable ID, while later duplicates are removed.

The comparison uses a positive drawing-unit tolerance and includes layer and
layout, so cleanup does not silently merge geometry from separate drawing
contexts. Reversed LINE endpoints and cyclic closed polylines are normalized.
Selection sets and constraints are redirected to the retained ID, and
associative HATCH source handles are redirected when both handles are present.

```python
result = agent.execute_operation("cad.entity_remove_duplicates", {
    "project_dir": project_dir,
    "input_file": "drawing.dxf",
    "output_file": "drawing-clean.dxf",
    "tolerance": 1e-6,
})
```

The result reports removed IDs, duplicate groups and the applied tolerance.
Malformed or non-positive tolerances fail before the transaction can publish an
output file.
