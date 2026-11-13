# 修剪边界契约

`cad.entity_trim` 对目标 LINE 支持点投影，以及有限 LINE、CIRCLE、ARC、LWPOLYLINE 或
POLYLINE 边界的真实交点。交点必须落在目标线段和切割边界的有限范围内；不会把两条无限
支撑线的交点误认为图纸上的修剪位置。

对 polyline 边界，直线段逐段求交并按保留端点选择最近交点。带 bulge 的多段线会明确返回
`TRIM_BULGED_BOUNDARY_UNSUPPORTED`，因为圆弧段需要曲线求交内核；没有交点、退化边界和
不支持的实体类型也会失败并回滚整个事务。

```python
result = agent.execute_operation("cad.entity_trim", {
    "project_dir": project,
    "input_file": "drawing.dxf",
    "output_file": "drawing.dxf",
    "entity_id": "e00001",
    "boundary_entity_id": "e00002",
    "keep": "start",
})
```

写入后会重新读取实际 DXF，执行统一几何验证、diff、checkpoint、历史和恢复。当前仍未把
复杂 spline/bulge 边界、任意曲线延伸和厂商专有修剪语义伪装成已支持能力。
