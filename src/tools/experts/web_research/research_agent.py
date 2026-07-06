"""Deep模式研究Agent - 基于Gemini初步结果的多渠道迭代研究.

核心流程:
1. 接收Gemini Grounding初步结果和用户查询
2. LangChain Agent自主决策补充搜索/抓取策略 (3-4轮)
3. 输出信息密度优先的结构化研究摘要 (供上层Agent综合使用)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from src.config.retry_config import get_retry_config
from src.inference.llm.response_utils import content_to_text
from src.inference.llm.retry_predicates import (
    format_llm_failure_message,
    is_retryable_llm_exception,
)
from src.tools.experts.agent_utils import (
    enable_tool_error_handling,
    extract_tool_calls,
)
from src.tools.experts.model_factory import ExpertModelFactory

logger = logging.getLogger(__name__)

_DEPTH_STRATEGY = (
    "## 研究模式: 深度研究\n"
    "- 你已获得Google搜索初步结果, 识别需要补充或验证的关键方面\n"
    "- 如果已获得URL Context页面证据, 优先利用其中带citation的信息\n"
    "- 搜索控制在3-4轮以内, 每轮1-2个查询\n"
    "- 避免重复搜索已有答案的问题\n"
    "- 对关键页面用fetch_webpage深入阅读, 获取具体数据和技术细节\n"
    "- 如信息已足够, 直接按输出格式综合, 不再继续搜索"
)

_WEB_RESEARCH_SYSTEM_PROMPT = (
    "你是一个深度研究助手. 你的输出是中间研究结果, 供上层Agent综合使用.\n\n"
    "**已有信息**: 若提供了Google搜索初步结果则参考, 否则直接用工具研究.\n"
    "**你的任务**: 从其他搜索渠道补充和验证信息, 深入关键页面获取详情.\n"
    "如已有URL Context页面证据, 只有在信息缺失或相互矛盾时才重复抓取同一URL.\n\n"
    "可用工具:\n"
    "- doubao_search: 豆包网络搜索(通用主力, 召回质量高, 含官方文档/权威源, 摘要详尽)\n"
    "- baidu_search: 百度AI搜索(时效性最强, 适合最新新闻/热点/实时事件)\n"
    "- academic_search: 学术论文搜索(权威研究支撑, 返回论文标题/作者/引用数/摘要)\n"
    "- fetch_webpage: 抓取网页正文\n"
    "- zhipu_reader: 智谱网页阅读器(支持JS渲染)\n\n"
    "**搜索约束**:\n"
    "- 最多3-4轮搜索, 不要反复搜索同一问题\n"
    "- 如果初步结果已足够, 直接综合, 不要为搜索而搜索\n"
    "- 优先深入阅读2-3个关键页面, 而非广撒网浅搜索\n"
    "- 交叉验证关键数据(数字,日期,对比结论)\n\n"
    "**工具选择策略**:\n"
    "- 搜索引擎: 默认用 doubao_search; 查最新新闻/热点/实时动态(今天/刚刚/最新)时用 baidu_search\n"
    "- 学术研究: 需要权威论文/学术支撑(算法原理/科学研究/技术综述)时用 academic_search\n"
    "- 关键事实(数字/日期/对比)可用两路交叉验证\n"
    "- fetch_webpage返回skipped或failed时, 对同一URL改用 zhipu_reader 重试\n"
    "- 不要对同一URL反复使用相同且已失败的工具\n\n"
    "**输出格式** (严格遵循):\n"
    "### 核心发现\n"
    "- [事实1]: 具体数据/结论\n"
    "- [事实2]: 具体数据/结论\n\n"
    "### 详细信息\n"
    "[按主题组织的关键细节, 包含具体数据,对比,技术参数等原始信息]\n\n"
    "### 参考来源\n"
    "- [标题](URL)\n\n"
    "### 未确认项\n"
    "- [未能验证或找到的信息, 如有]"
)


def _create_research_prompt(
    query: str,
    language: str,
    *,
    grounding_context: dict[str, Any] | None = None,
    url_context_context: dict[str, Any] | None = None,
) -> str:
    lang_instruction = "用中文回答" if language == "zh" else "respond in English"
    parts: list[str] = []

    if grounding_context:
        answer = grounding_context.get("answer", "")
        sources = grounding_context.get("sources", [])
        search_queries = grounding_context.get("search_queries", [])
        if answer:
            parts.append(f"## Google搜索初步结果\n{answer}")
        if sources:
            src = "\n".join(f"- 来源: {s['domain']}" for s in sources)
            parts.append(f"初步来源(域名):\n{src}")
        if search_queries:
            qs = "\n".join(f"- {q}" for q in search_queries)
            parts.append(
                "Google用过的搜索词(补充来源时可直接拿这些去 doubao/baidu 搜"
                "以获取真实可点击URL):\n" + qs
            )
    else:
        # 未提供 Google 预处理结果(Grounding 不可用), 提示 Agent 直接用工具
        parts.append(
            "## 说明\n本次未获得 Google 搜索初步结果, 请直接用 doubao_search/"
            "baidu_search 等工具完成研究."
        )

    if url_context_context:
        answer = url_context_context.get("answer", "")
        sources = url_context_context.get("sources", [])
        retrievals = url_context_context.get("retrievals", [])
        if answer:
            parts.append(f"## URL Context页面证据\n{answer}")
        if sources:
            src = "\n".join(f"- [{s['title']}]({s['url']})" for s in sources)
            parts.append(f"URL Context引用来源:\n{src}")
        if retrievals:
            rows = "\n".join(
                f"- {item.get('url', '')}: {item.get('status', '')}"
                for item in retrievals
            )
            parts.append(f"URL Context抓取状态:\n{rows}")

    parts.append(
        f"研究问题: {query}\n\n{_DEPTH_STRATEGY}\n\n{lang_instruction}. "
        f"严格按系统提示中的输出格式返回, 聚焦原始信息密度, 不要写成文章.",
    )
    return "\n\n".join(parts)


class ResearchAgent:
    """Deep模式研究Agent - 基于grounding结果的多渠道迭代研究."""

    def __init__(
        self,
        model_id: str = "",
        tools: list[BaseTool] | None = None,
        timeout: float = 360.0,
        llm_request_timeout: float = 90.0,
    ) -> None:
        self.model_id = model_id
        self.tools = tools or []
        self.timeout = timeout
        self.llm_request_timeout = llm_request_timeout
        self._agent: Any | None = None

    def _get_llm(self) -> BaseChatModel:
        return ExpertModelFactory.create_for_tool("web_research")

    def _get_or_create_agent(self) -> Any:
        if self._agent is not None:
            return self._agent

        llm = self._get_llm()
        retry_cfg = get_retry_config().expert_agent
        agent = create_agent(
            llm,
            self.tools,
            system_prompt=_WEB_RESEARCH_SYSTEM_PROMPT,
            middleware=[
                ModelRetryMiddleware(
                    max_retries=retry_cfg.max_retries,
                    retry_on=is_retryable_llm_exception,
                    on_failure=format_llm_failure_message,
                    initial_delay=retry_cfg.initial_delay,
                    max_delay=retry_cfg.max_delay,
                ),
            ],
        )
        enable_tool_error_handling(agent)
        self._agent = agent
        return agent

    async def research(
        self,
        query: str,
        language: str = "zh",
        *,
        grounding_context: dict[str, Any] | None = None,
        url_context_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行深度研究."""
        start_time = time.time()
        try:
            agent = self._get_or_create_agent()

            prompt = _create_research_prompt(
                query,
                language,
                grounding_context=grounding_context,
                url_context_context=url_context_context,
            )
            messages = [HumanMessage(content=prompt)]

            result = await asyncio.wait_for(
                agent.ainvoke(
                    {"messages": messages},
                    config=RunnableConfig(max_concurrency=1),
                ),
                timeout=self.timeout,
            )

            tool_calls_made = extract_tool_calls(result["messages"])
            final_message = result["messages"][-1]
            raw_answer = content_to_text(
                final_message.content
                if hasattr(final_message, "content")
                else str(final_message)
            )

            elapsed = time.time() - start_time
            return {
                "result": raw_answer,
                "query": query,
                "depth": "deep",
                "language": language,
                "elapsed_seconds": round(elapsed, 2),
                "tools_used": list(set(tool_calls_made)),
            }

        except TimeoutError:
            elapsed = time.time() - start_time
            logger.warning(f"Deep研究Agent执行超时({self.timeout}s)")
            return {
                "result": f"研究操作超时({self.timeout}秒), 请尝试缩小查询范围.",
                "query": query,
                "depth": "deep",
                "language": language,
                "elapsed_seconds": round(elapsed, 2),
                "error": "timeout",
            }
        except Exception as e:
            elapsed = time.time() - start_time
            logger.exception("Deep研究Agent执行失败: %s", e)
            return {
                "result": f"研究过程出错: {e!s}",
                "query": query,
                "depth": "deep",
                "language": language,
                "elapsed_seconds": round(elapsed, 2),
                "error": str(e),
            }


_agent_instance: ResearchAgent | None = None
_agent_tools_key: str | None = None


def get_research_agent(
    model_id: str = "",
    tools: list[BaseTool] | None = None,
    timeout: float = 360.0,
    llm_request_timeout: float = 90.0,
) -> ResearchAgent:
    """获取全局ResearchAgent单例(按tools名称集合做key自动重建)."""
    global _agent_instance, _agent_tools_key

    tools_list = tools or []
    current_key = ",".join(sorted(t.name for t in tools_list))

    if _agent_instance is not None and _agent_tools_key == current_key:
        return _agent_instance

    _agent_instance = ResearchAgent(
        model_id=model_id,
        tools=tools_list,
        timeout=timeout,
        llm_request_timeout=llm_request_timeout,
    )
    _agent_tools_key = current_key
    return _agent_instance
