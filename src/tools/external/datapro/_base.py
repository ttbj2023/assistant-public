"""DataPro 领域工具共享运行时 - 组合模式, 非继承.

DataProClient: DataPro 调用 + LLM 轻加工 (各工具实例化时组合持有).
模块级函数: 响应预处理 / 结果合并 (纯函数, 无状态).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.config.credentials_registry import get_credential
from src.inference.llm.response_utils import content_to_text
from src.tools.experts.model_factory import ExpertModelFactory
from src.tools.shared.semantic_cache import get_semantic_cache

logger = logging.getLogger(__name__)

_DATAPRO_TOOL_NAME = "dataPro_search"
_DATAPRO_CALL_TIMEOUT = 60.0
_LLM_PROCESS_TIMEOUT = 60.0
_SKIP_DEEPSEEK_THRESHOLD = 4000
_SCALAR_FULL_LIMIT = 600
_MAX_LIST_DETAIL = 8
_MAX_SUBITEM_FIELDS = 8
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


class DataProClient:
    """DataPro 调用客户端 + LLM 轻加工. 各工具组合持有."""

    def __init__(
        self,
        *,
        datapro_url: str = "https://datapro.hqd.cn-beijing.volces.com/mcp",
        api_key_env: str = "ARK_AGENT_PLAN_API_KEY",
        cache_collection: str = "datapro_cache",
        llm_prompt: str = "",
        skip_deepseek_threshold: int = _SKIP_DEEPSEEK_THRESHOLD,
    ) -> None:
        self.datapro_url = datapro_url
        self.api_key_env = api_key_env
        self.cache_collection = cache_collection
        self.llm_prompt = llm_prompt
        self.skip_deepseek_threshold = skip_deepseek_threshold

    def is_available(self) -> bool:
        return bool(_get_datapro_api_key(self.api_key_env))

    async def execute(
        self,
        tool_name: str,
        query: str,
        *,
        split_fn: Any = None,
    ) -> str:
        """完整查询流程: 缓存 → 调用 → 预处理 → LLM → 写缓存."""
        start_time = time.time()

        cache = get_semantic_cache(self.cache_collection)
        cached = await cache.get(query)
        if cached is not None:
            try:
                result = json.loads(cached)
                logger.info("%s 缓存命中: query=%s", tool_name, query[:50])
                return result.get("result", "")
            except (json.JSONDecodeError, KeyError):
                logger.warning("缓存数据损坏, 降级正常执行")

        sub_queries = split_fn(query) if split_fn else [query]
        raw_results = await asyncio.gather(
            *[self._call_datapro(q) for q in sub_queries],
            return_exceptions=True,
        )

        processed_results = [
            preprocess_response(r)
            if isinstance(r, str) and not r.startswith("[查询")
            else r
            for r in raw_results
        ]

        merged = merge_results(processed_results, query, len(sub_queries) > 1)

        if (
            "error" not in merged
            and len(merged["result"]) >= self.skip_deepseek_threshold
        ):
            processed = await self._light_process(merged["result"], query)
            if processed:
                merged["result"] = processed

        if "error" not in merged:
            try:
                await cache.put(query, json.dumps(merged, ensure_ascii=False))
            except Exception as e:
                logger.warning("缓存写入异常(不影响结果): %s", e)

        logger.info(
            "%s 查询完成: %.2fs (query=%s)",
            tool_name,
            time.time() - start_time,
            query[:50],
        )
        return merged.get("result", "")

    async def _call_datapro(self, query: str) -> str:
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
        try:
            from src.inference.usage import usage_source

            llm = ExpertModelFactory.create_for_tool("datapro")
            messages = [
                SystemMessage(content=self.llm_prompt),
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


def _get_datapro_api_key(env_name: str) -> str:
    if env_name == "ARK_AGENT_PLAN_API_KEY":
        return get_credential("ark_agent_plan_api_key")
    return ""


def _extract_result_text(result: Any) -> str:
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


# =============================================================================
# 响应预处理: 结构驱动信息密度提取 (纯函数, 无状态)
# =============================================================================


def preprocess_response(raw_text: str) -> str:
    """DataPro响应预处理: 结构识别 + 去冗余 + 重要事实全貌 + 明细预算填充."""
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


def merge_results(
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


def _try_decode_json(text: str) -> Any:
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
    if "(JSON字符串)" in key or "记录" in key:
        return value.lstrip().startswith("[")
    return False


def _try_decode_json_list(value: str) -> list | None:
    data = _try_decode_json(value)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return None


def _extract_entity_name(scalars: dict[str, Any], lists: dict[str, list]) -> str:
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
    return key in _INTERNAL_KEYS or "关联主键" in key or "首字母" in key


def _scalar_preview(v: Any) -> str:
    if isinstance(v, list) and len(v) == 1:
        v = v[0]
    s = str(v)
    if len(s) <= _SCALAR_FULL_LIMIT:
        return s
    return s[:_SCALAR_FULL_LIMIT] + "..."


def _format_list(key: str, lst: list) -> list[str]:
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
    result: dict[str, Any] = {}
    for k, v in d.items():
        if v is None or (isinstance(v, list) and all(x is None for x in v)):
            continue
        result[k] = v
    return result


def _is_indicator_dict(d: dict[str, Any]) -> bool:
    if len(d) < 10:
        return False
    return all(
        not isinstance(v, (dict, list)) or (isinstance(v, list) and len(v) <= 1)
        for v in d.values()
    )


def _subitem_summary(sub: dict[str, Any]) -> str:
    fields = [(k, v) for k, v in sub.items() if not _is_internal_key(k)]
    parts = [f"{k}: {_scalar_preview(v)}" for k, v in fields[:_MAX_SUBITEM_FIELDS]]
    return " | ".join(parts)


def _type_distribution(items: list[dict]) -> str:
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
