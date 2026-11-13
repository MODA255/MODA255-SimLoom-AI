# CAD blocks from entities

`cad.block_from_entities` turns a selected set of drawing entities into a
reusable native DXF block definition. The selected geometry is stored relative
to `base_point`, while the source entities are retained by default. Set
`insert: true` to add an INSERT at `insert_point`; set `keep_originals: false`
when the source geometry should be consumed after the block is created.

```json
{
  "operation": "cad.block_from_entities",
  "project_dir": ".",
  "input_file": "drawing.dxf",
  "output_file": "drawing-block.dxf",
  "name": "DETAIL_MARK",
  "entity_ids": ["e00007", "e00008"],
  "base_point": {"x": 10, "y": 20},
  "insert": true,
  "insert_point": {"x": 100, "y": 200}
}
```

The operation checks source and insertion layer locks, duplicate block names,
finite insertion transforms and associative HATCH references. It then writes
the block and any INSERT through the normal transaction, native read-back,
history and rollback path.
