# Template-Based DOCX Merge Report - 2026-05-07

## 文件角色

- 格式模板：`C:/Project/New_Energy_Sys/reports/新能源储能侧优化调度系统毕业论文初稿.docx`
- 内容来源：`thesis/main_formatted_formula.docx`
- 合成输出：`thesis/main_template_based_formula.docx`

## 处理内容

- 保留新论文正文、图表、公式、截图、引用标注和参考文献。
- 复制初稿 DOCX 的 Word 样式、编号、设置和主题作为格式基线。
- 统一页眉为论文题目，页脚使用与初稿相近的居中页码格式。
- 保留自动目录域，并补齐可见“目录”标题。

## 自动检查

- `paragraphs`: `497`
- `tables`: `18`
- `images`: `11`
- `sections`: `2`
- `visible_toc_heading`: `True`
- `toc_field`: `8`
- `page_field`: `2`
- `header_refs`: `2`
- `footer_refs`: `2`
- `references_heading`: `True`
- `reference_items`: `63`
- `citation_marks`: `179`
- `figure_5_1`: `True`
- `table_6_1`: `True`
- `equation_5_1`: `True`

## 人工复核点

- 打开 Word 后执行 `Ctrl+A` 和 `F9`，更新目录页码和页码域。
- 本环境无法调用 Word 渲染页面，最终分页仍需以本机 Word 打开后的视觉结果为准。

Pitfall：该方案保留新论文内容并迁移模板格式，但 Word 自动目录在更新前可能显示旧页码或空白页码。
