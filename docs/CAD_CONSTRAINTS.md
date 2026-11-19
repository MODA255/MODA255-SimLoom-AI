# CAD 几何约束

SimLoom 现在提供 AgentCAD 风格的约束检查和顺序施加入口。

```python
checked = agent.execute_operation("cad.constraints_check", {
    "project_dir": "./project",
    "input_file": "./project/plate.dxf",
    "constraints": [
        {"type": "horizontal", "a": "e00001"},
        {"type": "coincident", "a": "e00001@end", "b": "e00002@start"},
    ],
})
```

约束点引用使用 `<entity-selector>@<point>`。支持 `start`、`end`、`mid`、`center`、`insert`、`location` 和多段线顶点 `v<N>`。实体选择器接受稳定 ID、原生 handle 和 `tag:<value>`。

`cad.constraints_check` 返回每条约束的 `expected`、`actual`、`error`、`tolerance`、`satisfied` 和实体引用。支持 `coincident`、`parallel`、`perpendicular`、`horizontal`、`vertical`、`tangent`、`concentric`、`distance`、`length`、`angle`、`equal`、`radius`、`diameter` 和 `fixed`。无法支持的对象会保留结构化错误，不会把检查结果猜成通过。

`cad.constraints_enforce` 在一个写事务中按声明顺序直接修改第二个操作数或单个实体，然后重新检查全部约束。当前可施加 `coincident`、`parallel`、`perpendicular`、`horizontal`、`vertical`、`concentric`、`distance`、`length`、`equal`、`radius`、`diameter` 和 `fixed`。如果后续约束破坏了前面的约束，事务返回 `CONSTRAINT_CONFLICT` 并回滚。这个机制是有界的顺序修改，不是同时求解器。

compact MCP 的 `apply_constraints` 通过 `mode=check|enforce` 复用这两条路径。它和 `apply_operations` 共享 CAD context、error envelope、事务和 profile policy。
