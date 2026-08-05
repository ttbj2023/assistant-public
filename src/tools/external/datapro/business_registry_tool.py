"""企业工商数据查询工具 - 企业登记信息、股东、变更、知识产权等.

DataPro 路由: query 含公司全称/统一信用代码 + 工商维度描述 → enterprise_info.
单次最多 5 家公司.
"""

from __future__ import annotations

from typing import ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.external.datapro._base import DataProClient
from src.tools.shared.tool_runtime import sync_runnable

_LLM_PROMPT = """你是专业企业工商数据整理助手. 对企业工商数据查询结果做轻加工, 输出结构化文本.

严格原则:
1. 保留原始字段名和数值, 禁止篡改/编造任何数据
2. 远端按query维度返回对应数据子表, 优先提取与用户查询意图相关的字段, 无关字段可省略
3. 按工商维度归类(基本信息/经营状况/股东与变更/知识产权等)
4. 完整保留来源标记(公司名称/trace_id/数据期)
5. 多企业查询时各企业并列展示同类信息
6. 禁止主观评价, 仅做客观归类呈现

输出规范:
- 标题行: [公司全称]
- 来源行: 来源: DataPro数据库 | trace_id: xxx
- 分类小标题 + 字段值列表
"""


class BusinessRegistryInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    query: str = Field(
        description=(
            "查询语句: 公司全称或统一信用代码 + 具体工商维度表述"
            "(如工商信息/股东与变更记录/知识产权专利/经营状况). "
            "须用公司全称, 避免简称; 维度表述决定返回哪个数据子表."
        )
    )


@sync_runnable
class BusinessRegistryTool(BaseTool):
    """企业工商数据查询工具 - 登记信息、股东、变更、知识产权."""

    name: str = "business_registry"
    summary: str = (
        "企业工商数据查询, 查境内企业登记信息/股东/变更记录/知识产权/经营状况"
    )
    search_keywords: ClassVar[list[str]] = [
        "工商",
        "企业信息",
        "营业执照",
        "统一信用代码",
        "知识产权",
        "专利",
        "商标",
        "股权",
        "股东",
        "法人",
        "法定代表人",
        "注册资金",
        "注册资本",
        "变更记录",
        "经营范围",
        "公司登记",
        "企业登记",
        "经营状况",
        "企业年报",
        "实缴资本",
    ]
    description: str = (
        "企业工商数据查询工具, 查询境内注册主体的权威工商数据"
        "(火山引擎DataPro企业工商数据库, 覆盖国内全量企业).\n"
        "标的须用公司全称或统一信用代码(避免简称, 简称路由不准确).\n\n"
        "数据维度(按query自然语言路由):\n"
        "- 基本信息: 企业登记/注册资本/法人/经营范围/组织类型\n"
        "- 经营状况: 年报/营收/纳税/社保人数\n"
        "- 股东与变更: 股东持股/历史股东/工商变更记录\n"
        "- 知识产权: 专利/商标/著作权\n\n"
        "用法: query = 公司全称(或统一信用代码) + 具体维度表述. "
        "用户问多个维度时分别调用多次(每次一个维度), 不要合并成模糊query.\n"
        "单次最多5家企业.\n\n"
        "示例(已验证):\n"
        '- 基本信息: {"query": "华为技术有限公司 工商信息"}\n'
        '- 股东变更: {"query": "深圳市腾讯计算机系统有限公司 股东与变更记录"}\n'
        '- 知识产权: {"query": "华为技术有限公司 知识产权专利"}\n'
        '- 经营状况: {"query": "比亚迪股份有限公司 经营状况"}'
    )
    args_schema: type[BaseModel] = BusinessRegistryInput

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._client = DataProClient(
            cache_collection="datapro_business_cache",
            llm_prompt=_LLM_PROMPT,
        )

    async def is_available(self) -> bool:
        return self._client.is_available()

    @override
    async def _arun(self, query: str) -> str:
        return await self._client.execute(self.name, query)


__all__ = ["BusinessRegistryInput", "BusinessRegistryTool"]
