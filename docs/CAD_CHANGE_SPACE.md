# CAD 模型空间与图纸空间

`cad.change_space` 把选定实体移动到模型空间或指定的纸空间 sheet。操作只改变实体所属
layout，不改变几何坐标、图层、实体 ID 或其他参数；目标纸空间必须已经存在。

```json
{
  "operation": "cad.change_space",
  "input_file": "drawing.dxf",
  "output_file": "drawing-paper.dxf",
  "entity_ids": ["e00001"],
  "space": "paper",
  "sheet": "A3"
}
```

目标为 `model` 时省略 `sheet`。写入后 SimLoom 会重新打开 DXF，确认实体仍存在并且被原生
DXF 放入正确的 layout。空间转换只负责图纸布局；模型与纸空间之间的比例、视口显示和打印设置
仍由独立的 sheet/viewport 操作控制。
