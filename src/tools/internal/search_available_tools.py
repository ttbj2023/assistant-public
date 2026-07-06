"""工具发现工具 - 帮助Agent动态发现和加载休眠工具.

作为工具发现系统的入口, Agent启动时只加载核心工具和此工具.
当Agent需要特定功能时, 调用此工具搜索匹配的休眠工具,
中间件会拦截搜索结果并将匹配的工具注入后续模型调用中.

匹配算法: 多信号加权评分 + token命中率过滤(召回优先) + 降序排序.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, override

from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal._search_synonyms import BUILTIN_SYNONYMS
from src.tools.shared.base_internal_tool import BaseInternalTool

logger = logging.getLogger(__name__)


class SearchAvailableToolsRequest(BaseModel):
    """搜索可用工具请求模型."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    query: str = Field(
        default="",
        description="搜索关键词, 描述你需要的功能",
    )


class SearchAvailableTools(BaseInternalTool):
    """搜索可用工具 - 帮助Agent发现休眠工具.

    当Agent需要使用某个功能但当前没有对应工具时,
    调用此工具搜索匹配的可用工具. 搜索结果返回后,
    中间件会自动将匹配的工具注入到后续的模型调用中.

    使用示例:
    - search_available_tools(query="发送消息")
    - search_available_tools(query="微信 定时")
    - search_available_tools(query="research")
    """

    name: str = "search_available_tools"
    summary: str = "搜索可用的休眠工具, 按需发现并激活"
    description: str = """搜索可用的休眠工具. 当你需要某个功能但当前工具列表中没有时, 使用此工具搜索.

搜索后匹配的工具会自动加载到你的工具列表中, 你可以直接调用它们.

示例:
- query="发送消息" - 搜索消息相关工具
- query="微信 定时" - 搜索定时/微信相关工具
"""
    args_schema: type[SearchAvailableToolsRequest] = SearchAvailableToolsRequest

    def __init__(self, user_id: str = "", thread_id: str = "", **kwargs: Any) -> None:
        super().__init__(user_id, thread_id, **kwargs)
        self._catalog: dict[str, dict[str, str]] = {}

    def set_catalog(self, catalog: dict[str, dict[str, str]]) -> None:
        """设置实例级工具目录, 由InferenceCoordinator在组装工具集时调用.

        Args:
            catalog: {tool_name: {"name": ..., "description": ...}} 格式的工具目录

        """
        self._catalog = catalog
        logger.info(f"工具目录已设置: {list(catalog.keys())}")

    @override
    async def _arun(self, query: str = "") -> str:
        """搜索休眠工具目录, 返回匹配结果.

        Args:
            query: 搜索关键词

        Returns:
            JSON格式的匹配工具列表

        """
        if not query or not query.strip():
            return self._format_all_tools()

        results = self._search_catalog(query.strip())

        if not results:
            return json.dumps(
                {
                    "success": True,
                    "message": f"未找到与 '{query}' 匹配的工具",
                    "matched_tools": [],
                    "available_categories": [
                        info.get("display_label", name)
                        for name, info in self._catalog.items()
                    ],
                },
                ensure_ascii=False,
            )

        # LLM 降噪: 当匹配 >= 2 个工具时, 调用本地小模型去除无关工具
        if len(results) >= 2:
            from src.tools.internal._llm_tool_filter import filter_tools_by_llm

            results = await filter_tools_by_llm(query.strip(), results)

        # 展开组条目为成员工具条目(组名对主对话模型透明, 只暴露真实成员名)
        matched = self._expand_members(results)

        logger.info(f"工具搜索: query='{query}', 匹配 {len(matched)} 个工具")
        return json.dumps(
            {
                "success": True,
                "message": f"找到 {len(matched)} 个匹配工具",
                "matched_tools": matched,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _search_catalog(self, query: str) -> list[dict[str, str]]:
        """在工具目录中搜索匹配的工具.

        使用多信号评分 + token命中率过滤 + 降序排序.

        Args:
            query: 搜索关键词

        Returns:
            匹配的工具信息列表(按相关性降序)

        """
        tokens, query_lower = self._tokenize(query)
        total_tokens = len(tokens)

        scored: list[tuple[float, dict[str, str]]] = []
        for tool_name, tool_info in self._catalog.items():
            s, matched, has_name_hit = self._score(
                tokens,
                query_lower,
                tool_name,
                tool_info,
            )
            if s <= 0:
                continue

            # token命中率过滤 (召回优先: 宽松阈值, 宁可多匹配不漏)
            if total_tokens > 2:
                hit_ratio = matched / total_tokens
                if hit_ratio < 0.2 and not has_name_hit:
                    continue

            scored.append((s, tool_info))

        # 降序排序
        scored.sort(key=lambda x: x[0], reverse=True)

        return [info for _, info in scored]

    @staticmethod
    def _tokenize(query: str) -> tuple[list[str], str]:
        """查询预处理: 空格+下划线分词, 保持中文片段完整.

        Args:
            query: 原始查询字符串

        Returns:
            (tokens列表, 小写原始查询)

        """
        query_lower = query.lower().strip()
        tokens: list[str] = []
        for part in query_lower.split():
            for sub in part.split("_"):
                sub = sub.strip()
                if sub:
                    tokens.append(sub)
        if not tokens and query_lower:
            tokens = [query_lower]
        return tokens, query_lower

    @staticmethod
    def _score(
        query_tokens: list[str],
        query_original: str,
        tool_name: str,
        tool_info: dict,
    ) -> tuple[float, int, bool]:
        """多信号加权评分.

        对每个 query token, 按优先级检查匹配信号:
        1. 工具名精确匹配 (10.0)
        2. 名称片段精确匹配 (5.0) / 子串匹配 (4.0)
        3. 关键词匹配 (4.0)
        4. summary 子串匹配 (3.0)
        5. full_description 子串匹配 (2.0)
        6. 同义词扩展匹配 (2.0)
        7. 中文 ngram 匹配 (1.0~3.0)

        Args:
            query_tokens: 预分词后的 token 列表
            query_original: 小写原始查询
            tool_name: 工具名称
            tool_info: 工具信息字典

        Returns:
            (总分, 命中token数, 是否有name_parts命中)

        """
        name_lower = tool_name.lower()
        name_parts = tool_info.get("name_parts", [])
        summary = tool_info.get("description", "").lower()
        full_desc = tool_info.get("full_description", "").lower()
        keywords = [k.lower() for k in tool_info.get("keywords", [])]

        # 优先检查: 完整 query 精确匹配工具名
        if query_original == name_lower:
            return 10.0, len(query_tokens), True

        total_score = 0.0
        matched = 0
        has_name_hit = False

        for token in query_tokens:
            if not token:
                continue

            best = 0.0

            # 信号 1: 名称片段精确匹配
            if token in name_parts:
                best = 5.0
                has_name_hit = True
            # 信号 1b: 名称片段子串匹配 (token 是 name_part 的子串)
            elif any(token in part for part in name_parts):
                best = max(best, 4.0)
                has_name_hit = True

            # 信号 2: 关键词匹配
            if best < 4.0:
                for kw in keywords:
                    if token in kw or kw in token:
                        best = max(best, 4.0)
                        break

            # 信号 3: summary 子串
            if best < 3.0 and token in summary:
                best = max(best, 3.0)

            # 信号 4: full_description 子串
            if best < 2.0 and full_desc and token in full_desc:
                best = max(best, 2.0)

            # 信号 5: 同义词扩展匹配
            if best < 2.0 and token in BUILTIN_SYNONYMS:
                syns = BUILTIN_SYNONYMS[token]
                combined = f"{name_lower} {summary}"
                for syn in syns:
                    if syn in combined:
                        best = max(best, 2.0)
                        break

            # 信号 6: 中文 ngram (仅当其他信号未命中)
            if math.isclose(best, 0.0, abs_tol=1e-9) and _has_chinese(token):
                best = _ngram_score(token, summary, full_desc, keywords)

            if best > 0:
                total_score += best
                matched += 1

        return total_score, matched, has_name_hit

    def _expand_members(self, results: list[dict]) -> list[dict[str, str]]:
        """展开组条目为成员工具条目.

        catalog 中组条目携带 _members 内部字段(成员工具名+描述). 本方法将其
        展开为多个独立成员条目, 使返回给主对话模型的 matched_tools 只含真实
        成员工具名, 组名对模型完全透明. 非组条目原样透传.

        Args:
            results: catalog 匹配结果(可能含组条目)

        Returns:
            展开后的工具条目列表(仅成员/个体工具)
        """
        expanded: list[dict[str, str]] = []
        for info in results:
            members = info.get("_members")
            if members:
                expanded.extend(
                    {"name": m["name"], "description": m.get("description", "")}
                    for m in members
                )
            else:
                expanded.append({
                    "name": info["name"],
                    "description": info.get("description", ""),
                })
        return expanded

    def _format_all_tools(self) -> str:
        """返回所有可用工具的简要列表(组条目展开为成员)."""
        if not self._catalog:
            return json.dumps(
                {
                    "success": True,
                    "message": "当前没有可发现的工具",
                    "matched_tools": [],
                },
                ensure_ascii=False,
            )

        # 展开组条目为成员, 使返回结果只含真实成员工具名
        entries = self._expand_members(list(self._catalog.values()))
        tools = [
            {"name": e["name"], "description": e.get("description", "")[:100]}
            for e in entries
        ]
        return json.dumps(
            {
                "success": True,
                "message": f"共 {len(tools)} 个可发现的工具",
                "matched_tools": tools,
            },
            ensure_ascii=False,
            indent=2,
        )


def _has_chinese(text: str) -> bool:
    """检查文本是否包含中文字符."""
    return any("一" <= ch <= "鿿" for ch in text)


def _ngram_score(
    token: str,
    summary: str,
    full_desc: str,
    keywords: list[str],
) -> float:
    """中文 ngram 匹配评分 (2-3gram).

    仅在高级信号全部未命中时调用.
    按 ngram 命中位置加权: summary=3.0, full_desc=2.0, keywords=4.0.

    Args:
        token: 中文 token
        summary: 小写 summary
        full_desc: 小写完整描述
        keywords: 小写关键词列表

    Returns:
        最佳 ngram 分数

    """
    best = 0.0
    for n in range(2, min(4, len(token)) + 1):
        for i in range(len(token) - n + 1):
            sub = token[i : i + n]
            if not _has_chinese(sub):
                continue
            if sub in summary:
                best = max(best, 3.0)
            elif full_desc and sub in full_desc:
                best = max(best, 2.0)
            elif any(sub in kw for kw in keywords):
                best = max(best, 4.0)
    return best


__all__ = ["SearchAvailableTools"]
