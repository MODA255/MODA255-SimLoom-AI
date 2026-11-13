# CAD 标注比例

`cad.set_annotation_scale` 在统一 CAD 对象模型中保存图纸标注比例，并把比例写入原生 DXF
`$DIMSCALE`。它可以作用于全部适用标注，也可以通过 `entity_ids`、`selection_set` 或
`layout` 限制范围。

## 语义

- `scale` 必须是有限的正数。
- TEXT、MTEXT、ATTDEF 和 ATTRIB 的文字高度按 `scale / previous_scale` 调整；MTEXT 的
  非零宽度也按同一比例调整。
- DIMENSION、LEADER、GD&T、表面粗糙度和 BOM 类实体保留 `annotation_scale` 元数据。
- 模型线、圆、弧和其他实际几何不改变；尺寸的实际测量值不改变。
- 锁定图层会拒绝写入，事务失败时原文件保持不变。

## 示例

```json
{
  "operation": "cad.set_annotation_scale",
  "input_file": "drawing.dxf",
  "output_file": "drawing-scaled.dxf",
  "scale": 2,
  "layout": "Model",
  "include_dimensions": true,
  "include_leaders": true
}
```

写入后 SimLoom 会重新打开目标 DXF 和 sidecar，检查实体数量、稳定实体 ID、实体类型、图层
以及 `$DIMSCALE` 是否与内存对象一致。返回值中的 `results[0]` 包含 `previous_scale`、
`factor`、受影响实体和 `changed`。

这是本地二维图纸辅助操作，不代表 CATIA、Ansys、Adams 或 VI-grade 内部标注对象已接入。
