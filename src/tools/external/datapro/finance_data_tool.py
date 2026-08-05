"""金融数据查询工具 - A股/港股基本面、估值、行情、财报等结构化数据.

DataPro 路由: query 含股票代码/上市公司名称 + 金融指标描述 → stock_finance.
单次最多 3 只标的, 超出自动分批.
"""

from __future__ import annotations

import re
from typing import ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.external.datapro._base import DataProClient
from src.tools.shared.tool_runtime import sync_runnable

_STOCK_CODE_RE = re.compile(r"\d{6}(?:\.(?:SZ|SH|BJ))?", re.IGNORECASE)
_MAX_STOCKS_PER_CALL = 3

_LLM_PROMPT = """你是专业金融数据整理助手. 对金融数据查询结果做轻加工, 输出结构化文本.

严格原则:
1. 保留原始数值和字段名, 禁止篡改/编造/估算任何数据
2. 远端按query维度返回对应数据子表, 优先提取与用户查询意图相关的字段, 无关字段可省略
3. 按金融维度归类(盈利能力/成长能力/偿债能力/营运能力/技术形态/估值/行情等)
4. 完整保留来源标记(查询代码/数据口径/trace_id/数据期)
5. 多标的查询整理成便于横向对比的结构(各标的并列展示同类指标)
6. 禁止将数值翻译成主观评价散文(如"盈利优秀"), 仅做客观归类呈现

输出规范:
- 标题行: [标的名称 (查询代码)]
- 来源行: 来源: DataPro数据库 | trace_id: xxx | 数据期: xxx
- 分类小标题 + 字段值列表
- 字段值保留原始精度, 不四舍五入
"""


class FinanceDataInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    query: str = Field(
        description=(
            "查询语句: 标的(公司全称/股票代码) + 具体金融维度表述. "
            "维度表述决定返回哪个数据子表, 须明确具体维度"
            "(如ROE盈利水平/K线形态分析/季度业绩/市值与估值水平)."
        )
    )


def _split_query_if_needed(query: str) -> list[str]:
    """股票代码超限时拆分查询, 突破DataPro金融单次≤3标的限制."""
    codes = _STOCK_CODE_RE.findall(query)
    if len(codes) <= _MAX_STOCKS_PER_CALL:
        return [query]

    description = _STOCK_CODE_RE.sub("", query).strip()
    description = re.sub(r"^[、,，;；\s]+|[、,，;；\s]+$", "", description)  # noqa: RUF001

    groups = [
        codes[i : i + _MAX_STOCKS_PER_CALL]
        for i in range(0, len(codes), _MAX_STOCKS_PER_CALL)
    ]
    suffix = f" {description}" if description else ""
    return [" ".join(g) + suffix for g in groups]


@sync_runnable
class FinanceDataTool(BaseTool):
    """金融数据查询工具 - A股/港股基本面、估值、行情、财报."""

    name: str = "finance_data"
    summary: str = (
        "金融数据查询, 查A股/港股基本面(财务/估值/行情/研报/技术形态等结构化指标)"
    )
    search_keywords: ClassVar[list[str]] = [
        "金融",
        "股票",
        "基金",
        "债券",
        "期货",
        "期权",
        "财报",
        "财务",
        "ROE",
        "ROA",
        "上市公司",
        "K线",
        "盈利预测",
        "技术形态",
        "估值",
        "研报",
        "市值",
        "营收",
        "净利润",
        "PE",
        "PB",
        "基本面",
        "港股",
        "年报",
        "季报",
        "行情",
        "连涨",
        "MACD",
        "均线",
        "对比",
    ]
    description: str = (
        "金融数据查询工具, 查询A股/港股权威结构化金融数据"
        "(火山引擎DataPro金融数据库, 覆盖全球股票/期货/期权/债券/基金).\n"
        "标的可用公司全称或股票代码; query维度表述决定返回哪个数据子表, "
        "须写明具体金融维度, 系统自动精炼为高密度摘要.\n\n"
        "数据维度(按query自然语言路由):\n"
        "- 盈利能力: ROE/ROA/净利率/毛利率\n"
        "- 技术形态: K线/连涨天数/MACD/KDJ/均线\n"
        "- 估值: PE/PB/市值\n"
        "- 行情: 开高低收/成交量\n"
        "- 财务报表: 季报/年报/营收/净利润/现金流\n\n"
        "用法: query = 标的(公司全称/股票代码) + 具体维度表述. "
        "维度表述决定返回子表: 写'ROE盈利水平'返回盈利指标, 写'K线形态分析'返回行情技术指标, "
        "写'金融数据'这类模糊词会命中无关表. "
        "用户问多个维度时分别调用多次(每次一个维度), 不要合并成模糊query.\n"
        "单次最多3只标的, 超出自动分批.\n\n"
        "示例(已验证):\n"
        '- A股盈利: {"query": "比亚迪 002594 ROE盈利水平"}\n'
        '- 技术形态: {"query": "002594 K线形态分析"}\n'
        '- 季度业绩: {"query": "五粮液 季度业绩季报数据"}\n'
        '- 年报财务: {"query": "平安银行 2024年年报财务数据"}\n'
        '- 港股估值: {"query": "小米集团 市值与估值水平"}\n'
        '- 港股财务: {"query": "网易 最新季度财务数据"}\n'
        '- 多标的对比: {"query": "美团 小米 营收对比"}'
    )
    args_schema: type[BaseModel] = FinanceDataInput

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._client = DataProClient(
            cache_collection="datapro_finance_cache",
            llm_prompt=_LLM_PROMPT,
        )

    async def is_available(self) -> bool:
        return self._client.is_available()

    @override
    async def _arun(self, query: str) -> str:
        return await self._client.execute(
            self.name, query, split_fn=_split_query_if_needed
        )


__all__ = ["FinanceDataInput", "FinanceDataTool"]
