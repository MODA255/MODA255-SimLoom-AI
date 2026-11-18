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

`cad.query` 的复杂块实例查询现已补齐：支持块名、ATT/ATTRIB 属性精确筛选和文本筛选，
并可按块基点、非等比缩放、旋转以及嵌套 INSERT 解析世界坐标包围盒。查询返回稳定的
`block_path`、完整 `block_paths` 和可审计 warning，不会把缺失或递归块误报为已解析几何；
分页、metadata-only 返回和 MCP schema 也保持兼容。细节见 `CAD_QUERY_BLOCKS.md`。

`cad.set_annotation_scale` 现已补齐：统一对象模型保存标注比例，原生 DXF 写入 `$DIMSCALE`，
按比例调整文字注释并保留尺寸、引线、GD&T、表面粗糙度和 BOM 的标注元数据；模型几何和实际
测量值不改变，细节见 `CAD_ANNOTATION_SCALE.md`。

`cad.rectangle_create` 和 `cad.polygon_create` 现已补齐：生成带旋转、尺寸校验和原生回读的
闭合 LWPOLYLINE，细节见 `CAD_PRIMITIVES.md`。

`cad.donut_create` 现已补齐：生成带稳定关联元数据的同心内外圆，支持原生回读和继续编辑，
细节见 `CAD_DONUT.md`。

`cad.ray_create` 和 `cad.xline_create` 现已补齐：写入原生 DXF 构造线并对方向归一化、回读和
零向量错误做验证，细节见 `CAD_CONSTRUCTION_LINES.md`。

`project.asset_export` 现已补齐：把项目资产的 SVG 预览、修改记录和可选元数据打包成独立
HTML，支持 CLI、Web 和 MCP 调用；生成和回读均经过事务、checkpoint、哈希和路径/外部资源
检查，适合把 Agent 的中间结果交给工程师离线查看。它改善了分享和审计体验，但不等于
AgentCAD 的完整实时 2D 交互，也不提供 CATIA、Ansys、Adams 或 VI-grade 的原生查看器。

四款软件 runner 现已在旧的 `verification`、`vendor_validation` 和 `output_manifest` 字段
旁边返回统一的 `details.engineering_result`（`simloom-ai/engineering-result/v1`）。该 envelope
把执行状态、文件完整性、工程指标、观测值、产物哈希和工程验收结论分开，且明确标记
`real_machine_verified`；现在还携带实际使用的输入/输出与指标契约摘要，以及统一的
`preflight → execute → collect → validate → recover` 生命周期状态，便于 Agent 依据原始
规则做失败归因、修复候选比较和重规划；错误还会带有配置、契约、传输、执行、回收、验证、恢复、
并发和发布分类，且会持久到工作流步骤状态供恢复页直接读取；详见 `ENGINEERING_RESULT.md`。
preflight、worker 请求和结果回收还共享版本化的 `software-operation-contract/v1` 与
`definition_digest`，避免动作绑定在不同入口发生漂移。这减少了 Agent 为四款
软件编写不同结果解析器的工作，但尚未替代真实软件的原生结果读取和 Windows 实机证据。

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

`validate_cad_document` 和独立的 `dxf_validation.inspect_native` 现在会对非相邻多段线段、
bulge 弧段、SPLINE 采样段和 HATCH 多段线边界检查自交/重叠；默认保留为可追溯 warning，
工程要求设置 `reject_self_intersections` 或 `reject_curve_topology` 后升级为错误，细节见
`CURVE_TOPOLOGY.md`。

## 必须逐项证明的能力

