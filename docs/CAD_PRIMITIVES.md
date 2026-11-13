# CAD 基础闭合图元

`cad.rectangle_create` 和 `cad.polygon_create` 生成可编辑的原生 `LWPOLYLINE`，并通过与
其他 CAD 写操作相同的事务、验证、diff、sidecar、历史和回读流程保存。

矩形使用 `origin`、`width`、`height` 和可选的 `rotation`（角度，单位为度）。正则多边形使用
`center`、`radius`、`sides` 和可选的 `rotation`。矩形固定生成四个顶点，多边形要求至少三个
边。所有尺寸必须是有限的正数，退化输入会在写入前拒绝。

```json
{
  "operation": "cad.rectangle_create",
  "input_file": "source.dxf",
  "output_file": "plate.dxf",
  "origin": {"x": 10, "y": 20},
  "width": 100,
  "height": 50,
  "rotation": 15,
  "layer": "OUTLINE"
}
```

```json
{
  "operation": "cad.polygon_create",
  "input_file": "source.dxf",
  "output_file": "bolt-pattern.dxf",
  "center": {"x": 0, "y": 0},
  "radius": 25,
  "sides": 6,
  "rotation": 30
}
```

这些操作是本地图纸输入准备和预览能力，不能替代 CATIA 中的 Part Design 特征或其他厂商软件
的参数化实体；真实厂商模型仍通过 Windows worker 运行。
