# 复杂曲线拓扑验证

SimLoom 的独立 CAD 验证器现在会对统一对象和原生 DXF 分别检查曲线自交和重叠。它覆盖：

- 直线型 `LWPOLYLINE` / `POLYLINE` 的非相邻段；
- 带 bulge 的多段线弧段；
- 有效的 DXF `SPLINE`，使用有限采样检查非相邻曲线段；
- HATCH 多段线边界。

相邻段共享端点是正常拓扑，不会被报告。曲线会使用有界采样，因此这是独立的保守检查，
不是厂商 B-Rep 或精确曲线求交内核。

## 统一对象验证

```python
from engineering_agent import CadDocument, validate_cad_document

document = CadDocument.from_dxf({
    "layers": {"0": {}},
    "entities": [{
        "id": "outline",
        "type": "LWPOLYLINE",
        "layer": "0",
        "vertices": [
            {"x": 0, "y": 0}, {"x": 10, "y": 10},
            {"x": 0, "y": 10}, {"x": 10, "y": 0}
        ]
    }]
})

report = validate_cad_document(document)
# 默认保留工程文件，但报告 CURVE_SELF_CROSSING 警告。
strict = validate_cad_document(document, {"reject_self_intersections": True})
# strict["valid"] 为 False。
```

报告的 `curve_topology` 保存实体 ID、曲线段索引和 `crossing` / `overlap` 类型。默认状态
会把发现放进 `warnings`，适合检查已有供应商图纸；设置 `reject_self_intersections` 或
`reject_curve_topology` 后，发现会进入 `errors`，从而阻止事务提交。

## 原生 DXF 验证

`dxf_validation.inspect_native()` 在独立重新读取 DXF 后执行相同检查：

```python
report = inspect_native("drawing.dxf")
strict = inspect_native("drawing.dxf", reject_curve_topology=True)
```

原生审计不会写回 DXF。`curve_topology` 和 `NATIVE_CURVE_SELF_CROSSING` /
`NATIVE_CURVE_SELF_OVERLAP` 会留下可追溯证据；严格模式下才会使 `valid` 变为 `False`。

## 边界

采样可以发现明显的自交和重叠，但不能证明任意高阶 NURBS 没有极小尺度交点，也不能替代
CATIA 或其他商业软件对特征、装配拓扑和制造语义的验收。需要精确求交时，应在 Windows
worker 中调用真实软件，并将其原生结果交给 SimLoom 的独立验证器复核。

```bash
.venv/bin/python -m pytest tests/test_cad_validation.py tests/test_native_dxf.py -q
```
