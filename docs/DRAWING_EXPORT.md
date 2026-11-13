# 工程图纸与 PDF / PNG 出图

无需 CATIA、Ansys、Adams 或 VI-grade：本地 DXF 可创建纸空间图纸、可编辑标题栏，
并导出多页矢量 PDF 或 PNG。图纸编辑仍走 CAD 事务、落盘校验、diff 和持久历史。

## 在工作台使用

1. 在「模型与结果」打开 DXF，展开「图纸与出图」。
2. 选择 A4～A0 纸张及方向，填写图纸名称、图号、版本，点击「创建工程图纸」。
   自动生成纸空间、适合模型的俯视视口和标题栏；一次撤销可撤回整个创建操作。
3. 选择已有图纸后可修改标题栏，点击「保存标题栏」。
4. 点击「生成出图预览」检查原生标注、填充、视口和文字；再导出 PDF 或 PNG。
   下载文件保存在项目 `exports/` 下。预览也会保存 PNG 和出图记录。
5. 打印 PDF 时使用「实际大小 / 100%」。原图修改后需重新导出。

下方交互编辑视图使用原生矢量路径显示模型空间，支持缩放、平移、框选，
不叠加纸空间坐标。详见 [原生 CAD 视图](NATIVE_CAD_VIEWER.md)。
实际纸张排版以「出图预览」为准。

## 可直接运行的示例

```bash
python examples/cad_sheet.py --output ./cad-sheet-example
```

目标文件夹必须不存在。示例通过统一接口创建带四个通孔、两组原生尺寸标注的
安装板图纸，并生成 A3 DXF、PDF、PNG 和两份 `.plot.json` 出图记录。
示例用英文标题以避免依赖特定系统的中文字体；它不是制造验收完成的零件图。

已有文件也可直接出图：

```bash
python -m engineering_agent.cli --project-dir ./project cad-plot part.dxf exports/model.pdf --paper A3 --scale 1:2
python -m engineering_agent.cli --project-dir ./project cad-plot part.dxf exports/sheet.png --layout A3 --dpi 300
# 多页 PDF：每一项可独立指定 layout、纸张、方向和比例
python -m engineering_agent.cli --project-dir ./project cad-plot part.dxf exports/set.pdf \
  --pages '[{"layout":"A3"},{"layout":"A4"}]'
```

命令行默认拒绝覆盖文件。需要明确替换时加 `--overwrite`。

## 接口与比例

`cad.sheet_prepare` 创建物理图纸、俯视视口和原生标题栏，可用于 `cad.batch_apply`。
自动排版基于同一批次中已完成的几何修改；隐藏和不打印图层不参与顶层几何范围。
自动排版使用任意适配比例，标题栏记录该比例的近似显示值，不承诺使用标准比例系列。

`cad.title_block_create` 使用 LINE / TEXT / ATTDEF 定义块，通过纸空间 INSERT 的
ATTRIB 保存实际字段；`cad.title_block_update` 更新现有字段并保留原生 handle。
可设置 1～12 个字段，字段名用大写 ASCII 标识，值最长 160 字符。
新增字段需要新模板，目前更新接口仅修改既有字段。

标题栏通过 `SIMLOOM_TITLE` XDATA 标记。将 DXF 单独复制到新项目后，仍能读回和
修改标题字段；直接删除已跟踪项目的侧车文件会触发原有历史冲突保护。
此模板名为 `simloom-grid/v1`，尚未按 ISO / GB 或企业标准验收。

`cad.plot` 写 PDF / PNG 和 `.plot.json`，不改源 DXF，不增加 CAD 撤销历史。PDF 的
`pages` 参数是页面对象数组；页面对象继承顶层渲染参数，也可以覆盖 `layout`、`paper`、
`orientation`、`scale`、`drawing_units`、`margin_mm`、`dpi`、`color_mode` 和 `strict`。
每页会在出图报告中保存独立的布局、物理尺寸、世界范围、比例和警告。

| 场景 | 规则 |
| --- | --- |
| 模型空间 `layout=Model` | A4～A0、横/纵向、`fit` 或 `1:1` / `1:2` 等数值比例 |
| 纸空间 `layout=<图纸名>` | 使用 DXF 物理纸张尺寸，`paper=layout`，纸空间 `scale=1:1`，保留视口比例 |
| 多页 PDF `pages=[...]` | 1～64 页；每页独立选择模型空间或纸空间、纸张、方向、比例和渲染质量 |
| 模型单位未定义 | `fit` 可用；数值比例必须指定 `drawing_units` |
| 指定比例放不下 | 返回 `PLOT_SCALE_DOES_NOT_FIT`，不会自动缩小 |
| 输出质量 | 默认黑白、150 DPI；可选彩色、72～600 DPI，最多 4000 万像素的渲染画布 |
| 不支持的实体或倾斜视口 | 默认返回 `PLOT_UNSUPPORTED_ENTITIES`；`strict=false` 可显式允许跳过并记录 |
| TrueType 字体缺字 | 默认返回 `PLOT_MISSING_GLYPHS`；可调整 DXF 字体样式并安装匹配字体后重试 |

这些 operation 自动进入 MCP `full` profile 的工具列表；`safe` profile 不开放写操作。
CLI、Python API 和工作台调用相同实现。

## 验证与恢复

出图持有项目锁，记录源文件 SHA-256；渲染结束和提交时都会检查源文件未发生变化。
PDF / PNG 与出图记录作为一组提交，提交后独立重读；失败会恢复已有输出。
源文件发生外部修改时返回 `PLOT_SOURCE_CONFLICT`。

- PDF 使用 pypdf 检查页数、每页物理纸张尺寸和非空内容流；PNG 仍是单页输出。
- PNG 使用 Pillow 完整解码，检查像素尺寸及非全白图片。
- 出图记录保存源/目标哈希、纸张尺寸、渲染范围、实际比例、提示及事务编号。
- 自动测试测量 PNG 内线条像素距离，核对毫米和英寸的指定比例；也检查
  PDF 矢量路径、带孔填充、标题/模型区域、失败回滚、批次排版和网页下载。

```bash
python -m pytest tests/test_cad_plotting.py -q
node tests/browser/cad_plotting.cjs
```

浏览器测试需要 Playwright 和 Chromium，环境变量与 `tests/browser/cad_editor.cjs` 相同。
CI 已配置跨平台 Python 回归和浏览器出图测试；配置不代表所有平台已实机通过。

## 当前边界

采用 ezdxf 绘图和 matplotlib 渲染，输出是二维工程图。
不支持 DWG 直接读写、三维消隐出图、CTB/STB 打印样式、打印机可打印边界/偏移；
原生字体仍依赖本机可用字体，MTEXT 内嵌字体和复杂排版需人工核对。
文字可能转换为矢量轮廓，不承诺 PDF 可搜索文字。

结构/尺寸检查不等于工程语义正确或所有图元视觉一致。客户图纸、中文及 SHX 字体、
GD&T、复杂视口裁剪和商业 CAD 互操作仍需独立回归验收。
