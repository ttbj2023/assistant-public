"""图表渲染输入模型 - 三个子工具各自独立的 Input.

拆分自原 ChartMakerInput: engine 分派歧义已消除, 各模型只暴露对应引擎的精确参数,
并保留针对各自 code 字段的常见误称容错 (继承 QueryAliasModel).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import ConfigDict, Field

from src.tools.shared.query_alias_model import QueryAliasModel


class MermaidChartInput(QueryAliasModel):
    """mermaid 流程图渲染输入 (无 width/height, mermaid 引擎忽略尺寸)."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "mermaid_code": "code",
        "graph_code": "code",
        "diagram": "code",
        "query": "code",
    }

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    code: str = Field(
        min_length=1,
        max_length=100000,
        description=(
            "mermaid 源码, 必填. 必须是可被 mermaid 直接渲染的语法源码, "
            "不能是自然语言描述或伪代码. "
            "如 'flowchart TD\\nA-->B' (流程图), 'sequenceDiagram\\n...' (时序图)"
        ),
    )
    filename: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "输出文件名(不含扩展名), 可选. "
            "留空时根据 title 自动生成; title 也为空时默认 'mermaid_chart'"
        ),
    )
    title: str | None = Field(
        default=None,
        max_length=200,
        description="图表标题(可选). 推荐使用, 渲染在图表上方",
    )
    scale: int = Field(
        default=3,
        ge=1,
        le=6,
        description="清晰度倍率. 简单图(少量节点)用3, 复杂图(多分支/密集文字/多节点)用5.",
    )


class VegaChartInput(QueryAliasModel):
    """Vega-Lite 数据图表渲染输入."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "spec": "code",
        "vega_spec": "code",
        "spec_json": "code",
        "json": "code",
        "query": "code",
    }

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    code: str = Field(
        min_length=1,
        max_length=100000,
        description=(
            "Vega-Lite JSON 规范字符串, 必填. 必须是完整的 spec JSON, "
            '不能是自然语言描述. 如 \'{"mark":"bar","encoding":{...}}\'. '
            "支持折线/柱状/饼/散点/堆叠等图表类型"
        ),
    )
    filename: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "输出文件名(不含扩展名), 可选. "
            "留空时根据 title 自动生成; title 也为空时默认 'vega_lite_chart'"
        ),
    )
    title: str | None = Field(
        default=None,
        max_length=200,
        description="图表标题(可选). Vega-Lite 通常在 spec 内含 title, 此字段用于外部标题",
    )
    width: int | None = Field(
        default=None,
        ge=100,
        le=4000,
        description="图表逻辑宽度(px, 可选). 注入 spec.width 覆盖原值",
    )
    height: int | None = Field(
        default=None,
        ge=100,
        le=4000,
        description="图表逻辑高度(px, 可选). 注入 spec.height 覆盖原值",
    )
    scale: int = Field(
        default=3,
        ge=1,
        le=6,
        description="清晰度倍率(deviceScaleRatio, 1=标准 3=高清默认 6=最大)",
    )


class MarkmapChartInput(QueryAliasModel):
    """markmap 思维导图渲染输入."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "markdown": "code",
        "md": "code",
        "mindmap": "code",
        "query": "code",
    }

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    code: str = Field(
        min_length=1,
        max_length=100000,
        description=(
            "Markdown 思维导图源码, 必填. 用层级标题/列表表达树状结构. "
            "如 '# 标题\\n## 子项\\n- 内容'. 不能是自然语言描述"
        ),
    )
    filename: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "输出文件名(不含扩展名), 可选. "
            "留空时根据 title 自动生成; title 也为空时默认 'markmap_chart'"
        ),
    )
    title: str | None = Field(
        default=None,
        max_length=200,
        description="图表标题(可选). 渲染在思维导图上方",
    )
    width: int | None = Field(
        default=None,
        ge=100,
        le=4000,
        description="SVG 画布宽度(px, 可选). 默认 1200",
    )
    height: int | None = Field(
        default=None,
        ge=100,
        le=4000,
        description="SVG 画布高度(px, 可选). 默认 800",
    )
    scale: int = Field(
        default=3,
        ge=1,
        le=6,
        description="清晰度倍率(deviceScaleRatio, 1=标准 3=高清默认 6=最大)",
    )


__all__ = ["MarkmapChartInput", "MermaidChartInput", "VegaChartInput"]
