# DXF 读写与独立校验

版本化的 ASCII / 二进制 DXF 使用 ezdxf 读写。编辑时加载原图纸，按 handle
更新实体。未编辑对象继续附着于原来的文档、图层表、块表和对象字典。
二进制输入当前保存为 ASCII DXF；保持原有 DXF 版本，不自动升级 R12。

## 实际能力

- 外部 handle 保持稳定；新建实体的应用 ID 写入 `SIMLOOM_AI` XDATA。
  其他应用的 XDATA 和扩展字典不会因编辑相邻实体而被重新构建。
- 支持带宽度、凸度的多段线，真实 DIMENSION 定义点与匿名图形块，
  INSERT 附属属性，以及多个填充边界、孔洞和线/圆弧/椭圆/样条边界。
- 移动、旋转和缩放使用原生几何变换，处理 OCS 坐标和附属属性。
  图层冻结状态、ACI 颜色、自定义线型和字体样式在未修改时保留。
- 图纸布局和视口直接存在于 DXF；删除视口、删除图纸和清理未使用的普通块
  会改变原生文件。清理保留嵌套块依赖、块内图层及内部特殊块。
- `.cad.json` 继续保存选择集、约束、项目元数据等应用内容。
  文件带 SHA-256；外部修改导致哈希不匹配时忽略过期侧车文件并报告
  `CAD_SIDECAR_STALE`，不会用旧数组位置覆盖 DXF 实体 ID。

P&ID 路线、过程线、阀门和连接段的语义保存在实体扩展元数据中：`process_line`、
`process_route`、`pid_valve`、`pid_connector`。这些字段会在 native DXF 写入时进入
SimLoom 的实体配对数据，重新读取时恢复到统一对象模型；原生 CAD 软件若重建实体或清除
XDATA，SimLoom 会把它视为外部变更并要求重新审计，不能假设语义仍然存在。
符号端口保存在 `pid_ports` 中，包含名称、方向、点位和可选方向向量；`cad.pid_audit` 会
把端口展开为独立结果，并验证连接段上的 `valve_id` / `port` 引用。

## 提交前后的检查

`CadDocument` 校验负责应用对象和工程要求。提交后的 `inspect_native` 直接
重新读取磁盘 DXF，不借助侧车文件恢复几何。它核对实体标识、主要几何属性、
填充边界、块属性、标注测量、纸空间归属、图纸尺寸与视口，并运行原生结构审计。

审计过程中发现的错误和需要修复的项目都会返回失败；审计不会保存自动修复后的
文件。数值标注覆写与真实测量不一致时报告 `DIMENSION_TEXT_MISMATCH`。
文字覆写的核对考虑线性比例和显示精度，但尚不包含全部公差、替代单位与 GD&T 语义。

任一落盘检查失败，事务恢复 DXF、侧车文件和历史游标。自动测试故意修改写出文件的
端点坐标，确认即使实体数量、类型和 ID 全部正确，几何丢失仍会被拦截并恢复。

## 测试证据与边界

`tests/test_native_dxf.py` 用 ezdxf 独立构造图纸，不调用 SimLoom 的生成器作为
复杂图纸来源。测试覆盖未编辑内容保留、块属性移动、带孔填充、圆弧边界、
自定义填充相位、标注定义和图形、原生审计、失效侧车、嵌套块、布局删除、
二进制输入、R12 多段线及英寸图纸。浏览器测试另行验证真实编辑流程。

```bash
python -m pytest tests/test_native_dxf.py tests/test_cad_editing.py tests/test_cad_transactions.py -q
```

这些是生成的兼容性 fixture，尚不是客户真实图纸或商业 CAD 实机验收。
SPLINE 已支持创建、原生读回、几何变换和方向反转；反转会同步处理拟合点、控制点、权重以及节点向量，控制点、拟合点、节点向量和权重会进入独立原生回读检查。代理对象等仍只保留未编辑内容，不表示可执行任意变换。
关联填充在 `cad.entity_move`、`cad.entity_transform`、`cad.entity_rotate`、`cad.entity_scale` 和 `cad.entity_mirror` 中会自动扩展到其 source boundary entities，保留 `associative` 标记和 source handles；直接改变关联边界结构、复制关联填充、特殊块删除和不支持的多段线结构修改会拒绝执行。
R12 不支持的新实体类型也会拒绝，避免隐式转换原文件版本。

早期 SimLoom 无版本头且含不透明记录的文件仍采用兼容写入路径，原样保留未知
记录的 group 顺序，并报告 `LEGACY_DXF_NATIVE_AUDIT_UNAVAILABLE`。
此路径不提供原生结构有效性保证；完整 DXF 读取失败不会回退到这个路径。

标题栏现在使用原生块与 ATTDEF/ATTRIB 字段，可在不携带侧车文件的新项目中
读取和修改。工程图 PDF/PNG 出图及实际比例验证见 [DRAWING_EXPORT.md](DRAWING_EXPORT.md)。
模板尚未通过企业制图标准验收。复杂几何约束、客户图纸回归集、Windows 商业软件
互操作与 MCP 协议完整性仍待完善。这些改动不等于与 AgentCAD 全面等价。
