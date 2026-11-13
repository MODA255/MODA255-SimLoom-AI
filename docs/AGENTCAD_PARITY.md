# AgentCAD 能力对齐与验收清单

目标仍是达到 AgentCAD 的实际能力。完成一组功能、通过若干测试或操作数量接近，
都不能作为整体完成的证据。本清单保留未实现、未验证和需要实机的项。

## 对齐基准

2026-11-11 通过 GitHub API 核对 `lizaixi01/AgentCAD` 的 `main`，当前提交为
`e9f2e904eb21a2171ad41ffe35cae676f8aaf05b`（提交时间 2026-11-09 09:22:02 UTC）。
验收依据是该提交的 README 及其链接的 operations、engineering-workflows、
viewer、architecture、limitations-roadmap 文档与实现。后续对齐应重新检查上游变化。
2026-11-12 再次核对远程 `main`，仍为上述提交；本轮也重新读取了 viewer 与 architecture 文档。

上游当前重点是二维 CAD；其历史三维测试分数不能作为本项目的对齐门槛或完成证据。
SimLoom 原有的 CATIA / Ansys / Adams / VI-grade 工程链、B-Rep 和仿真查看功能继续保留，
真实后端验证仍须单独完成。

`cad.draw_order` 现已补齐：它通过 DXF `SORTENTS` 持久化置前、置后、前移、后移和相对定位，沿用事务、锁层、独立读回、diff、历史和 rollback；细节见 `CAD_DRAW_ORDER.md`。这缩小了二维 typed operation 差距，但不改变四款商业软件实机验证仍待 Windows 的事实。

`cad.edit_hatch` 现已补齐：可在事务中修改已有 HATCH 的图层、实/空填充、图案、角度、比例、样式和多段边界，并拒绝破坏关联 HATCH 的边界语义，细节见 `CAD_EDIT_HATCH.md`。

`cad.create_text_style` 现已补齐：统一对象模型保存可复用的 DXF `STYLE` 定义，并在原生文件读回时验证字体、字高和宽度因子，细节见 `CAD_TEXT_STYLES.md`。

`cad.block_from_entities` 现已补齐：可把选定实体转换为相对基点的原生块定义，并按请求保留原件或创建 INSERT，细节见 `CAD_BLOCK_FROM_ENTITIES.md`。

`cad.set_annotation_scale` 现已补齐：统一对象模型保存标注比例，原生 DXF 写入 `$DIMSCALE`，
按比例调整文字注释并保留尺寸、引线、GD&T、表面粗糙度和 BOM 的标注元数据；模型几何和实际
测量值不改变，细节见 `CAD_ANNOTATION_SCALE.md`。

`cad.rectangle_create` 和 `cad.polygon_create` 现已补齐：生成带旋转、尺寸校验和原生回读的
闭合 LWPOLYLINE，细节见 `CAD_PRIMITIVES.md`。

`cad.donut_create` 现已补齐：生成带稳定关联元数据的同心内外圆，支持原生回读和继续编辑，
细节见 `CAD_DONUT.md`。

`cad.ray_create` 和 `cad.xline_create` 现已补齐：写入原生 DXF 构造线并对方向归一化、回读和
零向量错误做验证，细节见 `CAD_CONSTRUCTION_LINES.md`。

`cad.change_space` 现已补齐：可把选定实体事务化移动到模型空间或已有纸空间，并在原生 DXF
回读时验证 layout 归属，细节见 `CAD_CHANGE_SPACE.md`。

`cad.set_page_setup` 现已补齐：为纸空间写入原生纸张尺寸、方向、边距、偏移、比例和输出设备，
并在回读时验证 layout 设置，细节见 `CAD_PAGE_SETUP.md`。

`cad.block_attribute_create` 现已补齐：为块定义写入原生 ATTDEF，并同步现有 INSERT 的 ATTRIB、
插入点变换和标签唯一性，细节见 `CAD_BLOCK_ATTRIBUTES.md`。