| 能力 | 本地证据及现状 | 完成所需证据 / 下一步 |
| --- | --- | --- |
| 真实二维绘图、编辑与 typed operations | 已有统一对象、原生 DXF 保存与多类编辑；`cad_operations.py`、CAD 回归测试；直线/圆弧/圆和直线多段线 offset 现在支持有符号左右侧、连接拐角和明确拒绝 bulge 曲线；SPLINE 已支持创建、读回、变换和作为有限 trim 边界；关联 HATCH 移动会自动扩展到 source boundary entities；`cad.entity_trim` 现在支持有限 LINE/CIRCLE/ARC/ELLIPSE/SPLINE/polyline 边界和 bulge 圆弧段；`cad.entity_extend` 现在支持 LINE/CIRCLE/ARC/ELLIPSE/SPLINE/polyline 有限边界、start/end/auto、最近交点、布局和锁层检查；`cad.center_mark_create` 现在为 CIRCLE/ARC 生成带标签、幂等策略和布局检查的可编辑中心标记；`cad.entity_remove_duplicates` 现在按容差清理同层同布局重复几何并重写选择集、约束和可用的关联 HATCH 引用；`cad.entity_stretch` 现在支持 crossing window、部分端点/顶点移动、完整实体移动、布局过滤和尺寸重测；两相连线的圆角/等距倒角已补真实切点、短弧与独立检查；新增只读 `cad.query`，按类型、图层、选择集、块、布局、属性、文字、闭合状态、约束引用、数值范围和空间窗口查询，并返回稳定 `EntityRef`、分页和 metadata-only 结果；2D Viewer 已提供查询 JSON、自动选择、高亮和定位，见 `CAD_CORNERS.md`、`CAD_TRIM.md`、`CAD_EXTEND.md`、`CAD_CENTER_MARK.md`、`CAD_DUPLICATES.md`、`CAD_STRETCH.md` | 对照上游每种操作的几何语义逐项验收，不能仅比较操作名称。SPLINE 极端退化、精确 NURBS 交点、关联边界重构、大图纸查询性能和厂商实体仍需真实图纸验收 |
| 原子批次、checkpoint、diff、跨会话恢复 | `cad_history.py`、`transactions.py`；已有真实子进程中断和恢复测试 | 扩充外部编辑冲突及各写入口的覆盖；核对脚本等新增入口遵守同一契约 |
| STEP B-Rep 写入可靠性 | `brep.boolean`、`brep.create_primitive`、`brep.build_feature_graph` 现在把输入和输出限制在项目目录，写入后先检查暂存文件，再从提交目标重新读取 STEP，验证形状、拓扑和体积；失败由事务恢复原文件；feature graph sidecar 随输出一起提交并在覆盖时清理 | 仍需在更多 OpenCascade 版本和真实复杂 STEP 数据上验收；布尔退化、装配、多实体和外部文件冲突的工程规则还需扩展 |
| 受控脚本执行 | 已有 `cad.script_apply`：默认关闭；显式启用后在事务 staging 工作目录运行，静态检查高风险导入/调用，限制脚本时长、输出大小和输出文件，重新读取 DXF 做独立验证、diff、checkpoint、历史和失败恢复；`tests/test_controlled_cad_scripts.py` | 在 Windows worker 上把同一策略映射到受控脚本宿主，并补 OS 级隔离和真实项目脚本验收；当前策略不是完整安全沙箱 |
| 独立工程验证 | `validate_cad_document` 和独立读取 DXF 的 `dxf_validation.inspect_native` 现在分别检查打印文字高度、闭合轮廓退化、重复几何、尺寸数值/引用、可配置尺寸关联，以及非相邻多段线段、bulge 弧段、SPLINE 采样段和 HATCH 边界的自交/重叠；原生结构审计、几何读回、标注测量仍有测试 | 继续补齐字体实际字形覆盖、精确 NURBS/厂商拓扑、尺寸与实体的标准级关联规则，以及更完整的可修复错误分类 |
| 原生几何交互视图 | `cad_render.py`、`cad_viewer.py`、`test_cad_viewer.py`、真实浏览器几何检查；Viewer 已支持选择、图层、框选、缩放、平移、SVG 路径对象捕捉和 LINE/LWPOLYLINE/CIRCLE/ARC/ELLIPSE/SPLINE 的语义夹点编辑；夹点松开后复用 `cad.entity_update` 事务、验证、diff、历史和 rollback | 原生模型视图这一子项已有实现；客户图纸、复杂字体、性能、捕捉精度和真实浏览器夹点操作仍待验收 |
| 历史版本查看、视觉 diff、实时查看 agent 修改 | 有持久撤销/重做、字段 diff、SVG 叠加和项目时间线。`cad.history_view` 校验并只读渲染事务保存的 DXF/sidecar，支持前后步和播放/暂停。批次现在为每个 operation 保存独立 before/after 统一对象快照、diff、验证摘要和可验证 SHA-256，并通过 `cad.batch_step_view` 只读渲染；Web 工作台的 2D Viewer 操作列表也能直接加载批次 after 快照并返回当前文件；详情见 `CAD_BATCH_REPLAY.md`。项目版本 schema 2 在同一事务中保存原始文件与配套元数据，支持历史原件下载、真实文件预览、向新目录导出，以及可离线打开的历史预览 HTML 和 diff HTML；版本时间线节点可点击并联动相邻版本比较。`project.watch_*` 现在可持久保存项目内文件指纹，跨进程轮询并识别 added/modified/touched/deleted/unstable，Web 工作台也可直接开始、轮询和停止；`project.dependency_scan` 可从显式清单、可读文本引用和配套文件生成保守依赖图；见 `PROJECT_VERSION_FILES.md`、`FILE_WATCH.md` 与 `DEPENDENCY_SCAN.md` | 依赖扫描仍不能解析专有二进制装配拓扑，也没有更大工程的回放验收；浏览器真实点击回放仍需 Playwright 环境验收。文件监测和依赖图只提供可审计证据，不等于厂商软件打开、求解成功或工程语义验收 |
| 工程图排版和文件交付 | 原生标题栏、纸空间/视口、多页 PDF、单页 SVG/PNG、每页比例和尺寸校验；`DRAWING_EXPORT.md` | 标准比例、中文/SHX 字体和打印可读性仍需补齐 |
| Agent 能直接看图与检查源图细节 | Web/本地 viewer 已有 SVG/PNG；MCP `engineering_asset_view`、`engineering://asset`、`engineering_inspect_image` 和 `engineering_measure_image` 可返回结构化数据、局部 PNG 预览、颜色区域的源像素边界/质心、overlay，以及可选的源像素到工程图纸坐标映射（见 `IMAGE_INSPECTION.md`） | 继续补通用安全图像脚本、尺寸证据标注和客户端互操作验收 |
| 行业图形库 | 已有符号/紧固件/表格等基础生成操作；新增 `cad.standard_catalog`、`cad.standard_library`、`cad.standard_parts_audit`，覆盖 P&ID 符号、14 类 GD&T 特征、M3～M24 紧固件、常用轴承系列和孔型查询，严格模式会拒绝未知输入 | 当前目录是可查询的规划与输入校验契约，不能视为 ISO P&ID、GD&T、标准紧固件/轴承/孔系尺寸库的合规实现；仍需有来源的尺寸表、符号几何和原生软件验收 |
| 工程工作流 | 四软件离线链和基本 CAD 批次可运行；`cad.pid_audit` 可把 tagged P&ID 符号、端口和过程线读回为节点/边并检查孤立符号、连接段引用和要求连接；`cad.process_route_create` 支持正交路线，`cad.pid_valve_cut_pipe` 可在带语义管线上切断并插入带 inlet/outlet 关系的阀门；新增 `cad.draw_arch_plan`，在事务中生成轴对齐墙环、门窗标记、房间面积标签和尺寸，并在落盘读回后核对面积 | 继续补有来源的 P&ID 标准符号几何、端口方向/规格约束、内部岛/复杂开口、回转件视图/BOM 和厂商工程对象；当前建筑和阀门操作仍是 vendor-neutral 关系模型，不是厂商建筑/P&ID 对象 |
| DWG、AutoCAD AUDIT 与实时同步 | 新增可选 `autocad.status`、`autocad.audit` 和事务化 `autocad.convert`；Core Console 脚本、超时、stdout/stderr、输入输出 SHA-256、DXF 独立回读和失败恢复已有离线回归，见 `AUTOCAD_BACKEND.md` | 仍缺 AutoCAD 2027/其它版本的 Windows 实机 DWG 往返、AUDIT 差分、文件锁/冲突和正在运行的 GUI 会话同步；当前能力不能宣称等价于 AgentCAD 的 AutoCAD 实测 |
| MCP、CLI、Python、HTTP 的一致性与策略 | 有统一 operation 调用、profiles、分页、schema/error envelope；MCP 可读取资产 JSON 和 SVG image content；写操作后发送资源更新。safe profile 已在实际调用入口拒绝写/执行操作及会保存产物的 run/generate/version 内置入口，并有绕过 discovery 的直接调用回归。工具执行失败现在按 MCP 语义返回 `result.isError=true`、文本 `content` 和结构化 error envelope；stdio server 支持标准 `ping`、初始化/取消通知且通知不产生响应，已有真实子进程 round-trip 回归。Web API 也返回兼容旧字段的版本化 `error_envelope`、请求 ID、结构化详情和可重试标记，见 `WEB_ERROR_ENVELOPE.md`。只读 `engineering_capabilities`、CLI `capabilities` 与 Web `/api/capabilities` 共享 `simloom-ai/capabilities/v1` 能力矩阵；`engineering_preflight` 还发布完整 operation contract 输出 schema。新增可配置的 tool/operation allow-list、deny-list，`tools/list`、operation resource 和直接调用使用同一策略，详见 `MCP_POLICY.md`；新增 `mcp-config` 生成通用、Claude 和 Codex 客户端配置，详见 `MCP_CLIENT_SETUP.md` | 继续补完整 schema/envelope 覆盖、更多外部 MCP 客户端互操作和断线重连验证 |
| 安装、发布与可重复环境 | 已有启动器和 CI 配置 | 干净 Windows/macOS/Linux 的实际安装、升级和发布产物验证；配置存在不代表通过 |
| 对照验收与真实项目数据 | 当前主要为生成 fixture 和本地功能测试 | 用固定任务、相同输入、明确成功标准与参考系统对照；记录失败和不支持项，不借用上游自评分 |
| 四款商业工程软件后端 | 脚本、桥接与离线验证链已有实现；本机无对应真实软件 | Windows 上 CATIA/Ansys/Adams/VI-grade 的建模、求解、结果回读、错误恢复逐项实测 |

