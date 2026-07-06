"""专业数据库工具 - 外部工具, 直接集成DataPro调用 + DeepSeek大上下文LLM整理.

定位: 无状态外部工具, 程序化直接调用DataPro(非agent编排).
核心价值: DeepSeek 1M上下文窗口处理DataPro返回的杂乱结构化原始数据, 提取核心信息.

数据流: query → 语义缓存 → (标的拆分) → 独立client调DataPro
       → _preprocess_response结构精炼(null剥离/重要事实全保留/list预算填充)
       → 合并 → (≥4K时)DeepSeek大上下文LLM整理 → 写缓存 → 结构化返回.

DataPro路由特性: query的维度表述(如"ROE盈利水平"/"K线形态分析"/"季度业绩")决定返回哪个数据子表, 须明确具体维度; 模糊大类词(如"金融数据")会命中无关表.

与web_research区分: 客观结构化数据呈现(归类+来源), 非主观综合成文.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, ClassVar, override

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from src.config.credentials_registry import get_credential
from src.inference.llm.response_utils import content_to_text
from src.tools.experts.model_factory import ExpertModelFactory
from src.tools.shared.base_external_tool import BaseExternalTool
from src.tools.shared.semantic_cache import get_semantic_cache

logger = logging.getLogger(__name__)

_DATAPRO_TOOL_NAME = "dataPro_search"
_DATAPRO_CACHE_COLLECTION = "datapro_cache"
_STOCK_CODE_RE = re.compile(r"\d{6}(?:\.(?:SZ|SH|BJ))?", re.IGNORECASE)
_MAX_STOCKS_PER_CALL = 3  # DataPro金融单次最多3标的
_DATAPRO_CALL_TIMEOUT = 60.0
_LLM_PROCESS_TIMEOUT = 60.0
# 预处理配置: 结构驱动信息密度提取
_SKIP_DEEPSEEK_THRESHOLD = 4000  # 预处理后<此值跳过DeepSeek, 直接返回主模型
_SCALAR_FULL_LIMIT = 600  # 标量值<=此长度全貌保留(经营范围/企业简介等重要事实)
_MAX_LIST_DETAIL = 8  # list明细前N项
_MAX_SUBITEM_FIELDS = 8  # 子项保留前N字段
_INTERNAL_KEYS = frozenset({
    "公司ID",
    "企业ID",
    "企业ID（关联主键）",  # noqa: RUF001
    "归属省份首字母小写",
    "行政区域代码",
    "行业代码",
    "区域编码",
    "企业所在区域编码",
    "注册资本数值(元) 默认-1.0",
    "实缴资本数值(元) 默认-1.0",
})

# LLM整理prompt: 归类/标注/结构化, 严守数据确定性, 不翻译成主观散文
_LIGHT_PROCESS_PROMPT = """你是专业数据整理助手. 对DataPro专业数据查询结果做轻加工, 输出结构化文本.

严格原则:
1. 保留原始数值和字段名, 禁止篡改/编造/估算任何数据
2. 远端按query维度返回对应数据子表, 优先提取与用户查询意图相关的字段, 无关字段可省略
3. 按数据维度归类整理(金融: 盈利能力/成长能力/偿债能力/营运能力等; 工商: 基本信息/经营状况等; 风险: 司法/行政处罚等)
4. 完整保留来源标记(查询代码/数据口径/trace_id/数据期)
5. 多标的查询整理成便于横向对比的结构(各标的并列展示同类指标)
6. 禁止将数值翻译成主观评价散文(如"盈利优秀"), 仅做客观归类呈现

