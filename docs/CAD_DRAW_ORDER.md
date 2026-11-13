# CAD draw order

`cad.draw_order` changes the visual redraw order of editable 2D entities in a
transaction. It writes the DXF `SORTENTS` table, so the order survives a native
DXF round trip and remains visible to CAD applications that honor redraw order.

The operation accepts `front`, `back`, `forward`, `backward`, `above` and
`below` actions. `entity_ids` or a named `selection_set` selects the entities;
the selected entities keep their current relative order. `above` and `below`
also require `relative_to`, which must be in the same sheet/layout. `steps`
controls repeated `forward` or `backward` moves.

```json
{
  "operation": "cad.draw_order",
  "project_dir": ".",
  "input_file": "drawing.dxf",
  "output_file": "drawing-front.dxf",
  "entity_ids": ["e00007", "e00012"],
  "action": "front"
}
```

The edit is subject to the same project path, transaction, checkpoint, locked
layer, validation, persisted read-back, history, diff and rollback rules as
the other CAD writes. A stale `.cad.json` sidecar is treated as advisory: the
native entity order is read from the file while stable application IDs are
still taken from the DXF objects themselves.

This is a drawing-order primitive. It does not alter geometry, layer display
state, block definition order or the rendering order of entities inside a
block definition.