## 核心能力差距评估（2026-11-17）

2026-11-18 后续：能力矩阵动作增加 `next_action`，将 `ready`、`not_probed`、能力缺失和
绑定不可用转换为可发现的下一步提示；它只辅助 Agent 调度，不授权跳过 preflight 或实机证据。

同日后续：`software-preflight/v1` 的步骤和计划现在也返回结构化 `next_actions`，并复用
`run_preflight`、`probe_worker`、`configure_capabilities`、`configure_action_binding` 和
`inspect_diagnostics` 这套建议码。未知阻塞会安全回退到人工诊断；建议只提供调度信息，不能
绕过重新 preflight、事务、停止确认或实机证据。详见 `SOFTWARE_CAPABILITY_REQUIREMENTS.md`。

随后补齐 typed operation `project.preflight` 的输出 schema，使 Python、MCP 内置工具和
`operation.project.preflight` 都发布相同的步骤级/计划级建议和 operation contract 字段；这减少
Agent 为不同入口维护特殊解析器的差距，但仍不构成四款商业软件实机验收。

其后又为 typed operation 发布 `outputSchema.required`，Agent 可以在调度前对 preflight
响应做严格结构校验，减少把旧客户端缓存或不完整响应误当成可执行条件的风险。

Web 工作台随后接入同一份 `next_actions`：默认离线检查启动条件，逐步骤显示阻塞原因和建议，
不会因为打开页面而启动商业软件或探测 Windows worker。这样 Agent-facing contract 和工程师可见的
操作反馈保持一致，减少“API 说未就绪但页面只显示失败”的交互差距。

