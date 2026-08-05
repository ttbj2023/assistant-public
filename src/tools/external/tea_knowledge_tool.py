"""茶领域知识库工具 - 外部工具, 检索本地茶专业文料库(书籍/论文/评测等).

定位: 无状态外部工具, 检索由 build_knowledge_base.py 离线构建的茶领域向量库.
数据流: query → 语义缓存 → dense 召回 + top-K + 出处 + 字符预算 → 返回主对话.

触发定位: 用户询问茶领域问题时, 本工具提供比网络搜索更权威的来源,
description/summary/search_keywords 全部聚焦茶语义, 在工具发现阶段被优先召回.
"""

from __future__ import annotations

import logging
from typing import ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.config.inference_config import get_config as get_inference_config
from src.inference.rerank.client import RerankClient
from src.knowledge_base.retriever import KnowledgeBaseRetriever
from src.knowledge_base.store import KnowledgeBaseStore
from src.tools.shared.semantic_cache import get_semantic_cache
from src.tools.shared.tool_runtime import sync_runnable

logger = logging.getLogger(__name__)

_KB_CACHE_COLLECTION = "knowledge_base_cache"


class TeaKnowledgeInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    query: str = Field(
        description="查询语句: 用自然语言描述想了解的茶领域问题, 如'龙井茶的冲泡水温'."
    )
    doc_type: str | None = Field(
        default=None,
        description=(
            "可选, 仅检索指定类型资料: book(书籍)/paper(论文)/review(评测)/article(文章)."
            "不填则检索全部类型."
        ),
    )


@sync_runnable
class TeaKnowledgeTool(BaseTool):
    """茶领域知识库工具 - 检索离线索引的茶专业文料向量库."""

    name: str = "tea_knowledge"
    summary: str = "茶领域知识库查询, 检索本地茶权威资料(中国茶经等书籍/论文/审评), 比网络搜索更可靠"
    search_keywords: ClassVar[list[str]] = [
        "茶",
        "茶叶",
        "茶树",
        "茶经",
        "绿茶",
        "红茶",
        "普洱",
        "龙井",
        "乌龙茶",
        "白茶",
        "黄茶",
        "黑茶",
        "花茶",
        "岩茶",
        "单丛",
        "铁观音",
        "冲泡",
        "审评",
        "茶艺",
        "茶道",
        "茶文化",
        "茶史",
        "茶多酚",
        "儿茶素",
        "咖啡因",
        "茶园",
        "制茶",
        "发酵",
        "杀青",
    ]
    description: str = (
        "茶领域知识库查询工具, 检索本地整理的茶权威资料"
        "(中国茶经等专业书籍/学术论文/审评报告等), 内容比互联网搜索更可靠, "
        "回答茶领域问题时应优先使用本工具而非网络搜索.\n"
        "覆盖: 茶史/茶树品种/栽培与制茶工艺/茶叶品质化学/冲泡与审评/茶文化/茶经济.\n\n"
        "用法: query 用自然语言描述问题; 可选 doc_type 限定资料类型"
        "(book/paper/review/article).\n"
        "返回内容自带出处(标题/类型/作者/章节), 引用时请保留.\n\n"
        "示例:\n"
        '- {"query": "龙井茶属于哪类茶, 产地在哪"}\n'
        '- {"query": "茶树的主要品种有哪些"}\n'
        '- {"query": "绿茶冲泡水温", "doc_type": "review"}'
    )
    args_schema: type[BaseModel] = TeaKnowledgeInput

    kb_name: str = Field(default="tea", description="知识库名称(对应离线索引的库)")
    persist_directory: str = Field(
        default="",
        description="向量库目录, 空=使用默认 BASE_DATA_PATH/_knowledge_base/<kb_name>",
    )
    top_k: int = Field(default=8, description="召回块数")
    budget_chars: int = Field(default=4000, description="返回文本字符预算上限")

    async def is_available(self) -> bool:
        """知识库已索引(目录非空)时可用; 廉价检查不加载 embedding."""
        if self.persist_directory:
            return KnowledgeBaseStore.directory_has_data(self.persist_directory)
        return KnowledgeBaseStore.has_data(self.kb_name)

    @override
    async def _arun(self, query: str, doc_type: str | None = None) -> str:
        cache = get_semantic_cache(_KB_CACHE_COLLECTION)
        cache_key = f"{self.kb_name}|{doc_type or ''}|{query}"

        cached = await cache.get(cache_key)
        if cached is not None:
            logger.info("茶知识库查询缓存命中: query=%s", query[:50])
            return cached

        store = KnowledgeBaseStore(
            self.kb_name, persist_directory=self.persist_directory or None
        )
        try:
            reranker, instruction = self._build_reranker()
            retriever = KnowledgeBaseRetriever(
                store,
                top_k=self.top_k,
                budget_chars=self.budget_chars,
                reranker=reranker,
                rerank_instruction=instruction,
            )
            result = await retriever.retrieve(query, doc_type=doc_type)
        finally:
            store.close()

        await cache.put(cache_key, result.text)
        logger.info(
            "茶知识库查询完成: query=%s, 命中%d块", query[:50], len(result.chunks)
        )
        return result.text

    @staticmethod
    def _build_reranker() -> tuple[RerankClient | None, str | None]:
        """根据配置构造 RerankClient, 未启用时返回 (None, None)."""
        cfg = get_inference_config().reranker
        if not cfg.enabled:
            return None, None
        return (
            RerankClient(cfg.base_url, timeout=cfg.timeout),
            cfg.instruction or None,
        )


__all__ = ["TeaKnowledgeInput", "TeaKnowledgeTool"]
