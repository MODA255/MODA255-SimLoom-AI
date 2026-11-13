# CAD text styles

`cad.create_text_style` creates or updates a reusable native DXF `STYLE`
definition inside the same transaction as the drawing. It supports the font,
big font, fixed height, width factor, oblique angle and DXF flags. Existing
styles require `if_exists: "update"` or `"replace"`; the default is an explicit
error so a planner cannot silently change a supplier's typography.

```json
{
  "operation": "cad.create_text_style",
  "project_dir": ".",
  "input_file": "drawing.dxf",
  "output_file": "drawing-styled.dxf",
  "name": "TITLE",
  "font": "Arial.ttf",
  "height": 3,
  "width": 0.8,
  "oblique": 12
}
```

The canonical CAD document now carries `text_styles`, and native DXF read-back
checks that the style definition is present. This defines a reusable style;
it does not rewrite existing text entities to use that style automatically.