`cad.hole_create` 现已补齐：创建可继续编辑的通孔、螺纹孔、沉头孔、锥沉孔和端面沉孔轮廓，
以原生同心 CIRCLE 保存几何，并记录孔组、孔型、尺寸、深度和标准标签，细节见 `CAD_HOLES.md`。

`cad.trace_boundary` 现已补齐：从按容差连接的 LINE 和直线型 LWPOLYLINE/POLYLINE 中寻找闭合
回路，生成带源实体追溯信息的闭合 LWPOLYLINE；bulge 曲线和找不到闭环的输入会明确失败，
细节见 `CAD_TRACE_BOUNDARY.md`。

## 必须逐项证明的能力

| 能力 | 本地证据及现状 | 完成所需证据 / 下一步 |
| --- | --- | --- |
| 真实二维绘图、编辑与 typed operations | 已有统一对象、原生 DXF 保存与多类编辑；`cad_operations.py`、CAD 回归测试；直线/圆弧/圆和直线多段线 offset 现在支持有符号左右侧、连接拐角和明确拒绝 bulge 曲线；SPLINE 已支持创建、读回、变换和作为有限 trim 边界；关联 HATCH 移动会自动扩展到 source boundary entities；`cad.entity_trim` 现在支持有限 LINE/CIRCLE/ARC/ELLIPSE/SPLINE/polyline 边界和 bulge 圆弧段；`cad.entity_extend` 现在支持 LINE/CIRCLE/ARC/ELLIPSE/SPLINE/polyline 有限边界、start/end/auto、最近交点、布局和锁层检查；`cad.center_mark_create` 现在为 CIRCLE/ARC 生成带标签、幂等策略和布局检查的可编辑中心标记；`cad.entity_remove_duplicates` 现在按容差清理同层同布局重复几何并重写选择集、约束和可用的关联 HATCH 引用；`cad.entity_stretch` 现在支持 crossing window、部分端点/顶点移动、完整实体移动、布局过滤和尺寸重测；两相连线的圆角/等距倒角已补真实切点、短弧与独立检查，见 `CAD_CORNERS.md`、`CAD_TRIM.md`、`CAD_EXTEND.md`、`CAD_CENTER_MARK.md`、`CAD_DUPLICATES.md`、`CAD_STRETCH.md` | 对照上游每种操作的几何语义逐项验收，不能仅比较操作名称。SPLINE 自交、极端退化、关联边界重构和其他退化输入仍需继续复核；复杂曲线和厂商实体仍需真实图纸验收 |
| 原子批次、checkpoint、diff、跨会话恢复 | `cad_history.py`、`transactions.py`；已有真实子进程中断和恢复测试 | 扩充外部编辑冲突及各写入口的覆盖；核对脚本等新增入口遵守同一契约 |
| STEP B-Rep 写入可靠性 | `brep.boolean`、`brep.create_primitive`、`brep.build_feature_graph` 现在把输入和输出限制在项目目录，写入后先检查暂存文件，再从提交目标重新读取 STEP，验证形状、拓扑和体积；失败由事务恢复原文件；feature graph sidecar 随输出一起提交并在覆盖时清理 | 仍需在更多 OpenCascade 版本和真实复杂 STEP 数据上验收；布尔退化、装配、多实体和外部文件冲突的工程规则还需扩展 |
| 受控脚本执行 | 已有 `cad.script_apply`：默认关闭；显式启用后在事务 staging 工作目录运行，静态检查高风险导入/调用，限制脚本时长、输出大小和输出文件，重新读取 DXF 做独立验证、diff、checkpoint、历史和失败恢复；`tests/test_controlled_cad_scripts.py` | 在 Windows worker 上把同一策略映射到受控脚本宿主，并补 OS 级隔离和真实项目脚本验收；当前策略不是完整安全沙箱 |
| 独立工程验证 | `validate_cad_document` 和独立读取 DXF 的 `dxf_validation.inspect_native` 现在分别检查打印文字高度、闭合轮廓退化、重复几何、尺寸数值/引用和可配置尺寸关联；原生结构审计、几何读回、标注测量仍有测试 | 继续补齐字体实际字形覆盖、复杂拓扑、尺寸与实体的标准级关联规则，以及更完整的可修复错误分类 |
| 原生几何交互视图 | `cad_render.py`、`cad_viewer.py`、`test_cad_viewer.py`、真实浏览器几何检查；Viewer 已支持选择、图层、框选、缩放、平移、SVG 路径对象捕捉和 LINE/LWPOLYLINE/CIRCLE/ARC/ELLIPSE/SPLINE 的语义夹点编辑；夹点松开后复用 `cad.entity_update` 事务、验证、diff、历史和 rollback | 原生模型视图这一子项已有实现；客户图纸、复杂字体、性能、捕捉精度和真实浏览器夹点操作仍待验收 |
| 历史版本查看、视觉 diff、实时查看 agent 修改 | 有持久撤销/重做、字段 diff、SVG 叠加和项目时间线。`cad.history_view` 校验并只读渲染事务保存的 DXF/sidecar，支持前后步和播放/暂停。项目版本 schema 2 在同一事务中保存原始文件与配套元数据，支持历史原件下载、真实文件预览和向新目录导出。`project.watch_*` 现在可持久保存项目内文件指纹，跨进程轮询并识别 added/modified/touched/deleted/unstable，Web 工作台也可直接开始、轮询和停止；`project.dependency_scan` 可从显式清单、可读文本引用和配套文件生成保守依赖图；见 `PROJECT_VERSION_FILES.md`、`FILE_WATCH.md` 与 `DEPENDENCY_SCAN.md` | 依赖扫描仍不能解析专有二进制装配拓扑，也没有更大工程的回放验收；批次按整次提交回放，子步骤没有独立几何快照。文件监测和依赖图只提供可审计证据，不等于厂商软件打开、求解成功或工程语义验收 |
| 工程图排版和文件交付 | 原生标题栏、纸空间/视口、多页 PDF、单页 SVG/PNG、每页比例和尺寸校验；`DRAWING_EXPORT.md` | 标准比例、中文/SHX 字体和打印可读性仍需补齐 |
| Agent 能直接看图与检查源图细节 | Web/本地 viewer 已有 SVG/PNG；MCP `engineering_asset_view`、`engineering://asset`、`engineering_inspect_image` 和 `engineering_measure_image` 可返回结构化数据、局部 PNG 预览、颜色区域的源像素边界/质心、overlay，以及可选的源像素到工程图纸坐标映射（见 `IMAGE_INSPECTION.md`） | 继续补通用安全图像脚本、尺寸证据标注和客户端互操作验收 |
| 行业图形库 | 已有符号/紧固件/表格等基础生成操作；新增 `cad.standard_catalog`、`cad.standard_library`、`cad.standard_parts_audit`，覆盖 P&ID 符号、14 类 GD&T 特征、M3～M24 紧固件、常用轴承系列和孔型查询，严格模式会拒绝未知输入 | 当前目录是可查询的规划与输入校验契约，不能视为 ISO P&ID、GD&T、标准紧固件/轴承/孔系尺寸库的合规实现；仍需有来源的尺寸表、符号几何和原生软件验收 |
| 工程工作流 | 四软件离线链和基本 CAD 批次可运行；`cad.pid_audit` 可把 tagged P&ID 符号、端口和过程线读回为节点/边并检查孤立符号、连接段引用和要求连接；`cad.process_route_create` 支持正交路线，`cad.pid_valve_cut_pipe` 可在带语义管线上切断并插入带 inlet/outlet 关系的阀门 | 继续补有来源的 P&ID 标准符号几何、端口方向/规格约束、房间面积、回转件视图/BOM 等端到端工程任务；当前阀门操作仍是 vendor-neutral 关系模型，不是厂商 P&ID 对象 |
| DWG、AutoCAD AUDIT 与实时同步 | 尚无等价实机验收 | 实现可选 AutoCAD 后端、版本/文件锁/冲突/回读契约，并在 Windows 真机验证 |
| MCP、CLI、Python、HTTP 的一致性与策略 | 有统一 operation 调用、profiles、分页、schema/error envelope；MCP 可读取资产 JSON 和 SVG image content；写操作后发送资源更新。safe profile 已在实际调用入口拒绝写/执行操作及会保存产物的 run/generate/version 内置入口，并有绕过 discovery 的直接调用回归 | 继续补完整 schema/envelope 覆盖、allow-list 策略、跨客户端互操作和断线重连验证 |
| 安装、发布与可重复环境 | 已有启动器和 CI 配置 | 干净 Windows/macOS/Linux 的实际安装、升级和发布产物验证；配置存在不代表通过 |
| 对照验收与真实项目数据 | 当前主要为生成 fixture 和本地功能测试 | 用固定任务、相同输入、明确成功标准与参考系统对照；记录失败和不支持项，不借用上游自评分 |
| 四款商业工程软件后端 | 脚本、桥接与离线验证链已有实现；本机无对应真实软件 | Windows 上 CATIA/Ansys/Adams/VI-grade 的建模、求解、结果回读、错误恢复逐项实测 |

