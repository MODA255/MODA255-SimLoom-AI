# CAD center marks

`cad.center_mark_create` adds editable horizontal and vertical center lines to
selected circles or arcs. It is useful for manufacturing drawings and follows
the normal CAD transaction path, including validation, diff, history and
rollback.

```python
result = agent.execute_operation("cad.center_mark_create", {
    "project_dir": project_dir,
    "input_file": "drawing.dxf",
    "output_file": "drawing-marked.dxf",
    "target": "e00012",
    "extension": 2,
    "layer": "CENTER",
    "tag": "hole-centers",
})
```

The default extension is `max(2, radius / 2)`. Multiple targets can be passed
through `entity_ids` or a named `selection_set`; tagged output is stored as
`tag.1`, `tag.2`, and so on. `if_tag_exists` supports `error`, `skip`, and
`replace`, which makes retries safe without creating duplicate marks. The
operation checks the target layout and destination layer lock before writing.
