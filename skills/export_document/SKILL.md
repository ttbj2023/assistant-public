---
name: export_document
description: "将 Markdown 导出为 PDF/DOCX 专业文档. 当用户需要生成月度报告/技术方案/会议总结/产品文档/学术报告等结构化长文, 或明确要求'导出文档/转PDF/生成报告'时触发. 加载后获得 export_document 渲染工具."
---

# 文档导出指南

将完整 GFM Markdown 渲染为带视觉样式的 PDF/DOCX. LLM 负责产出高质量内容, 排版由模板引擎承担, **不要在内容里手搓 HTML/CSS 控制版式**.

## 使用流程

1. 按用户需求组织 **完整** GFM 内容 (语法见 reference="gfm_syntax")
2. 据下方两表选定 `format` 与 `style`
3. 调用 `export_document` 工具提交内容
4. 工具返回下载链接, 转交用户

## 格式选型

| format | 适用场景 | 特点 |
|--------|---------|------|
| `pdf` | 正式报告/交付物/打印分发 | 版式固定, 所见即所得, 跨平台一致 |
| `docx` | 用户需二次编辑/套用企业模板 | 可编辑, 走 pandoc reference-doc 样式 |

默认 `pdf`. 用户未指明时按内容用途判断: 交付/存档选 pdf, 需继续编辑选 docx.

## 风格选型

| style | 适用 | 特征 |
|-------|------|------|
| `default` | 通用文档/日常报告 | 中性无衬线, 蓝灰配色, 宽松行距 |
| `academic` | 论文/学术报告/研究文档 | 宋体正文+黑体标题, 首行缩进2em, 标准页边距 |
| `business` | 项目计划/商务报告/季度总结 | 深蓝主色, 紧凑专业, 标题带下边框 |
| `technical` | API文档/技术手册/开发文档 | 蓝色标题, 代码块圆角高亮, 等宽字体优化 |

默认 `default`. 据内容主题匹配, 不确定时保持 default.

## 参数

- **content** (必填): 完整 GFM Markdown. 支持表格/任务列表/脚注/删除线/Callout/代码高亮/LaTeX 数学/mermaid·vega-lite·markmap 图表
- **format** (可选): `pdf` (默认) 或 `docx`
- **style** (可选): `default`/`academic`/`business`/`technical` (默认 default)
- **filename** (可选): 文件名(不含扩展名), 留空自动从内容首个 `#` 标题生成

## 陷阱

- content 必须是**完整成篇**的 Markdown, 不要只传片段或大纲; 开头建议有 `# 一级标题` (也用于自动生成文件名)
- 图表用**代码块**承载 (```mermaid / ```vega-lite / ```markmap), 预处理器会自动渲染为图片嵌入; 不要自己贴图或写图片 URL
- 图表/表格过多或内容超长时导出耗时上升, 属正常
- 脚注用 `[^1]` 语法; 上下标 `<sub>`/`<sup>` 会降级为普通文本, 重要信息避免依赖上下标
- filename 只用于命名, 不影响文档内标题

## 语法参考

编写内容前, 如需确认某语法的确切写法, 加载完整 GFM 语法速查:

```
load_skill(skill_name="export_document", reference="gfm_syntax")
```
