# CAD 块属性

`cad.block_attribute_create` 在现有块定义中创建或更新原生 `ATTDEF`，并为引用该块的
`INSERT` 自动创建或更新对应的 `ATTRIB`。块内插入点按每个 INSERT 的基点、缩放和旋转转换
到图纸坐标，属性值和稳定引用会进入 sidecar 与原生 DXF。

```json
{
  "operation": "cad.block_attribute_create",
  "input_file": "drawing.dxf",
  "output_file": "drawing-labelled.dxf",
  "block_name": "LABEL",
  "tag": "PART_NO",
  "text": "P-1001",
  "prompt": "Part number",
  "insert": {"x": 2, "y": 3},
  "height": 2.5,
  "if_exists": "update"
}
```

属性标签必须唯一。默认重复标签会失败，`if_exists=update` 会更新块定义及其现有引用。
这项能力用于图纸块和标题栏等二维辅助对象；真实 CATIA 产品属性仍通过 Windows worker 操作。
