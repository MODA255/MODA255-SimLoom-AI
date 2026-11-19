# 规格修订与验证

`cad.revise_drawing` 读取图纸 sidecar 保存的 `simloom-ai/drawing-spec/v1`，把变更合并到 plate 规格，再在同一份图纸中更新 outline 和带语义标签的孔阵列。

```python
result = agent.execute_operation("cad.revise_drawing", {
    "project_dir": "./project",
    "input_file": "plate.dxf",
    "output_file": "plate.dxf",
    "changes": {"width": 120, "height": 70},
})
```

轮廓和已有孔实体会按标签就地更新。实体 ID、原生 handle、sidecar、事务历史和 revision diff 会被保留。孔数量或孔阵列变化时，缺少的实体会创建，多余的 `hole.*` 实体会删除。输出提交前会回读 DXF，并重新检查规格中的尺寸、孔标签、孔中心和直径。

`cad.validate_spec` 是只读验收入口。它返回 `expected`、`actual`、逐项错误和 `valid`，适合 Agent 在修订后再次检查。没有规格元数据的普通 DXF 会返回 `DRAWING_SPEC_MISSING`，不会猜测图纸意图。

compact MCP 通过 `revise_drawing` 和 `validate_drawing` 暴露这两条路径。它们继续遵守活动 CAD context、项目路径约束、full/safe profile 和统一 error envelope。
