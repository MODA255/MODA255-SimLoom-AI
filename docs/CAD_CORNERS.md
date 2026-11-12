# 圆角、倒角的几何契约

`cad.entity_fillet` 与 `cad.entity_chamfer` 支持同一模型空间或纸空间中的两条
相连 LINE。两条线必须处于同一个平行于世界 XY 的平面；允许非零共同 Z 高度。
两条线的实体 ID、保留端点和样式不变，靠近交点的端点被修剪，并新增连接实体。

## 圆角

```python
result = agent.execute_operation("cad.entity_fillet", {
    "project_dir": "/path/to/project",
    "input_file": "part.dxf",
    "output_file": "part.dxf",
    "entity_ids": ["2F", "30"],
    "radius": 2.0,
    "tolerance": 1e-6,
})
```

`radius` 表示绘图单位下的实际圆弧半径，必须为有限正数。兼容旧参数 `distance`，
其含义同样是半径；同时提供两个参数时必须相等。MCP 的 1.1 operation schema
公开这两个参数和“至少提供其中之一”的约束。

对于两条保留线段形成的内部夹角 θ，圆角到交点的切线距离为 `r / tan(θ/2)`。
因此半径不等于端点修剪长度，钝角处的有效半径可能大于原线段长度。
实际切点必须落在线段内部，并留出大于 `tolerance` 的剩余长度。
圆弧采用内部短弧，扫角为 `180° - θ`，与选择顺序和 LINE 起终点方向无关。

## 倒角

```python
agent.execute_operation("cad.entity_chamfer", {
    "project_dir": "/path/to/project",
    "input_file": "part.dxf",
    "output_file": "part.dxf",
    "entity_ids": ["2F", "30"],
    "distance": 2.0,
})
```

`distance` 是从原交点沿两条直线各自量取的相等退让距离，不是连接线长度。
连接实体为真实 LINE。当前不支持两个不同退让距离或按角度定义倒角。

## 独立检查与事务

构造之后先检查端点连续性、圆角半径、短弧方向和相切性，或倒角的实际退让距离。
检查器不调用圆角构造函数。保存后再直接读取 DXF 的原生 LINE/ARC，执行同样的
几何性质检查，不依赖 `.cad.json` 中的几何或生成器报告。

- `results[*].geometry_validation`：该步构造完成时的独立检查及数值误差。
- `results[*].corner_check`：可复用的检查描述，包括两条线、连接实体和目标尺寸。
- `persisted_validation.native.corner_checks`：最终实际文件的检查结果。
- 检查失败：事务恢复之前的 DXF、侧车元数据和历史游标。

同一批次稍后可以有意移动、修剪或删除这些实体。若三者中有实体被修改，该步
的自动创建后置条件会标为 `superseded_by_later_edit`，不再假称最终文件仍满足
原连接关系。创建当时的检查仍执行；这些检查不会自动变成永久几何约束。

需要在最终文件强制保持连接时，将检查描述放入明确的工程要求：

```python
check = result["results"][0]["corner_check"]
audit = agent.execute_operation("cad.audit", {
    "project_dir": "/path/to/project", "input_file": "part.dxf",
    "requirements": {"corner_connections": [check]},
})
```

同样的 `requirements` 可传给 CAD 写操作或批次，在保存读回时强制检查。
审计返回有效性和误差，不修改图纸；写操作检查失败会回滚。

## 明确拒绝的输入

少选、多选、重复选择、非 LINE、未连接或共线的线、跨布局、非 XY 平面、
非零厚度、锁定图层、零长度线、过大尺寸、非正或非有限尺寸/容差均拒绝。
没有静默缩短单条线来冒充圆角或倒角的兼容路径。未连接的延长线圆角、圆弧与
圆弧圆角、三维圆角仍未实现。非常接近退化或超出数值精度的构造可以被独立检查拒绝。

## 回归证据

```bash
python -m pytest tests/test_cad_corners.py -q
```

可运行 `python examples/cad_corners.py --output /path/to/new-folder` 生成六组
真实圆角 DXF、独立检查报告，以及 PDF/PNG 预览；输出目录必须尚不存在。

测试直接用 ezdxf 创建输入并读取实际输出，覆盖锐角/直角/钝角、选择顺序、
四种起终点方向、纸空间与非零高度、近重合端点、非法参数、相切/间隙/扫角/
半径/退让距离、构造错误注入、保存错误注入以及文件和历史一起回滚、撤销和重做。
这些是确定性图纸的功能证据，不代表全部客户图纸或完整 CAD 内核已通过验收。