## 按产品定位调整后的优先顺序（2026-11-11）

用户明确：主要操作 CATIA、Ansys、Adams、VI-grade，而非替代这些软件。
AgentCAD 对齐用于衡量 AI 操作、反馈、验证、追溯的可靠性，不再以扩展本地 CAD 内核为主线。
SimLoom 的产品主线仍是操作 CATIA、Ansys、Adams、VI-grade；AgentCAD 的二维能力只作为
交互和可靠性参考，不能把本地 CAD 能力的增加误认为四款软件已经接入。
上表保留已有能力和未完成项，但不表示所有二维 CAD 功能具有相同开发优先级。

1. 修通软件动作、脚本、输入传递、结果回收与明确的失败状态。
2. 建立逐软件连接配置与实机证据；一款软件可独立接入，远程控制端无需 Windows。
3. 在软件内完成最小任务，读回原生模型和结果，检查工程指标。
4. 将真实结果和失败信息提供给 AI，支持有界重规划、重试与人工查看。
5. 围绕真实任务补充版本比较、可视化与工程交付；本地几何编辑按实际辅助需求推进。

本轮修正了 adapter / worker 协议、脚本绑定、文件传输、输出完整性与事务发布。
受控程序测试不能代表商业软件验收，详见 `SOFTWARE_INTEGRATION.md`。
本地复杂 trim、关联填充和厂商级曲线 offset 仍有语义边界，不宣称达到 AgentCAD 整体能力。


