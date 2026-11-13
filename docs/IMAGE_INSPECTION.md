# 图像测量与图纸坐标

`engineering_measure_image` 对 PNG、JPEG 和 WebP 做只读颜色测量。返回值保留源图像像素坐标，同时可以用 `coordinate_system` 把像素结果映射到工程图纸坐标，便于把视觉证据交给后续规则或工程师复核。

```json
{
  "coordinate_system": {
    "x_min": 0,
    "x_max": 100,
    "y_min": 0,
    "y_max": 50,
    "y_axis": "up"
  }
}
```

映射使用经过 EXIF 校正的完整源图像尺寸。源像素原点在左上角，`x=0` 和 `x=width` 分别对应 `x_min` 和 `x_max`；`y_axis: "up"` 时图像顶部对应 `y_max`、底部对应 `y_min`，`y_axis: "down"` 时保持图像向下为正。颜色匹配的半开像素边界会返回为 `world_match_bbox`，质心会返回为 `world_centroid`。未提供坐标系时，这三个映射字段为 `null`，原有像素测量结果不变。

坐标系必须包含五个字段，四个范围值必须是有限数值且严格递增，`y_axis` 只能是 `up` 或 `down`。映射不会修改原图像，overlay 仍然按源像素区域生成。
