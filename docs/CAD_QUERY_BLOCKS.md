# `cad.query` 块实例查询契约

`cad.query` 是 SimLoom-AI 的只读 CAD 查询操作。它读取统一
`CadDocument`，返回稳定的 `EntityRef`，并可以在不启动商业 CAD 软件的情况下解析
DXF 中的 `INSERT`、块定义、属性和嵌套块几何。这个能力服务于 Agent 的定位、选择、
输入准备、审计和回放；它不替代 CATIA 或其它厂商软件对原生对象的最终解释。

## 何时启用块几何

普通查询只把 `INSERT.insert` 当作实体位置，因此速度更快，也不会展开块定义。需要按
实际块外形做空间过滤，或需要让 Agent 看到块的解析结果时，传入：

```json
{
  "operation": "cad.query",
  "input_file": "drawing.dxf",
  "entity_type": "INSERT",
  "block_geometry": true,
  "window": [{"x": 90, "y": 40}, {"x": 120, "y": 80}],
  "include_geometry": false
}
```

`block_geometry` 只影响 `INSERT`。对其它实体，空间窗口仍按该实体自身的语义点计算。
`include_geometry=false` 会去掉实体的详细线、圆和属性字段，但保留 `block_instance`，
这样列表页可以先拿到轻量元数据，再用稳定 `ref` 请求详细实体。

## 块和属性筛选

块名可以直接传入，也可以放在 AgentCAD 兼容的 `filter` 对象中：

```json
{
  "input_file": "drawing.dxf",
  "filter": {
    "type": "INSERT",
    "block_name": "LABEL",
    "attribute_properties": {"NAME": "Bracket"}
  },
  "attribute_text_contains": "brack",
  "case_sensitive": false,
  "block_geometry": true
}
```

筛选规则如下：

- `block_name` 对 `INSERT.block_name` 做精确匹配。
- `attribute_properties` 对属性标签到文本值做精确匹配；一个属性缺失或文本不同，
  该 `INSERT` 就不会命中。
- `attribute_text_contains` 在属性标签和值拼接成的文本中做子串匹配，大小写由
  `case_sensitive` 控制。
- `text_contains` 仍然匹配普通文字；对 `INSERT` 也会把属性标签和值纳入搜索。
- 这些筛选只作用于顶层图纸中的实体。块定义内部的 LINE、CIRCLE 等几何不会作为
  独立查询实体返回；它们通过 `block_instance` 的空间范围和路径反映出来。

## `block_instance` 返回值

启用 `block_geometry` 后，每个匹配的 `INSERT` 都会获得以下字段：

```json
{
  "block_instance": {
    "resolved": true,
    "block_path": ["ASSEMBLY", "LEAF"],
    "block_paths": [["ASSEMBLY"], ["ASSEMBLY", "LEAF"]],
    "bounds": [10.0, 10.0, 20.0, 16.0],
    "warnings": []
  }
}
```

- `resolved` 表示顶层块定义是否存在。
- `bounds` 是 `[min_x, min_y, max_x, max_y]`，位于图纸世界 XY 坐标中。它由块中
  所有可识别语义点计算，曲线使用保守的语义包围范围；它不是渲染器生成的精确曲线
  tessellation。
- `block_paths` 是所有包含实际几何的块路径，按字典序稳定排序并去重。路径的第一项
  是顶层块名，后续项是嵌套 `INSERT` 引用的块名。
- `block_path` 是最深的确定性路径，用来给列表和 Agent 一个单一代表路径。一个块没有
  嵌套时，它就是 `["BLOCK_NAME"]`；一个块包含多个分支时，应使用 `block_paths` 获取
  完整信息。
- `warnings` 是可审计的非致命问题列表。查询仍会返回顶层 `INSERT`，不会因为某个
  分支无法展开而伪造几何范围。

块实例的变换顺序与 DXF 语义一致：先减去块定义的 `base_point`，再应用 X/Y 缩放，
再绕 Z 轴旋转，最后平移到 `INSERT.insert`。嵌套块把子块变换与父块变换按矩阵顺序
组合，因此旋转、非等比缩放和不同的基点都会影响最终窗口查询。

## 警告和边界

目前定义的 warning code 为：

| Code | 含义 | 结果行为 |
| --- | --- | --- |
| `BLOCK_NOT_FOUND:<name>` | 块定义不存在，或嵌套引用断开 | `resolved` 可能为 `false`；没有可计算的范围时 `bounds` 为 `null` |
| `BLOCK_RECURSIVE:<path>` | 块定义直接或间接引用自身 | 停止该递归分支，保留其它已经解析的几何 |
| `BLOCK_NESTING_LIMIT:<path>` | 嵌套深度超过 16 层 | 停止超深分支，避免异常文件消耗无限时间 |

这些 warning 会同时出现在实体的 `block_instance.warnings` 和查询级别的
`warnings` 数组中。查询级别数组会去重并排序，适合日志和自动化判断。warning 不等于
商业 CAD 的 AUDIT 结果；如果工程要求块定义完整，应把 `warnings` 作为独立验证条件
处理，而不是忽略它们。

## 分页和稳定引用

`offset` 与 `limit` 先作用于完整匹配集，再截取 `entities`。`total_count` 表示截取前
的数量，`matched_ids` 也表示完整匹配顺序。每个返回实体都有：

```json
{"ref": {"document_id": "cad-...", "entity_id": "e00001"}}
```

后续查询可以把这个 `ref` 放入 `entity_ids`，避免依赖列表顺序。`block_instance` 是
附加只读元数据，不改变原始 DXF，也不会创建新的实体 ID。

## 验证和测试

查询操作会经过统一的读取、路径约束和 CAD 独立验证链。块几何解析本身是纯函数，
不调用 CAD COM，也不依赖 Viewer。当前回归覆盖：

- 块名、ATT/ATTRIB 属性精确筛选和文本子串筛选；
- `include_geometry=false` 下的轻量返回；
- 缩放、旋转、块基点和窗口相交；
- 多层嵌套块的所有路径和合并包围范围；
- 缺失块和递归块的非致命 warning；
- 分页、稳定 `EntityRef` 和 MCP operation discovery。

可运行：

```bash
.venv/bin/python -m pytest -q tests/test_cad_query.py
```

这套能力证明的是 SimLoom 统一对象模型对 DXF 块实例的可重复读取。它不能证明
CATIA 实例、外部引用、动态块、厂商专有对象或三维实体在 Windows 实机上的等价语义；
这些仍需要对应软件、版本、许可证和真实工程文件联调。
