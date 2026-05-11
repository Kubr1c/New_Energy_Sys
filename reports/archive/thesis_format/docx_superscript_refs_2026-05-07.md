# DOCX Format Repair Report - 2026-05-07

## 输出文件

- Word 终稿：`thesis/main_formatted_superscript_refs.docx`
- 内容来源：`thesis/main.tex`
- 格式参考：`reports/毕业论文（示例）.docx`

## 修复内容

- 将 LaTeX `\cite{}` 按 `\bibitem` 顺序转换为正文数字引用。
- 恢复参考文献章节，共 `36` 条。
- 插入 Word TOC 字段、页眉和 PAGE 页码字段。
- 将图题、表题转换为“章-序号”格式。
- 保留 11 张前端截图并应用论文正文版式。

## 自动检查

- `paragraphs`: `470`
- `tables`: `18`
- `images`: `11`
- `sections`: `2`
- `references_heading`: `True`
- `reference_items`: `36`
- `citation_marks`: `793`
- `bracket_citation_marks`: `36`
- `acknowledgement`: `True`
- `toc_field`: `5`
- `page_field`: `1`
- `header_refs`: `1`
- `footer_refs`: `1`
- `header_files`: `1`
- `footer_files`: `1`
- `figure_5_1`: `True`
- `table_6_1`: `True`
- `equation_2_1`: `True`
- `equation_2_4`: `True`
- `equation_5_1`: `True`
- `equation_5_5`: `True`
- `equation_numbered`: `9`

## 人工复核点

- 当前环境未检测到可调用的 `soffice` 或 `winword` 命令，无法自动渲染 DOCX 页面。
- 用 Microsoft Word 打开后执行 `Ctrl+A`，再按 `F9` 更新目录和页码域。
- 重点检查目录页、截图页、参考文献页和附录页是否满足学校最终模板要求。

Pitfall：DOCX 与 PDF 的分页算法不同，Word 更新域后页码可能与 `main.pdf` 存在轻微差异，应以最终提交的 Word 渲染结果为准。