随后补齐计划级错误建议：无效计划和需要澄清的计划步骤也会返回统一 `inspect_diagnostics` 建议，
避免 Agent 在没有可执行步骤时收到空的指导信息。

同轮把 `EngineeringResult/v1` 的 `metric_evidence` 接入任务详情和可下载 HTML 报告，工程师可以
直接看到指标来源文件、JSON selector 或 CSV 列名与 reducer；历史任务缺少来源时保持为空，不从当前
项目文件推断证据。这改善了结果回溯和 Agent-facing 审计体验，但仍不替代厂商原生后处理器或实机验收。

2026-11-17 后续：新增 `simloom-ai/capability-matrix/v1`。每个已声明的软件动作现在
同时返回状态、规范化要求、缺失原因、发现状态、证据等级和实机验收标记；CLI、Web 和
MCP 复用同一个矩阵，避免 Agent 从不同入口拼接 `doctor()` 字段。该改动改善了操作发现
和调度可靠性，但不会增加四款商业软件的真实验收证据，详见 `CAPABILITY_MATRIX.md`。
能力矩阵现在还支持 `probe=false` 的离线发现，并通过 MCP
`engineering://capabilities` resource 提供给先发现后调用的客户端。

2026-11-18 后续：设置页新增 `simloom-ai/action-profile/v1`。它把四款软件动作的脚本、命令、
输入输出声明、能力要求、恢复策略和绑定摘要集中展示，并提供脱敏的 `simloom-worker-profile.json`。
新增 `project.action_preflight`、CLI `action-preflight` 和 Web `POST /api/action-preflight`，可以
对一个明确选择的软件动作直接复用 adapter 的 file contract 和 `definition_digest`，不必先让规划器
生成完整工程链。它改善了 Agent-facing 的动作发现和部署排障，但仍只代表配置/契约检查，不能证明
商业软件已启动或结果已验收。