输出规范:
- 标题行: [标的名称 (查询代码)]
- 来源行: 来源: DataPro数据库 | trace_id: xxx | 数据期: xxx
- 分类小标题 + 字段值列表
- 字段值保留原始精度, 不四舍五入
"""


class ProfessionalDatabaseInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    query: str = Field(
        description=(
            "查询语句: 标的(公司全称/股票代码/统一信用代码) + 具体维度表述"
            "(如ROE盈利水平/K线形态分析/季度业绩/司法诉讼). "
            "维度表述决定返回哪个数据子表, 须明确具体维度."
        )
    )


class ProfessionalDatabaseTool(BaseExternalTool):
    """专业数据库工具 - 直接集成DataPro + DeepSeek大上下文LLM整理."""

    name: str = "professional_database"
    summary: str = "专业数据库查询, 查A股/港股金融基本面(财务/估值/研报/市值)/企业工商/企业风险等结构化数据"
    search_keywords: ClassVar[list[str]] = [
        # 金融(基本面/结构化数据)
        "金融",
        "股票",
        "基金",
        "债券",
        "期货",
        "期权",
        "财报",
        "财务",
        "ROE",
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
        # 市场覆盖
        "港股",
        # 分析动作
        "对比",
        # 工商
        "工商",
        "企业信息",
        "营业执照",
        "统一信用代码",
        "知识产权",
        "专利",
        "股权",
        "法人",
        "法定代表人",
        "注册资金",
        "注册资本",
        "股东",
        "变更记录",
        # 风险
        "企业风险",
        "司法",
        "诉讼",
        "行政处罚",
        "经营异常",
        "失信",
        "被执行",
        "限制消费",
    ]
    description: str = (
        "专业数据库查询工具, 查询金融/工商/风险等权威结构化数据"
        "(火山引擎DataPro; 金融覆盖A股/港股, 工商/风险覆盖境内注册主体).\n"
        "标的可用公司全称/股票代码/统一信用代码任一; query维度表述决定DataPro返回哪个数据子表, "
        "须写明用户关心的具体维度, 系统自动精炼为高密度摘要.\n\n"
        "数据维度(按query自然语言路由):\n"
        "- 金融: 盈利能力(ROE/ROA/净利率),技术形态(K线/连涨天数),估值(PE/PB),行情(开高低收),财务报表\n"
        "- 工商: 企业登记信息/经营状况/股东与变更记录/知识产权\n"
        "- 风险: 司法诉讼/行政处罚/经营异常/失信\n\n"
        "用法: query = 标的(公司全称/股票代码/统一信用代码) + 具体维度表述"
        "(如ROE盈利水平/K线形态分析/季度业绩/司法诉讼).\n"
        "维度表述决定返回子表: 写'ROE盈利水平'返回盈利指标, 写'K线形态分析'返回行情技术指标, "
        "写'金融数据'这类模糊词会命中无关表. "
        "用户问多个维度时分别调用多次(每次一个维度), 不要合并成模糊query.\n\n"
        '特殊能力: 支持跨企业关联查询(如"A与B的诉讼/商业纠纷"), 返回双方关系分析.\n\n'
        "示例(可用公司名或代码):\n"
        '- 港股估值: {"query": "小米集团 市值与估值水平"}\n'
        '- 港股财务: {"query": "网易 最新季度财务数据"}\n'
        '- A股盈利: {"query": "比亚迪 002594 ROE盈利水平"}\n'
        '- 工商: {"query": "华为技术有限公司 工商信息"}\n'
        '- 风险: {"query": "美团 司法诉讼与风险"}\n'
        '- 多标的对比: {"query": "美团 小米 营收对比"}'
    )
    args_schema: type[BaseModel] = ProfessionalDatabaseInput

    datapro_url: str = Field(
        default="https://datapro.hqd.cn-beijing.volces.com/mcp",
        description="DataPro MCP服务地址",
    )
    api_key_env: str = Field(
        default="ARK_AGENT_PLAN_API_KEY",
        description="DataPro API Key的环境变量名",
    )

    @override
    async def is_available(self) -> bool:
        return bool(_get_datapro_api_key(self.api_key_env))

    @override
    async def _arun(self, query: str) -> str:
        start_time = time.time()

        cache = get_semantic_cache(_DATAPRO_CACHE_COLLECTION)
        cached = await cache.get(query)
        if cached is not None:
            try:
                result = json.loads(cached)
                logger.info("专业数据查询缓存命中: query=%s", query[:50])
                return result.get("result", "")
            except (json.JSONDecodeError, KeyError):
                logger.warning("缓存数据损坏, 降级正常执行")

        sub_queries = _split_query_if_needed(query)
        logger.info("专业数据查询拆分: %d批 (query=%s)", len(sub_queries), query[:50])

        raw_results = await asyncio.gather(
            *[self._call_datapro(q) for q in sub_queries],
            return_exceptions=True,
        )

        processed_results = [
            _preprocess_response(r)
            if isinstance(r, str) and not r.startswith("[查询")
            else r
            for r in raw_results
        ]

        merged = _merge_results(processed_results, query, len(sub_queries) > 1)

        if "error" not in merged and len(merged["result"]) >= _SKIP_DEEPSEEK_THRESHOLD:
            processed = await self._light_process(merged["result"], query)
            if processed:
                merged["result"] = processed

        if "error" not in merged:
            try:
                await cache.put(query, json.dumps(merged, ensure_ascii=False))
            except Exception as e:
                logger.warning("缓存写入异常(不影响结果): %s", e)

        logger.info(
            "专业数据查询完成: %.2fs (query=%s)",
            time.time() - start_time,
            query[:50],
        )
        return merged.get("result", "")

    async def _call_datapro(self, query: str) -> str:
        """独立fastmcp.Client调用DataPro, 程序化非agent."""
        from fastmcp.client import Client
        from fastmcp.client.transports import StreamableHttpTransport

        api_key = _get_datapro_api_key(self.api_key_env)
        if not api_key:
            return f"[配置缺失: 环境变量{self.api_key_env}未设置]"

        headers = {"X-Agent-Plan-Key": api_key}
        transport = StreamableHttpTransport(url=self.datapro_url, headers=headers)
        try:
            async with Client(transport, timeout=_DATAPRO_CALL_TIMEOUT) as client:
                result = await client.call_tool(_DATAPRO_TOOL_NAME, {"query": query})
            return _extract_result_text(result)
        except TimeoutError:
            return f"[查询超时({_DATAPRO_CALL_TIMEOUT}秒)]"
        except Exception as e:
            logger.exception("DataPro调用失败: %s", e)
            return f"[查询失败: {e}]"

    async def _light_process(self, raw_text: str, query: str) -> str | None:
        """DeepSeek大上下文LLM整理: 归类/来源标记, 保留数据确定性.

        失败返回None, 上层降级返回原始数据.
        """
        try:
            from src.inference.usage import usage_source

            llm = ExpertModelFactory.create_for_tool("professional_database")
            messages = [
                SystemMessage(content=_LIGHT_PROCESS_PROMPT),
                HumanMessage(
                    content=(
                        f"用户查询:\n{query}\n\n"
                        f"原始数据:\n{raw_text}\n\n"
                        "请整理成结构化呈现."
                    )
                ),
            ]
            with usage_source("expert_llm"):
                response = await asyncio.wait_for(
                    llm.ainvoke(messages), timeout=_LLM_PROCESS_TIMEOUT
                )
            return content_to_text(response.content)
        except Exception as e:
            logger.warning("LLM整理失败, 降级返回原始数据: %s", e)
            return None


def _extract_result_text(result: Any) -> str:
    """从fastmcp CallToolResult提取文本内容."""
    if isinstance(result, str):
        return result

    content = getattr(result, "content", None)
    if content is None and isinstance(result, (list, tuple)):
        content = result

    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
            elif hasattr(item, "text"):
                texts.append(item.text)
        return "\n".join(texts)

    return str(result) if result else ""


def _get_datapro_api_key(env_name: str) -> str:
    """读取 DataPro API Key."""
    if env_name == "ARK_AGENT_PLAN_API_KEY":
        return get_credential("ark_agent_plan_api_key")
    return ""


# =============================================================================
# 响应预处理: 结构驱动信息密度提取
# 重要事实(标量)全貌保留 + list明细去null/统计/预算填充
# =============================================================================


def _preprocess_response(raw_text: str) -> str:
    """DataPro响应预处理: 结构识别 + 去冗余 + 重要事实全貌 + 明细预算填充.

    目标: 单企业~8K高密度文本(匹配DeepSeek输入预算).
    金融(小)经此处理后通常<阈值, 跳过DeepSeek直接返回.
    """
    data = _try_decode_json(raw_text)
    if not isinstance(data, dict):
        return raw_text[:8000]

    items = data.get("items")
    if not isinstance(items, list) or not items:
        return raw_text[:8000]

    parts = [_response_header(data)]
    for item in items:
        if isinstance(item, dict):
            entity = _extract_entity(item)
            if entity:
                parts.append(entity)
    return "\n\n".join(parts)


def _try_decode_json(text: str) -> Any:
    """解码JSON, 兼容单层/双层JSON字符串."""
    current: Any = text.strip()
    for _ in range(2):
        if not isinstance(current, str):
            return current
        stripped = current.strip()
        if not stripped:
            return current
        try:
            current = json.loads(stripped)
        except json.JSONDecodeError:
            return current
    return current


def _response_header(data: dict[str, Any]) -> str:
    lines = ["[专业数据查询结果]"]
    if data.get("query"):
        lines.append(f"查询: {data['query']}")
    if data.get("trace_id"):
        lines.append(f"trace_id: {data['trace_id']}")
    total = data.get("total", len(data.get("items") or []))
    lines.append(f"结果数: {total}")
    return "\n".join(lines)


def _extract_entity(item: dict[str, Any]) -> str:
    """单企业提取: 重要事实标量全貌 + list统计+明细预算填充."""
    scalars, lists = _classify_fields(item)
    name = _extract_entity_name(scalars, lists)

    lines = [f"■ {name}"]

    facts = [(k, v) for k, v in scalars.items() if not _is_internal_key(k)]
    if facts:
        lines.append("  基本信息:")
        for k, v in facts[:20]:
            lines.append(f"    {k}: {_scalar_preview(v)}")

    if lists:
        lines.append("  明细:")
        for k, lst in lists.items():
            lines.extend(_format_list(k, lst))

    return "\n".join(lines)


def _classify_fields(
    item: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, list]]:
    """分标量事实与list明细. JSON字符串标量(如工商变更记录)解析为list."""
    scalars: dict[str, Any] = {}
    lists: dict[str, list] = {}
    for k, v in item.items():
        if v is None:
            continue
        if isinstance(v, list):
            lists[k] = v
        elif isinstance(v, dict):
            lists[k] = [v]
        elif isinstance(v, str) and _is_json_list_field(k, v):
            parsed = _try_decode_json_list(v)
            if parsed is not None:
                lists[k] = parsed
            else:
                scalars[k] = v
        else:
            scalars[k] = v
    return scalars, lists


def _is_json_list_field(key: str, value: str) -> bool:
    """判断是否JSON字符串list字段(如工商变更记录/主要人员记录)."""
    if "(JSON字符串)" in key or "记录" in key:
        return value.lstrip().startswith("[")
    return False


def _try_decode_json_list(value: str) -> list | None:
    """尝试解析JSON字符串为list."""
    data = _try_decode_json(value)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return None


def _extract_entity_name(scalars: dict[str, Any], lists: dict[str, list]) -> str:
    """提取企业名称: 标量优先, 风险数据(无顶层名称)从list子项找."""
    for k in ("公司名称", "企业名称", "纳税人名称", "证券代码", "查询代码"):
        if scalars.get(k):
            return str(scalars[k])
    for lst in lists.values():
        for sub in lst:
            if isinstance(sub, dict):
                for nk in ("企业名称", "公司名称", "当事人"):
                    if sub.get(nk):
                        return str(sub[nk])
    return "企业"


def _is_internal_key(key: str) -> bool:
    """内部字段判断(精确黑名单 + 关联主键/首字母模式)."""
    return key in _INTERNAL_KEYS or "关联主键" in key or "首字母" in key


def _scalar_preview(v: Any) -> str:
    """标量值预览: 单元素list解包(金融指标[1.65]→1.65), <=600字全貌保留."""
    if isinstance(v, list) and len(v) == 1:
        v = v[0]
    s = str(v)
    if len(s) <= _SCALAR_FULL_LIMIT:
        return s
    return s[:_SCALAR_FULL_LIMIT] + "..."


def _format_list(key: str, lst: list) -> list[str]:
    """list格式化: 清理键名 + 总数(有效数) + 类型分布 + 前N明细."""
    clean_key = (
        key
        .replace("(JSON字符串)", "")
        .replace("（关联主键）", "")  # noqa: RUF001
        .replace("(关联主键)", "")
    )
    if not lst:
        return [f"    {clean_key}: 0项"]

    clean = [_strip_nulls(x) for x in lst if isinstance(x, dict)]
    clean = [x for x in clean if x]

    if len(clean) == 1 and _is_indicator_dict(clean[0]):
        fields = [
            (k, v)
            for k, v in clean[0].items()
            if not _is_internal_key(k) and v is not None
        ]
        lines = [f"    {clean_key}({len(fields)}个指标):"]
        for k, v in fields:
            lines.append(f"      {k}: {_scalar_preview(v)}")
        return lines

    header = f"    {clean_key}: 共{len(lst)}项"
    if len(clean) != len(lst):
        header += f"(有效{len(clean)})"
    lines = [header]

    dist = _type_distribution(clean)
    if dist:
        lines.append(f"      分布: {dist}")

    for sub in clean[:_MAX_LIST_DETAIL]:
        summary = _subitem_summary(sub)
        if summary:
            lines.append(f"      - {summary}")
    return lines


def _strip_nulls(d: dict[str, Any]) -> dict[str, Any]:
    """去除null/空字段(含[None]等全空list, 风险/工商/金融子项null比例高)."""
    result: dict[str, Any] = {}
    for k, v in d.items():
        if v is None or (isinstance(v, list) and all(x is None for x in v)):
            continue
        result[k] = v
    return result


def _is_indicator_dict(d: dict[str, Any]) -> bool:
    """指标集合型dict(如金融table): 字段多(>=10)且值都是标量或单元素list.

    区别于风险/工商的多字段子项: 那些是单条记录的多属性,
    table是单实体的独立指标集合, 应全展开而非按子项限字段数.
    """
    if len(d) < 10:
        return False
    return all(
        not isinstance(v, (dict, list)) or (isinstance(v, list) and len(v) <= 1)
        for v in d.values()
    )


def _subitem_summary(sub: dict[str, Any]) -> str:
    """子项摘要: 去null后取前N有效字段."""
    fields = [(k, v) for k, v in sub.items() if not _is_internal_key(k)]
    parts = [f"{k}: {_scalar_preview(v)}" for k, v in fields[:_MAX_SUBITEM_FIELDS]]
    return " | ".join(parts)


def _type_distribution(items: list[dict]) -> str:
    """list子项的类型分布(风险类型/案件类型等), 字段找不到返回空."""
    if not items:
        return ""
    type_key = ""
    for cand in (
        "风险类型描述",
        "案件类型。",  # noqa: RUF001
        "案件类型",
        "知识产权类型(关联主键)",
        "知识产权类型",
        "裁判文书类型。",  # noqa: RUF001
        "公告类型大类",
    ):
        if items[0].get(cand):
            type_key = cand
            break
    if not type_key:
        return ""
    counts: dict[str, int] = {}
    for x in items:
        t = str(x.get(type_key) or "其他")[:20]
        counts[t] = counts.get(t, 0) + 1
    return ", ".join(f"{k}{v}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))


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


def _merge_results(
    raw_results: list[Any],
    query: str,
    multi_batch: bool,
) -> dict[str, Any]:
    """合并多次调用结果."""
    valid = [r for r in raw_results if isinstance(r, str) and not r.startswith("[查询")]
    failed = [r for r in raw_results if isinstance(r, str) and r.startswith("[查询")]

    if not valid and failed:
        return {"result": failed[0], "query": query, "error": "all_batches_failed"}

    if not multi_batch:
        return {"result": valid[0] if valid else "", "query": query}

    parts = ["[专业数据查询结果(分批合并)]", f"查询: {query}", ""]
    for idx, text in enumerate(valid, 1):
        parts.append(f"--- 批次 {idx} ---")
        parts.append(text)
        parts.append("")
    if failed:
        parts.append(f"[失败批次 {len(failed)} 个]")
    return {"result": "\n".join(parts), "query": query}


__all__ = ["ProfessionalDatabaseInput", "ProfessionalDatabaseTool"]
