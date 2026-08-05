"""企业风险数据查询工具 - 司法诉讼、行政处罚、经营异常、失信等.

DataPro 路由: query 含公司全称/统一信用代码 + 风险维度描述 → enterprise_risk.
单次最多 5 家公司.
"""

from __future__ import annotations

from typing import ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.external.datapro._base import DataProClient
from src.tools.shared.tool_runtime import sync_runnable

_LLM_PROMPT = """你是专业企业风险数据整理助手. 对企业风险数据查询结果做轻加工, 输出结构化文本.

严格原则:
1. 保留原始字段名和数值, 禁止篡改/编造任何数据
2. 远端按query维度返回对应数据子表, 优先提取与用户查询意图相关的字段, 无关字段可省略
3. 按风险维度归类(司法诉讼/行政处罚/经营异常/失信/被执行/限制消费等)
4. 完整保留来源标记(企业名称/trace_id/数据期)
5. 多企业查询时各企业并列展示同类风险信息
6. 禁止主观评价, 仅做客观归类呈现; 风险条数/金额等如实呈现

输出规范:
- 标题行: [企业全称]
- 来源行: 来源: DataPro数据库 | trace_id: xxx
- 按风险类型分类: 类型 + 条数 + 典型案例明细
"""


class EnterpriseRiskInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    query: str = Field(
        description=(
            "查询语句: 公司全称或统一信用代码 + 具体风险维度表述"
            "(如司法诉讼/行政处罚/经营异常与失信/被执行信息). "
            "须用公司全称, 避免简称; 维度表述决定返回哪个数据子表."
        )
    )


@sync_runnable
class EnterpriseRiskTool(BaseTool):
    """企业风险数据查询工具 - 司法诉讼、行政处罚、经营异常、失信."""

    name: str = "enterprise_risk"
    summary: str = (
        "企业风险数据查询, 查境内企业司法诉讼/行政处罚/经营异常/失信/被执行等风险信息"
    )
    search_keywords: ClassVar[list[str]] = [
        "企业风险",
        "公司风险",
        "司法",
        "诉讼",
        "起诉",
        "被告",
        "原告",
        "行政处罚",
        "经营异常",
        "失信",
        "被执行",
        "限制消费",
        "限高",
        "裁判文书",
        "开庭公告",
        "法院公告",
        "终本案件",
        "欠税公告",
        "严重违法",
        "股权冻结",
        "商业纠纷",
    ]
    description: str = (
        "企业风险数据查询工具, 查询境内注册主体的权威风险信息"
        "(火山引擎DataPro企业风险数据库, 覆盖国内全量企业).\n"
        "标的须用公司全称或统一信用代码(避免简称, 简称路由不准确).\n\n"
        "数据维度(按query自然语言路由):\n"
        "- 司法诉讼: 裁判文书/开庭公告/法院公告/案件类型分布\n"
        "- 行政处罚: 处罚事由/处罚机关/处罚日期\n"
        "- 经营异常: 列入原因/列入日期/移出记录\n"
        "- 失信与被执行: 失信被执行人/限制消费/终本案件\n\n"
        "用法: query = 公司全称(或统一信用代码) + 具体风险维度表述. "
        "用户问多个维度时分别调用多次(每次一个维度), 不要合并成模糊query.\n"
        "单次最多5家企业.\n\n"
        '特殊能力: 支持跨企业关联查询(如"A与B的诉讼/商业纠纷"), 返回双方关系分析.\n\n'
        "示例(已验证):\n"
        '- 司法诉讼: {"query": "美团 司法诉讼与风险"}\n'
        '- 行政处罚: {"query": "恒大地产集团有限公司 行政处罚"}\n'
        '- 经营异常: {"query": "北京三快科技有限公司 经营异常与失信"}'
    )
    args_schema: type[BaseModel] = EnterpriseRiskInput

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._client = DataProClient(
            cache_collection="datapro_risk_cache",
            llm_prompt=_LLM_PROMPT,
        )

    async def is_available(self) -> bool:
        return self._client.is_available()

    @override
    async def _arun(self, query: str) -> str:
        return await self._client.execute(self.name, query)


__all__ = ["EnterpriseRiskInput", "EnterpriseRiskTool"]