同日新增 `simloom-ai/engineering-context-validation/v1`。统一工程结果现在会对 runner 明确导出的
原生对象、坐标系、工况和模型引用检查 schema、ID 唯一性、父子关系、模型/坐标系引用、有限坐标值和
项目相对路径，并把错误作为独立诊断保留。结构验证不会提升 `real_machine_verified`，也不会把不一致
的上下文自动改写成软件执行失败；真实对象语义仍需厂商结果和 Windows 机器证据。

同日新增 `simloom-ai/worker-config-template/v1` 导出。工作台可以生成带 host、port、root、动作命令、
COM 声明和 token 占位符的脱敏 `simloom-worker.json`；Windows worker 支持 `token_env`，启动时从受保护
环境读取令牌，旧的显式 `token` 仍兼容。模板、命令解析和认证环境变量都不能替代 Windows 上的路径、
许可证、COM dispatch、模型求解和异常恢复验收。

随后补齐工作台连接设置：控制端可以保存四款软件的 worker 地址、令牌环境变量名和诊断字段，
并将脱敏运行时覆盖立即应用到当前 adapter；设置页不会写入密钥、动作命令，也不会把连接成功
误报成商业软件实机验收。细节见 `CONNECTION_SETTINGS.md`。这缩小了部署体验差距，但 Windows
worker 的独立配置文件、脚本绑定和真实软件验收仍需逐款完成。

这里的“接近 AgentCAD”分成两个口径：一是 Agent-facing runtime 是否让模型能够可靠地发现、调用、观察、验证、恢复和复现工程操作；二是 SimLoom 的产品主线是否真的能操作 CATIA、Ansys、Adams 和 VI-grade。前一个口径可以在没有商业软件的机器上持续补齐，后一个口径必须依赖 Windows、软件版本、许可证和真实工程文件。

按当前仓库状态，SimLoom 的 Agent-facing 基础已经具备：统一 operation registry、MCP/CLI/HTTP/Python 入口、事务锁、checkpoint、journal、rollback、版本 diff、分页 discovery、结构化 error envelope、离线 CAD/结果验证、任务队列、工作流恢复和有界修复。当前 operation registry 中有 224 个公开名称，其中 115 个是 `cad.*` typed operation；数量已经超过对照项目的 2D 操作规模，但数量不代表每个操作的语义、性能和真实工程兼容性都达到同一水平。

真正的核心差距按影响排序如下：

1. **真实软件闭环仍是最大缺口。** 四款软件的动作绑定、脚本、文件传输、worker、结果契约和 evidence schema 已具备，但当前机器没有 Windows 和商业软件，四款软件的 `real_machine_verified` 仍为 false。没有真实运行证据，就不能确认 CATIA 参数树、Ansys 求解数据库、Adams 结果曲线或 VI-grade 场景的脚本语义、许可证状态、GUI 占用、版本差异和异常恢复。
2. **工程结果的统一语义还不够深。** 现在能可靠解析 JSON、CSV、文本和声明式 vendor report；`EngineeringResult/v1` 已统一暴露输入/输出路径、报告路径、必需指标、单位、原始规则、每个指标的 `metric_evidence` 来源 selector，以及可选的 `engineering-context/v1` 原生对象摘要。四个 runner 现在还支持从动作绑定的 `vendor_options.engineering_context` 补充 build、edition、许可证状态、坐标系、工况和模型引用；真实软件的原生数据库、结果对象和这些映射仍依赖每个动作的绑定脚本明确导出，且还没有 Windows 实机样本证明映射正确。
3. **自主闭环目前是“有界修复”，还不是通用自主工程师。** 工作流可以恢复，统一 envelope 中的失败指标可以匹配工程师声明的 repair strategy，并由 CLI、MCP 或 Python 入口在总预算内串联多个独立子工作流；每轮生成 `repair-comparison/v1`，比较父子指标并在验收通过、回归、无改善、证据不足或预算耗尽时停止或转人工。仍然不会在没有显式策略时推断新的载荷、边界条件、求解设置或建模动作。
4. **Agent 操作契约仍需继续收敛。** MCP 已有分页、订阅、profile、schema 版本和 error envelope，vendor operation 现在也会展开 `engineering_result.contract`、`lifecycle` 和 `failure`；preflight、worker 请求、结果回收和工作流状态已经共享 `software-operation-contract/v1` 与 `definition_digest`。剩余差距是把真实软件原生对象、坐标系、工况和恢复条件继续纳入同一份可版本化 contract，并补更多外部 MCP 客户端互操作。

