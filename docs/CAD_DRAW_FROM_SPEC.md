# 从规格创建图纸

`cad.draw_from_spec` 和 compact MCP 的 `draw_from_spec` 可以从结构化规格创建一张可继续编辑的 DXF。当前内置 `plate` 规格，适合验证 Agent 从工程意图到图纸产物的完整路径。

```python
result = agent.execute_operation("cad.draw_from_spec", {
    "project_dir": "./project",
    "output_file": "plate.dxf",
    "spec": {
        "kind": "plate",
        "units": "mm",
        "width": 100,
        "height": 60,
        "holes": [{"pattern": "corners", "diameter": 8, "edge_offset": 10}],
    },
})
```

规格会创建一个带 `outline` 标签的闭合 LWPOLYLINE。`holes` 支持 `pattern: corners`，按边缘偏移生成四个带 `hole.<index>.<corner>` 标签的 CIRCLE，也支持显式 `center` 的单孔。输出保留 `simloom-ai/drawing-spec/v1` 元数据，记录原始规格和展开后的孔数量。

写入流程经过事务锁、checkpoint、DXF 原生写入、sidecar、独立回读、验证、diff 和 CAD history。输出存在时必须显式传入 `overwrite: true`。后续可以直接使用 `inspect_entities`、`measure_entities`、`apply_operations`、`apply_constraints` 和 `verify_drawing`。

这是规格编译器，不是 CATIA 参数树或装配特征生成器。需要 CATIA、Ansys、Adams 或 VI-grade 原生对象时，仍通过四软件 operation contract 和 Windows worker 执行。
