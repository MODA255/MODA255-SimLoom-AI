# CAD 孔轮廓

`cad.hole_create` 在一个事务中创建可继续编辑的同心圆孔轮廓，并把制造语义写入统一工程对象和 DXF sidecar。

支持的 `kind`：

- `clearance`：通孔轮廓；
- `tapped`：螺纹孔轮廓，可同时给出 `minor_diameter`；
- `counterbored`：通孔和沉头外径两条圆；
- `countersunk`：通孔和沉孔外径两条圆，并保存 `countersink_angle`；
- `spotface`：通孔和端面沉孔外径两条圆。

示例：

```json
{
  "operation": "cad.hole_create",
  "center": {"x": 40, "y": 20},
  "diameter": 8,
  "kind": "counterbored",
  "outer_diameter": 14,
  "depth": 4,
  "through": false,
  "standard": "ISO",
  "size": "M8",
  "tag": "H-01"
}
```

输出实体带有共同的 `hole_id`、`hole_role` 和 `hole` 元数据，因此可以在后续操作中按孔组审计、移动、删除或继续编辑。几何仍以原生 `CIRCLE` 实体保存，便于 DXF 读回和 Viewer 夹点编辑。

这里的标准、尺寸和孔型字段是可审计的工程语义输入，不代表 SimLoom 已经替代 CATIA 或其他厂商软件的标准件数据库；实际制造标准仍应由工程师和原生软件验收。