2026-11-17 后续：`software-operation-contract/v1` 增加可选 `recovery.policy`，统一记录
超时、断线、输出冲突、重试预算、停止确认和可重试错误码。它约束 Agent 的恢复边界，
不会把不确定的软件状态自动判定为可重跑；真实软件会话仍需 Windows 实机验证。

同日新增 Windows worker `simloom-ai/worker-diagnostics/v1`，提供命令、根目录、认证、
COM 声明和 evidence 的只读诊断，并随 `/capabilities` 返回；它改善部署排障，不等于四款
软件的真实建模、求解或许可证验收。
5. **产品化和可重复部署还有差距。** 本地 Web、CLI、MCP 和测试已经可用，但干净 Windows 安装、worker 配置向导、版本升级、许可证诊断、真实软件路径发现和正式发布包还没有完成一轮端到端验收。
6. **对照项目的细节体验仍有差距。** SimLoom 的 2D CAD 和 Viewer 已经很丰富，并且额外覆盖了 B-Rep、结果查看和四软件流程；但 AgentCAD 的优势在于围绕单一图纸 runtime 形成了很紧凑的 agent contract、独立验证和可复现工作流。SimLoom 需要继续减少跨模块的特殊分支，让 Agent 面对 CAD、仿真结果和厂商任务时都得到一致的调用和失败反馈。

因此，当前不能用“CAD operation 数量”概括距离。若只比较 Agent-facing runtime 的基础设施，项目已经接近可用；若比较 SimLoom 的核心使命——真实操作四款工程软件并根据结果自主闭环——仍处于实机联调前阶段。下一轮开发应优先补真实软件原生结果对象映射、原生失败证据和多候选预算调度；2D CAD、Viewer 和离线导出作为这些核心能力的验证载体继续维护。

## 按产品定位调整后的优先顺序（2026-11-17）

用户明确：主要操作 CATIA、Ansys、Adams、VI-grade，而非替代这些软件。
AgentCAD 对齐用于衡量 AI 操作、反馈、验证、追溯的可靠性，不再以扩展本地 CAD 内核为主线。
SimLoom 的产品主线仍是操作 CATIA、Ansys、Adams、VI-grade；AgentCAD 的二维能力只作为
交互和可靠性参考，不能把本地 CAD 能力的增加误认为四款软件已经接入。
上表保留已有能力和未完成项，但不表示所有二维 CAD 功能具有相同开发优先级。

1. 统一四款软件的 operation contract、结果对象、指标单位和证据 envelope。
2. 修通软件动作、脚本、输入传递、结果回收、锁冲突和明确的失败状态。
3. 建立逐软件连接配置与实机证据；一款软件可独立接入，远程控制端无需 Windows。
4. 在软件内完成最小任务，读回原生模型和结果，检查工程指标。
5. 将真实结果和失败信息提供给 AI，支持预算受限的有界重规划、重试与人工查看。
6. 围绕真实任务补充版本比较、可视化与工程交付；本地 2D CAD 和 B-Rep 能力服务于输入准备、审计和回放。

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

2026-11-14 后续：MCP `tools/call` 的工具执行失败改为标准 `result.isError=true`，同时保留
文本 `content` 和结构化 `structuredContent.error`。这样客户端可以区分参数、权限、资源和后端
执行失败与 JSON-RPC 传输/发现错误；safe/full profile、schema 版本拒绝和 CAD 事务冲突都经过
同一错误 envelope 回归测试。这个对齐改善了 agent 的反馈闭环，但还需要真实 MCP 客户端互操作
和断线重连验收。

2026-11-14 后续：CAD 批次和自动修复批次现在为每个实际步骤保存独立 before/after checkpoint，
包括完整统一对象、步骤 diff 和验证摘要；新增只读 `cad.batch_step_view` 重新读取并渲染指定
步骤。正式文件仍由一个事务原子提交，失败时保留运行目录用于诊断。对无法脱离原始厂商 DXF
重建的实体，viewer 使用明确标记的语义 SVG fallback；这提升了 Agent 的可观察性，不等于
厂商软件内部历史回放。

2026-11-14 后续：分块 worker 传输现在与 vendor evidence 校验打通。超过 32 MiB 的原生输出
不再为了验证而整体解码；worker 和控制端对文件流式计算 SHA-256，仅按契约读取需要解析的
STEP、CSV、JSON 指标文件。大网格和求解数据库可以在 Windows worker 上完成完整性校验后
按块回收，仍不把文件完整性误报为商业软件实机验收。

