# CAD 构造线

`cad.ray_create` 和 `cad.xline_create` 写入原生 DXF `RAY` 与 `XLINE`。两者都使用 `start`
和 `direction`，方向会被归一化；零向量和非有限值在事务写入前拒绝。

- `RAY` 从起点沿方向正向延伸；
- `XLINE` 沿方向双向无限延伸；
- 两者都可以被图层、选择集、事务、diff、历史、回读和 Viewer 识别；
- 构造线没有有限终点，普通有限边界交点算法不会把它当作 LINE 使用。

```json
{
  "operation": "cad.ray_create",
  "input_file": "source.dxf",
  "output_file": "construction.dxf",
  "start": {"x": 10, "y": 20},
  "direction": {"x": 1, "y": 0}
}
```
