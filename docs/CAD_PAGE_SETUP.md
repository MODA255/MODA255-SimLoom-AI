# CAD 纸张与打印设置

`cad.set_page_setup` 为已有纸空间 sheet 设置纸张、方向、边距、偏移、比例和输出设备，并把
设置写入原生 DXF layout。支持 A0～A4，也支持直接提供宽度、高度和单位。

```json
{
  "operation": "cad.set_page_setup",
  "input_file": "drawing.dxf",
  "output_file": "drawing-a3.dxf",
  "sheet": "SHEET1",
  "setup": {
    "paper": "A3",
    "orientation": "landscape",
    "margins": [5, 5, 5, 5],
    "offset": [0, 0],
    "scale": [1, 1],
    "device": "DWG to PDF.pc3"
  }
}
```

边距顺序为左、下、右、上。比例可以是单个正数或 `[numerator, denominator]`。写入后会
重新读取 layout，检查纸张尺寸、方向和设置元数据；视口缩放与 PDF 多页输出仍由 viewport
和 `cad.plot` 操作负责。