2026-11-15 后续：`cad.validate_and_repair` 扩展了 spline、文本、非有限坐标、重复几何、
曲线/填充边界拓扑、HATCH 关联句柄、DIMENSION 引用和约束引用的有界修复。每项计划包含
来源错误码、原因和目标；同一实体的字段修复会合并，无法推断设计意图的错误会保守删除，
并继续通过独立验证、checkpoint、diff、history 和事务回滚。该闭环仍是离线 CAD 模型能力，
不能替代 CATIA、Ansys、Adams 或 VI-grade 的实机验收。

2026-11-15 后续：新增 AgentCAD 公开 2D operation 名称兼容层。`add_line`、`move`、
`fillet`、`add_linear_dim` 等平面操作会解析到对应的 `cad.*` typed operation，并在 MCP
发现中返回 `compatibility.alias_for` 和参数别名信息；所有调用仍进入 SimLoom 的事务和
验证链。该层解决名称/参数互操作，不等于 AgentCAD 的无文件上下文语义，也不替代商业
软件后端验收，见 `AGENTCAD_COMPAT.md`。

2026-11-14 后续：Windows worker 增加 `simloom-ai/machine-evidence/v1` 验收记录。只有在
Windows 交互式会话中完成并通过独立 vendor evidence 的任务，才能由操作员登记软件版本、
许可证状态、smoke test 和输出哈希；`/capabilities` 的 `verified_actions` 会持续复核这些
哈希，文件变化或作业记录丢失会自动失效。这样控制端可以明确区分“动作已配置”“结果契约
通过”和“四款软件实机验收”，仍需要实际 Windows 许可证环境产生第一批记录。

2026-11-14 后续：规划器现在识别带有 CATIA/Ansys/Adams/VI-grade evidence 参数的请求，选择
真实 typed runner 并从参数推导输入/输出契约；多软件请求支持按工具分组的参数对象。仓库同时
提供手动触发的 Windows self-hosted smoke-test workflow，将 preflight、真实任务、输出归档和
machine evidence 登记串成一条验收路径。它改善了四软件实际接入的可执行性，仍不能替代带许可
桌面环境中的首次运行。

2026-11-14 后续：MCP 初始化现在会回显调用方请求的受支持协议版本，未指定版本时选择当前默认
版本；`tools/list` 对内置工具和 typed operation 都发布标准 `outputSchema`，同时保留
SimLoom 的 schema、输出类型、事务副作用和安全级别扩展字段。外部客户端可以直接发现输入与
输出契约，不必解析自定义元数据；仍需用不同 MCP 客户端做互操作和断线重连验收。

2026-11-14 后续：MCP 增加配置化 tool/operation allow-list 和 deny-list。策略同时过滤
`tools/list`、`engineering_operations`、`engineering://operations` 以及直接 `tools/call`，
deny 规则优先；绕过 discovery 的已知工具返回 `MCP_POLICY_DENIED` 工具执行 error envelope，
safe/full profile 继续叠加生效。这样控制端可以按项目或部署环境暴露最小 MCP 表面，仍需用不同
外部 MCP 客户端做互操作和断线重连验收。

2026-11-14 后续：新增 `brep.tessellate_step`，把 OpenCascade 读取到的 STEP 曲面三角化为项目内
可追溯的 OBJ 或 GLB 显示网格；写入经过事务、checkpoint、原生网格重读和独立网格验证，失败会恢复
目标文件。这样 STEP 不再只能在内存中预览，控制端可以把同一份网格交给 3D viewer 或结果审查
流程；它仍是显示网格，不改变 STEP B-Rep，也不替代 CATIA 原生模型。

2026-11-14 后续：新增只读 `cad.query` 的 2D Viewer 入口。用户或 Agent 可以输入 JSON 查询，
由工作台通过统一 operation 调用读取当前 DXF；返回的稳定 `EntityRef` 会自动选择、变更高亮并
缩放到当前视图，分页时同时显示完整命中数。查询不会进入写事务，也不会改变图纸文件；历史快照
保持只读。复杂块实例过滤、嵌套变换和缺失/递归 warning 已补齐；后续仍需补大图纸查询性能
和真实浏览器交互回归。

2026-11-14 后续：新增 `cad.draw_arch_plan` 工程工作流。它接受轴对齐闭合墙环、厚度、开口和
房间位置，在一个 CAD 事务中生成墙体边界、开口标记、房间面积标签和外形尺寸；房间面积由保存
后的墙体边界重新计算并与请求值核对，失败会回滚。当前只覆盖矩形/正交墙环，内部岛、复杂弧墙
和厂商建筑对象仍未实现。

