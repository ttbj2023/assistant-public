---
name: chart_maker
description: "生成专业图表(mermaid流程图/Vega-Lite数据图/markmap思维导图). 当用户需要可视化数据、绘制流程图/架构图/时序图/甘特图/思维导图时触发. 加载后获得三个渲染工具."
---

# 图表制作指南

## 引擎选型

根据用户需求选择引擎, 不同引擎的语法互相独立:

| 场景 | 引擎 | 工具 | 详细语法 |
|------|------|------|---------|
| 流程图/架构图/决策/状态 | mermaid | mermaid_chart | reference="mermaid" |
| 时序图/交互序列 | mermaid | mermaid_chart | reference="mermaid" |
| 柱状图/折线图/散点图/面积图 | Vega-Lite | vega_chart | reference="vega_lite" |
| 堆叠柱/分组柱/双轴图/热力图 | Vega-Lite | vega_chart | reference="vega_lite" |
| 饼图/环形图 | Vega-Lite | vega_chart | reference="vega_lite" |
| 思维导图/知识结构/层级树 | markmap | markmap_chart | reference="markmap" |

**选型原则**: 流程/时序用 mermaid, 数据统计用 Vega-Lite, 层级发散用 markmap.

## 使用流程

1. 根据上表选择引擎
2. **必须**: 加载该引擎的详细语法参考, 获取完整语法和可运行示例:
   ```
   load_skill(skill_name="chart_maker", reference="mermaid")
   load_skill(skill_name="chart_maker", reference="vega_lite")
   load_skill(skill_name="chart_maker", reference="markmap")
   ```
3. 按参考中的语法和示例编写 code
4. 调用对应的渲染工具提交 code

## 通用参数

三个工具共用的参数:
- **code** (必须): 引擎语法的源码字符串, 不能是自然语言描述
- **filename** (可选): 输出文件名, 留空时自动从 title 生成
- **title** (可选): 图表标题, 显示在图片上方
- **scale** (可选): 清晰度倍率, 1标准 / 3默认高清 / 6最大, 图复杂时调大

vega_chart 额外参数:
- **width** / **height** (可选): 图片尺寸 px, 注入 spec 覆盖原值

## 通用陷阱

- 中文标签和特殊字符需用引号包裹 (mermaid 用双引号, Vega-Lite 在 JSON 中天然引号)
- 图表过于复杂时调大 scale (建议 4-6)
- mermaid 不支持 width/height 参数 (由内容自适应)
- Vega-Lite 的 nominal/ordinal X 轴标签默认斜向 -45 度 (避免竖排), 自定义角度在 `encoding.x.axis.labelAngle` 指定
