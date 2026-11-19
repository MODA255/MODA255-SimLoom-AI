# 规格文档提取

`project.extract_requirements` 从项目内的 TXT、Markdown、JSON 或 PDF 文档中提取有限、可审计的图纸事实。当前识别 plate 规格的宽度、高度、孔径、边缘偏移和孔数，并返回源文件 SHA-256、警告和置信度。

```python
requirements = agent.execute_operation("project.extract_requirements", {
    "project_dir": "./project",
    "document": "requirements.md",
})
```

提取器使用明确的规则和单位字段。无法识别的说法会保留 warning，不会猜测尺寸。`revise_from_document` 这个 MCP façade 会把识别出的 plate 尺寸和角孔参数交给 `cad.revise_drawing`，之后仍由规格验证和 DXF 回读决定是否成功。

PDF 读取使用可选的 `pypdf` 依赖。没有该依赖时会返回 `PDF_TEXT_EXTRACTION_UNAVAILABLE`，保留人工复核路径。