2026-11-11 后续：新增远程持久任务、独立监督进程、幂等提交、按软件排队、状态和日志分页、
取消与占用核实、单步结果回收，以及 Web/CLI/MCP 入口。测试使用受控程序和实际进程退出；
不能据此认定 Windows 或四款商业软件已验收，也不等于整条计划可自动续跑。

2026-11-11 后续：多步骤软件计划现可保存输入、原计划、稳定请求编号和原始结果，
通过 Web/CLI/MCP 显式恢复；覆盖实际控制端退出与发布中断测试。
这推进了工程链恢复能力，不是四软件实机通过证据；见 `WORKFLOW_RECOVERY.md`。


2026-11-12 后续：增加了基于独立 vendor evidence 的有界修复和重规划。修复策略必须由工程师
在软件动作绑定中声明；系统按失败指标选择策略，创建新输出路径和子工程链，原结果保持可追溯。
这实现了执行后反馈闭环的基础，不代表系统能够自动判断任意物理设计的正确修改。

2026-11-12 后续：MCP 增加 `engineering_asset_view` 和 `engineering://asset` 资源。
Agent 可以同时读取结构化资产元数据和 SVG image content，支持图纸、CSV 曲线和网格/结果
预览；仍需补图像局部裁切、缩放坐标契约和客户端互操作验收。

