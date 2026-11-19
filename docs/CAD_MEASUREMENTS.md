# CAD 几何测量

`cad.measure_entities` 是 SimLoom 的只读几何测量 operation。它把图纸中的可测实体转换为带来源引用的长度、周长、面积和圆弧信息，供 Agent 在修改后检查工程指标，也可在交给 CATIA 或其他 Windows 后端前做输入审计。

```python
result = agent.execute_operation("cad.measure_entities", {
    "project_dir": "./project",
    "input_file": "./project/plate.dxf",
    "layer": "PROFILE",
})
```

返回值使用 `simloom-ai/cad-measurements/v1`。每个 `measurements` 条目都包含稳定实体 `id`、原生 `handle`、类型、图层、`exact` 和计算方法。`summary` 汇总选中数量、成功测量数量、无法支持的数量和总长度、周长、面积。源 DXF、sidecar、事务历史和 Viewer 状态都不会被修改。

当前支持以下精确测量：

- `LINE` 返回三维欧氏长度。
- `CIRCLE` 返回半径、面积和周长。
- `ARC` 返回半径、圆心角和弧长。DXF 的 ARC 按逆时针方向读取。
- `LWPOLYLINE` 和 `POLYLINE` 返回开放路径周长，闭合路径还返回面积。直线段和 bulge 圆弧段使用解析公式计算。
- 简单 `HATCH` 边界返回解析面积和周长。复杂厂商边界或未支持的实体会保留逐实体错误证据。

默认情况下，未支持的实体不会让整次只读测量失败，而是写入 `warnings`，并在条目中标记 `supported: false`。需要把所有目标都限定在支持集合内时传入 `strict: true`，此时会返回 `UNSUPPORTED_MEASUREMENT:<entity_id>`。

`target`、`entity_ids`、`selection_set`、`entity_type`、`layer`、`window` 和 `filter` 使用与 `cad.query` 相同的选择语义。通过 `target` 传入的实体引用可以直接来自上一次 `cad.query` 的 `matched_ids`，因此 Agent 不需要依赖 DXF 数组顺序。

测量结果是独立的几何证据。它不能从退出码推断 CATIA、Ansys、Adams 或 VI-grade 的原生结果，也不会替代真实软件后端导出的工程指标。四款软件的结果仍需通过对应 runner 的 `EngineeringResult/v1` 和 Windows 实机证据验收。
