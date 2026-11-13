# CAD 环形轮廓

`cad.donut_create` 创建一个带稳定 `donut_id` 的外圆和内圆。两个圆都是原生 DXF CIRCLE，
因此可以继续被选择、移动、标注、阵列和编辑；sidecar 保存 `donut_role` 和 `donut_id`，让
验证器能识别这两个圆属于同一个环形轮廓。

`outer_radius` 必须大于 `inner_radius`，两者都必须是有限正数。当前表示方式是可编辑的同心
圆对，适合图纸输入准备和预览；它不宣称是 CATIA Part Design 的参数化实体或厂商软件中的
原生特征。需要真实三维特征时，计划应通过 Windows worker 调用 CATIA 或其他商业软件。