2026-11-12 后续：增加只读 `engineering_inspect_image`。PNG/JPEG/WebP 可按源像素区域
裁切、放大、旋转，并通过 MCP 返回 PNG image content；源文件不会被修改，错误使用统一
error envelope。图像脚本测量和尺寸证据仍未实现。

2026-11-12 后续：增加只读 `engineering_measure_image`。调用方提供目标 RGB 和容差，
SimLoom 返回源像素区域内的匹配数量、边界、质心、比例和可选的 PNG 标注预览；源图不被
修改。这先建立了可审计的图像测量契约，仍不等同于任意 Python 图像脚本执行。

2026-11-13 后续：`engineering_measure_image` 增加可选 `coordinate_system`，按完整源图像
尺寸把匹配边界和质心映射到工程图纸坐标，并严格校验有限范围值与 y 轴方向；同时保留源像素
证据和 overlay。映射只描述图纸坐标关系，不宣称从图像自动识别比例或图框。

2026-11-12 后续：增加 `cad.script_apply`。脚本默认关闭，显式开启后只能在事务 staging
目录中读取请求和 DXF 副本，输出文件必须在白名单内；脚本完成后由 SimLoom 重新解析、独立
验证、生成 diff，并写入同一套 checkpoint、历史和 rollback。它用于 CAD 输入准备和可审计
辅助自动化，不代表已经替代 CATIA、Ansys、Adams 或 VI-grade 的原生操作。

2026-11-12 后续：增加只读 `cad.visual_diff`。它按稳定实体 ID 对齐两个 DXF 版本，保留
语义 diff，并返回一个可嵌入工作台或 MCP 结果的 SVG：绿色表示新增、红色表示删除、橙色
表示修改、灰色表示未变化。它仍是二维图纸对比，不能替代 CATIA/Ansys/Adams/VI-grade
原生模型和结果查看器。

2026-11-12 后续：收紧 STEP B-Rep 写入边界。`brep.boolean`、`brep.create_primitive` 和
`brep.build_feature_graph` 的输入/输出必须位于项目目录；生成后先做暂存检查，事务提交时
再从实际目标文件重新读取 STEP，验证有效性、非空拓扑和体积，并把检查结果写入事务 journal
和 operation 返回值。提交后检查失败会恢复原文件，旧的 `.brep.json` feature graph sidecar
不会残留在 primitive/boolean 覆盖之后。这个机制提高了本地 B-Rep 可靠性，仍不代表真实
CATIA 或其他商业软件后端已经验收。

2026-11-13 后续：Viewer 增加语义夹点。选中单个实体后，LINE、LWPOLYLINE/POLYLINE、CIRCLE、
ARC、ELLIPSE 和 SPLINE 会从渲染元数据生成夹点；拖动过程使用半透明预览，松开后通过现有
`cad.entity_update` 事务提交，失败时不修改原文件并恢复夹点状态。当前离线回归已覆盖元数据、
事务和历史链路；真实浏览器环境仍需安装 Playwright 后运行交互测试。

2026-11-13 后续：增加 `cad.hole_create` 和 `cad.trace_boundary`。前者生成带孔组和制造字段的
可编辑同心孔轮廓，后者从直线型线网按容差追踪闭合边界并保存源实体追溯。两者都走统一事务、
独立读回、验证、diff、checkpoint 和 rollback；它们仍是 vendor-neutral DXF 辅助能力，不代表
CATIA 原生特征或标准件数据库已经验收。
