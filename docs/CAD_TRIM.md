# 修剪边界契约

`cad.entity_trim` 对目标 LINE 支持点投影，以及有限 LINE、CIRCLE、ARC、ELLIPSE、LWPOLYLINE
或 POLYLINE 边界的真实交点。ELLIPSE 使用 DXF 的 major-axis/ratio 参数，支持旋转椭圆和
有限参数区间；带 bulge 的多段线段按真实圆弧求交；SPLINE 边界通过 B-spline 参数求交并在
有限容差内细化。交点必须落在目标线段和切割边界的有限范围内；不会把两条无限
支撑线的交点误认为图纸上的修剪位置。

对 polyline 边界，直线段或 bulge 圆弧段逐段求交并按保留端点选择最近交点；无效 bulge、没有
交点、退化边界和不支持的实体类型会失败并回滚整个事务。

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
任意曲线延伸和厂商专有修剪语义仍不会被伪装成已支持能力；SPLINE 的自交、极端退化和需要
外推的情况会明确失败并回滚。