2026-11-14 后续：新增只读 `cad.verify_dimensions`。它按稳定 `EntityRef`、实体 ID 或 handle 找到
DIMENSION，重新计算可支持的物理测量值，在请求容差内比较目标值，并可额外核对显示文字；结果包含
上下限、物理值、显示值和稳定错误码。操作使用读锁且不会修改图纸，测试覆盖实体引用、容差边界、
显示值不一致、缺失实体和 MCP discovery。它仍不等于厂商级尺寸关联或 GD&T 语义验收。

2026-11-14 后续：新增 `cad.draw_mechanical_assembly`。它从零件规格生成前视、端视和剖视图，
支持分段外径、中心孔、圆周孔阵列、尺寸、BOM、序号气泡和 A3 等纸张布局；事务提交后重新读取
DXF，重新计算已生成尺寸并检查机械语义元数据。当前是 vendor-neutral 工程图工作流，孔深、配合、
表面处理、材料真实性和 CATIA 原生装配约束仍需真实软件或明确工程数据验收。

2026-11-14 后续：新增 `cad.draw_pid`。它从设备、阀门、仪表、连接器和 `设备.端口` 连接关系
生成语义 P&ID 符号与正交流程线，保存管线编号和端口记录，并在内存和 DXF 落盘后分别重建图检查
孤立符号、连接器和重复编号。端口锚点现在也参与 `pid_audit` 的独立连通性判断；它仍是轻量
vendor-neutral P&ID 图模型，不等于 ISO/ISA 全部符号、厂商对象和工艺设计审查。

2026-11-17 后续：有界修复增加 `repair-loop/v1`。CLI、Python 和 MCP 可以在 1～8 次预算内串联
多个显式 repair strategy；同一步骤的多条匹配策略也会按声明顺序逐条评估，每次尝试仍创建独立子工程链，并复用 `repair-comparison/v1` 的父子
指标比较。只有比较结果允许 `evaluate_next_candidate` 时才继续，验收通过、回归、证据不足和预算
耗尽都会留下停止决策并转人工复核。这推进了 Agent-facing 的反馈闭环，但仍不是无策略约束的
通用自主工程师。

2026-11-17 后续：四软件动作增加 `capability_requirements`。适配器能力和远程 worker 特性会进入统一 operation contract；preflight 可以在不启动软件时指出缺失能力，doctor/CLI/Web/MCP 能力矩阵会把未探测或不满足要求的动作从 `ready_actions` 中排除，执行前也会拒绝不满足的绑定。该机制改善 Agent 的调度和失败分类，但它仍是配置/协议证据，不是商业软件实机验收。详见 `SOFTWARE_CAPABILITY_REQUIREMENTS.md`。

2026-11-18 后续：`EngineeringResult/v1` 增加可选 `metric_evidence`，记录每个指标来自 JSON
selector、CSV 列和 reducer，或 CATIA reopen 参数路径；修复比较会把父子来源一起保留，方便
Agent 在数值改善、回归或证据不足时定位原始结果。它增强可审计性，不会把来源记录当作工程验收。

2026-11-18 后续：action profile 增加 `simloom-ai/software-operation-catalog/v1`。MCP 资源、Web
设置页和脱敏 profile 现在同时展示四款软件已支持的 typed operation 目录与当前 runner 绑定状态。
目录由同一份 operation registry 生成，避免 Agent 只能看到已配置动作而不知道可配置的操作范围；
目录项仍不代表脚本、Windows worker、许可证或商业软件已经可执行，具体动作必须继续通过
`project.action_preflight` 和真实结果证据确认。

同轮补齐 MCP operation 资源的按需读取：`engineering://operations` 支持 `detail`、软件分类、分页
和游标，`engineering://operation?name=...` 支持单个完整 schema（包括 AgentCAD 兼容别名）。这让
compact MCP 客户端可以先加载轻量目录，再只请求当前任务需要的参数契约，减少上下文和互操作压力；资源
仍然是只读发现，不代表对应 runner 或商业软件已可执行。

2026-11-17 后续：`EngineeringResult/v1` 增加可选 `engineering-context/v1`。runner 可以明确导出
软件版本/构建/许可证状态、原生对象、坐标系、工况和模型引用；控制端只做有界字段归一化，不从
退出码、文件名或工具名称推断原生语义。MCP operation schema 与结果文档同步发布，真实 Windows
样本仍是验证这些映射的必要条件。
