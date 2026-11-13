# CAD 边界追踪

`cad.trace_boundary` 会在事务中读取现有 LINE 和直线型 LWPOLYLINE/POLYLINE，按给定容差把相接端点组装成图，并寻找一条闭合回路。成功后生成一个新的闭合 LWPOLYLINE，源实体默认保留。

```json
{
  "operation": "cad.trace_boundary",
  "seed_entity_id": "e00007",
  "tolerance": 0.001,
  "layer": "PROFILE",
  "keep_source": true
}
```

可以通过 `seed_entity_id`、`entity_id`、`entity_ids` 或 `point` 指定搜索范围或起点。输出实体的 `boundary_trace` 元数据保存源实体 ID、容差和起点，后续可以继续用于偏移、拉伸、倒角、填充和版本 diff。

当前实现明确拒绝带 bulge 的多段线和弧段，避免把曲线错误近似成直线；没有闭合回路、端点无法按容差连接或线网存在无法找到的回路时，事务会失败并恢复输入文件。
