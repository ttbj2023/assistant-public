"""对话历史向量存储实现 - 直连 ChromaDB.

实现统一配置驱动的对话历史存储架构:
- Agent物理隔离: 每个agent拥有独立的向量存储目录
- 轮次信息作为向量存储的唯一元数据
- 主内容存储用户输入 + 助手回复全文
- 支持两阶段检索: 语义搜索 + 精确轮次检索
- 使用统一配置系统管理嵌入模型 (inference.embeddings.model)
- 集成统一路径管理 (src.core.path_resolver.get_vector_path)

依赖说明: 对 inference.embeddings 的依赖 (向量存储本质需要 embedding 模型)
经架构评估保留, 详见 AGENTS.md "分层依赖总览 - 已知的语义合理交叉依赖".
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import chromadb
from langchain_core.documents import Document

from src.config.inference_config import get_config
from src.core.path_resolver import get_vector_path
from src.inference.embeddings.embeddings import create_embeddings

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)


def distance_to_similarity(distance: float) -> float:
    """L2 distance → 相似度转换.

    Chroma collection 默认 L2 (欧氏距离), 值域 [0, +∞), 不具备 "1=最相似" 语义.
    用 1/(1+d) 单调递减映射到 (0,1]: d=0 → 1.0, d 越大越趋近 0.
    (原先误用 1-d 的 cosine 语义, 会因 d>1 截断为 0 丢失区分度.)
    """
    return 1.0 / (1.0 + max(0.0, distance))


def _results_to_docs_and_scores(results: Any) -> list[tuple[Document, float]]:
    """将 ChromaDB 原始 query 结果转换为 (Document, distance) 列表."""
    return [
        (
            Document(page_content=doc, metadata=meta or {}, id=doc_id),
            dist,
        )
        for doc, meta, doc_id, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["ids"][0],
            results["distances"][0],
            strict=False,
        )
        if doc is not None
    ]


class LangChainVectorStore:
    """直连 ChromaDB 的对话历史向量存储.

    特性:
    - 统一配置系统:从 inference.embeddings.model 读取嵌入模型配置
    - 统一路径管理:使用 src.core.path_resolver.get_vector_path 管理存储路径
    - 延迟初始化:支持异步初始化和资源管理
    - 线程安全:使用异步锁防止并发初始化问题
    - 错误处理:完整的错误处理和日志记录
    """

    def __init__(
        self,
        collection_name: str,
        user_id: str,
        thread_id: str,
        agent_id: str,
        persist_directory: str | None = None,
        embedding_model: str | None = None,
    ) -> None:
        """初始化向量存储 (Agent物理隔离).

        Args:
            collection_name: 集合名称
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID (用于物理隔离)
            persist_directory: 持久化目录,如果不提供则使用统一路径管理
            embedding_model: 嵌入模型名称,如果不提供则从 inference.embeddings.model 读取

        Raises:
            ValueError: 当 user_id,thread_id 或 agent_id 未提供时
            ValueError: 当嵌入模型配置缺失时

        Note:
            - 嵌入模型配置来源: inference.embeddings.model
            - 路径管理来源: src.core.path_resolver.get_vector_path (含agent_id隔离)
            - 支持延迟初始化,实际资源在首次使用时创建

        """
        if not user_id or not thread_id:
            raise ValueError("user_id和thread_id必须提供")
        if not agent_id:
            raise ValueError("agent_id必须提供")

        self.collection_name = collection_name
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_id = agent_id

        # 使用统一配置系统获取嵌入模型
        if embedding_model:
            self.embedding_model = embedding_model
        else:
            try:
                inference_config = get_config()
                self.embedding_model = inference_config.embeddings.model
                logger.debug(
                    f"🔧 从inference配置读取embedding_model: {self.embedding_model}",
                )
            except (KeyError, AttributeError) as e:
                logger.error("❌ 嵌入模型配置缺失: %s", e)
                raise ValueError(
                    f"嵌入模型配置缺失,请检查config.yaml中的inference.embeddings.model配置: {e}",
                ) from e

        # 解析持久化目录
        self.persist_directory = self._resolve_persist_directory(persist_directory)

        # 获取嵌入模型
        self._embeddings: Embeddings | None = None

        # 线程池执行器(单例模式,避免资源泄漏)
        self._executor: ThreadPoolExecutor | None = None

        # ChromaDB 客户端与 collection
        self._client: chromadb.ClientAPI | None = None
        self._collection: Any | None = None
        self._initialized: bool = False
        self._initialization_lock = asyncio.Lock()

        logger.info(f"🔗 初始化向量存储: {self.collection_name}")

    def _get_executor(self) -> ThreadPoolExecutor:
        """获取线程池执行器,实现单例复用模式."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix=f"chroma-{self.collection_name}-",
            )
            logger.debug(f"🔧 创建线程池: {self.collection_name}")
        return self._executor

    def _resolve_persist_directory(self, persist_directory: str | None) -> str:
        """解析持久化目录路径 (Agent物理隔离).

        Args:
            persist_directory: 用户指定的持久化目录,如果为None则使用统一路径管理

        Returns:
            解析后的持久化目录路径

        Note:
            使用 src.core.path_resolver.get_vector_path 进行统一路径管理
            包含 agent_id 实现三级物理隔离

        """
        if persist_directory:
            return persist_directory

        vector_path = get_vector_path(
            self.user_id,
            self.thread_id,
            agent_id=self.agent_id,
        )

        vector_path.mkdir(parents=True, exist_ok=True)
        return str(vector_path)

    async def _ensure_initialized(self) -> None:
        """确保存储已初始化,使用锁防止并发初始化."""
        if self._initialized:
            return

        async with self._initialization_lock:
            if self._initialized:  # 双重检查
                return
            await self._initialize()

    def _create_collection(self) -> None:
        """在同步上下文中创建 ChromaDB 客户端与 collection."""
        self._client = chromadb.PersistentClient(
            path=self.persist_directory,
            tenant=chromadb.config.DEFAULT_TENANT,
            database=chromadb.config.DEFAULT_DATABASE,
        )
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
        )

    async def _initialize(self) -> None:
        """初始化向量存储."""
        try:
            # 初始化嵌入模型
            await self._initialize_embeddings()

            # 创建 ChromaDB collection
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._get_executor(), self._create_collection)

            self._initialized = True
            logger.info(f"✅ 向量存储初始化成功: {self.collection_name}")

        except Exception as e:
            logger.error("❌ 向量存储初始化失败: %s", e, exc_info=True)
            raise RuntimeError(f"向量存储初始化失败: {self.collection_name}") from e

    async def _initialize_embeddings(self) -> None:
        """初始化嵌入模型."""
        if self._embeddings is not None:
            return

        # 从统一配置系统获取嵌入模型
        embedding_model_name = self._get_embedding_model_config()

        # 验证配置格式
        provider, _model_id = self._validate_and_parse_embedding_config(
            embedding_model_name,
        )

        # 创建嵌入模型
        logger.info("🔧 初始化嵌入模型: %s", embedding_model_name)
        embeddings = create_embeddings(provider, embedding_model_name)

        # 检查创建结果
        if embeddings is None:
            raise RuntimeError(
                f"嵌入模型创建失败,请检查模型配置和依赖: {provider}:{embedding_model_name}",
            )

        self._embeddings = embeddings

    def _get_embedding_model_config(self) -> str:
        """从统一配置系统获取嵌入模型配置.

        Returns:
            嵌入模型配置字符串,格式为 provider:model

        Raises:
            KeyError: 当 inference.embeddings.model 配置缺失时
            AttributeError: 当配置结构不正确时

        Note:
            配置来源:src.config.get_config().embeddings.model

        """
        inference_config = get_config()
        return inference_config.embeddings.model

    def _validate_and_parse_embedding_config(
        self,
        embedding_model_name: str,
    ) -> tuple[str, str]:
        """验证并解析嵌入模型配置.

        Args:
            embedding_model_name: 嵌入模型配置字符串,格式为 provider:model

        Returns:
            (provider, model_id) 元组

        Raises:
            ValueError: 当配置格式不正确时

        Note:
            要求严格的 'provider:model' 格式,例如:
            - 'local-embedding:bge-m3'
            - 'openai:text-embedding-ada-002'

        """
        # 严格要求provider:model格式
        if ":" not in embedding_model_name:
            raise ValueError(
                f"Embedding模型配置格式错误: '{embedding_model_name}'."
                f"请使用 'provider:model' 格式,例如 'local-embedding:bge-m3'",
            )

        provider, model_id = embedding_model_name.split(":", 1)
        if not provider or not model_id:
            raise ValueError(
                f"Embedding模型配置格式错误: '{embedding_model_name}'."
                f"provider和model都不能为空",
            )

        return provider, model_id

    async def add_documents(
        self,
        documents: list[Document],
        ids: list[str] | None = None,
        **_kwargs: Any,
    ) -> list[str]:
        """添加文档到向量存储.

        Args:
            documents: Document列表
            ids: 文档ID列表(可选)
            **kwargs: 其他参数

        Returns:
            添加的文档ID列表

        """
        await self._ensure_initialized()
        assert self._collection is not None

        try:
            # 验证输入
            if not documents:
                raise ValueError("文档列表不能为空")

            # 如果没有提供ID,生成唯一的ID避免覆盖
            if ids is None:
                import time
                import uuid

                timestamp = int(time.time() * 1000)  # 毫秒时间戳
                ids = [
                    doc.metadata.get(
                        "id",
                        f"doc_{timestamp}_{uuid.uuid4().hex[:8]}_{i}",
                    )
                    for i, doc in enumerate(documents)
                ]

            # 验证ID数量匹配
            if len(ids) != len(documents):
                raise ValueError(
                    f"文档ID数量({len(ids)})与文档数量({len(documents)})不匹配",
                )

            # 清理和验证文档元数据,确保所有值都是基本类型
            cleaned_documents = []
            for doc in documents:
                if doc.metadata:
                    cleaned_metadata = self._clean_metadata(doc.metadata)
                    cleaned_doc = Document(
                        page_content=doc.page_content,
                        metadata=cleaned_metadata,
                    )
                    cleaned_documents.append(cleaned_doc)
                else:
                    cleaned_documents.append(doc)

            texts = [doc.page_content for doc in cleaned_documents]
            metadatas = [doc.metadata for doc in cleaned_documents]

            assert self._embeddings is not None
            embeddings = await self._embeddings.aembed_documents(texts)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                self._get_executor(),
                lambda: self._collection.upsert(
                    embeddings=embeddings,
                    documents=texts,
                    metadatas=metadatas if any(metadatas) else None,
                    ids=ids,
                ),
            )

            logger.debug(f"📝 添加了 {len(documents)} 个文档到向量存储")
            return ids

        except Exception as e:
            logger.error("❌ 添加文档失败: %s", e, exc_info=True)
            raise

    async def similarity_search(
        self,
        query: str,
        max_results: int = 4,
        filter: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> list[Document]:
        """相似性搜索.

        Args:
            query: 查询字符串
            max_results: 返回结果数量
            filter: 元数据过滤条件
            **kwargs: 其他参数

        Returns:
            相似文档列表

        """
        await self._ensure_initialized()
        assert self._collection is not None

        try:
            # 验证输入
            if not query or not query.strip():
                raise ValueError("查询字符串不能为空")

            if max_results <= 0:
                raise ValueError("max_results必须大于0")

            # 构建过滤条件,包含用户和线程隔离
            search_filter = self._build_search_filter(filter)

            where_clause = search_filter or None

            assert self._embeddings is not None
            query_embedding = await self._embeddings.aembed_query(query)

            loop = asyncio.get_running_loop()
            chroma_results = await loop.run_in_executor(
                self._get_executor(),
                lambda: self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=max_results,
                    where=where_clause,
                ),
            )

            docs_and_scores = _results_to_docs_and_scores(chroma_results)
            results = [doc for doc, _ in docs_and_scores]

            logger.debug(f"🔍 相似性搜索返回 {len(results)} 个结果")
            return results

        except Exception as e:
            logger.error("❌ 相似性搜索失败: %s", e, exc_info=True)
            raise

    async def delete(
        self,
        ids: list[str] | None = None,
        **_kwargs: Any,
    ) -> bool:
        """删除文档.

        Args:
            ids: 文档ID列表
            **kwargs: 其他参数

        Returns:
            是否删除成功

        """
        await self._ensure_initialized()
        assert self._collection is not None

        try:
            if not ids:
                raise ValueError("文档ID列表不能为空")

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                self._get_executor(),
                lambda: self._collection.delete(ids=ids),
            )
            logger.debug(f"🗑️ 删除了 {len(ids)} 个文档")
            return True

        except Exception as e:
            logger.error("❌ 删除文档失败: %s", e, exc_info=True)
            raise

    def close(self, *, blocking: bool = True) -> None:
        """关闭向量存储,清理资源.

        Args:
            blocking: True 时阻塞等待线程池任务结束(显式关闭用);
                False 时非阻塞(析构兜底用, 避免在事件循环/GC 线程中阻塞).

        """
        try:
            if self._executor:
                logger.debug(f"🔧 关闭线程池: {self.collection_name}")
                self._executor.shutdown(wait=blocking)
                self._executor = None

            # 清理其他资源
            self._client = None
            self._collection = None
            self._embeddings = None
            self._initialized = False

            if blocking:
                logger.info(f"✅ 向量存储已关闭: {self.collection_name}")

        except Exception as e:
            logger.warning("⚠️ 关闭向量存储时出现警告: %s", e)

    def __del__(self) -> None:
        """析构兜底: 仅非阻塞清理, 避免在事件循环线程/GC 中阻塞."""
        self.close(blocking=False)

    def _build_search_filter(
        self,
        custom_filter: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """构建搜索过滤条件.

        注意:用户和线程隔离通过集合命名实现,不需要额外过滤.

        Args:
            custom_filter: 自定义过滤条件

        Returns:
            自定义过滤条件(不包含用户和线程隔离)

        """
        # 直接返回自定义过滤条件,用户和线程隔离通过集合命名实现
        return custom_filter or {}

    def _clean_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Clean metadata to ensure compatibility with ChromaDB.

        Args:
            metadata: Raw metadata dictionary

        Returns:
            Cleaned metadata with ChromaDB-compatible values

        """
        if not metadata:
            return {}

        cleaned = {}
        for key, value in metadata.items():
            cleaned[key] = self._clean_metadata_value(value)

        return cleaned

    def _clean_metadata_value(self, value: object) -> str | int | float | bool | None:
        """Clean individual metadata value for ChromaDB compatibility.

        Args:
            value: Raw metadata value

        Returns:
            Cleaned value compatible with ChromaDB

        """
        # 基本类型直接保留
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        # 列表转换为逗号分隔的字符串
        if isinstance(value, list):
            return self._clean_list_value(value)
        # 字典转换为JSON字符串
        if isinstance(value, dict):
            return self._clean_dict_value(value)
        # 其他类型转为字符串
        return str(value)

    def _clean_list_value(self, value: list) -> str:
        """Clean list metadata value."""
        try:
            # 过滤出基本类型的元素
            basic_elements = []
            for item in value:
                if isinstance(item, (str, int, float, bool)) or item is None:
                    basic_elements.append(str(item))
                else:
                    # 复杂对象转为JSON字符串
                    basic_elements.append(self._serialize_complex_value(item))

            return ", ".join(basic_elements)
        except Exception as e:
            logger.debug("列表元数据序列化失败, 降级为str: %s", e)
            return str(value)

    def _clean_dict_value(self, value: dict) -> str:
        """Clean dictionary metadata value."""
        try:
            return self._serialize_complex_value(value)
        except Exception as e:
            logger.debug("字典元数据序列化失败, 降级为str: %s", e)
            return str(value)

    def _serialize_complex_value(self, value: object) -> str:
        """Serialize complex value to JSON string."""
        import json

        return json.dumps(value, ensure_ascii=False)

    def get_collection_stats(self) -> dict[str, Any]:
        """获取集合统计信息.

        Returns:
            集合统计信息字典

        """
        try:
            if not self._collection:
                return {"error": "Vector store not initialized"}

            doc_count = self._collection.count()

            return {
                "name": self.collection_name,
                "user_id": self.user_id,
                "thread_id": self.thread_id,
                "persist_directory": self.persist_directory,
                "document_count": doc_count,
                "embedding_model": self.embedding_model,
                "status": "active",
            }

        except Exception as e:
            logger.error("❌ 获取集合统计失败: %s", e)
            return {"error": str(e)}

    # === 对话轮次存储专用方法 ===

    async def add_conversation_round(
        self,
        round_number: int,
        user_message: str,
        assistant_response: str,
        agent_id: str,
    ) -> str:
        """添加对话轮次到向量存储.

        按照用户需求,将轮次信息作为元数据,主内容为用户输入+助手回复全文.
        doc_id 包含 agent_id,与 SQL 的 (user_id, thread_id, agent_id, round_number)
        唯一约束对齐,避免同轮次不同 Agent 的向量记录互相覆盖.

        Args:
            round_number: 对话轮次号
            user_message: 用户输入消息
            assistant_response: 助手回复
            agent_id: Agent ID,用于区分同一线程中不同 Agent 的记录

        Returns:
            添加的文档ID

        """
        await self._ensure_initialized()

        try:
            # 验证输入
            if round_number < 0:
                raise ValueError("轮次号必须大于等于0")

            if not user_message or not user_message.strip():
                raise ValueError("用户消息不能为空")

            if not assistant_response or not assistant_response.strip():
                raise ValueError("助手回复不能为空")

            # 构建主内容:用户输入 + 助手回复全文
            content = f"用户: {user_message}\n\n助手: {assistant_response}"

            # 构建轮次元数据(确保所有值都是基本类型,避免unhashable type错误)
            round_metadata = {
                "round_number": int(round_number),
                "user_id": str(self.user_id),
                "thread_id": str(self.thread_id),
                "agent_id": str(agent_id),
            }

            document = Document(page_content=content, metadata=round_metadata)

            # doc_id 包含 agent_id,与 SQL unique key 对齐
            doc_id = f"{self.user_id}_{self.thread_id}_{agent_id}_round_{round_number}"

            # 添加到向量存储
            await self.add_documents([document], ids=[doc_id])

            logger.debug(
                "💬 添加对话轮次 %s (agent=%s) 到向量存储",
                round_number,
                agent_id,
            )
            return doc_id

        except Exception as e:
            logger.error("❌ 添加对话轮次失败: %s", e, exc_info=True)
            raise

    async def get_existing_round_numbers(self) -> set[int]:
        """查询 collection 中已存在的 round_number 集合(向量补偿 diff 用).

        实例已按 (user_id, thread_id, agent_id) 物理隔离, 故无需额外 where 过滤.
        用于向量补偿: SQL 有但向量库缺失的轮次即为需补入的 gap.

        Returns:
            已存在文档的 round_number 集合; 无文档或查询异常时返回空集合

        """
        await self._ensure_initialized()
        assert self._collection is not None

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self._get_executor(),
                lambda: self._collection.get(include=["metadatas"]),
            )
            rounds: set[int] = set()
            for meta in result.get("metadatas") or []:
                if meta and "round_number" in meta:
                    try:
                        rounds.add(int(meta["round_number"]))
                    except (TypeError, ValueError):
                        continue
            logger.debug("📋 向量库已有 round: %s", sorted(rounds))
            return rounds
        except Exception as e:
            logger.warning("查询已存在 round 失败, 返回空集合: %s", e)
            return set()

    # === 两阶段检索专用方法 ===

    async def search_rounds_only(
        self,
        query: str,
        max_results: int = 10,
        round_range: tuple[int, int] | None = None,
        **filters: Any,
    ) -> list[tuple[int, float]]:
        """两阶段检索第一阶段:仅返回匹配的轮次号和相似度得分.

        专门为两阶段检索架构优化,不返回完整文档内容,减少数据传输.

        Args:
            query: 搜索查询
            max_results: 返回结果数量
            round_range: 轮次范围 (start_round, end_round)
            **filters: 其他过滤条件

        Returns:
            匹配的(轮次号, 相似度得分)列表,按相似度降序排序
            相似度得分范围:0-1,1表示最相关

        """
        await self._ensure_initialized()
        assert self._collection is not None

        try:
            # 验证输入
            if not query or not query.strip():
                raise ValueError("查询字符串不能为空")

            if max_results <= 0:
                raise ValueError("max_results必须大于0")

            if round_range:
                if len(round_range) != 2:
                    raise ValueError("轮次范围必须是包含2个元素的元组")
                start_round, end_round = round_range
                if start_round < 0 or end_round < 0:
                    raise ValueError("轮次号必须大于等于0")
                if start_round > end_round:
                    raise ValueError("开始轮次必须小于等于结束轮次")

            # 构建搜索过滤条件
            # ChromaDB where 仅支持 {field: {op: val}} 单 operator 或 {"$and": [...]} 组合,
            # 不支持 {field: {"$gte": x, "$lte": y}} 同字段多 operator 合并,
            # 故把所有条件收集到 list, 最后按需用 $and 包裹
            conditions: list[dict] = []

            # 添加轮次范围过滤 (拆为 $gte/$lte 两个独立条件)
            if round_range:
                start_round, end_round = round_range
                conditions.append({"round_number": {"$gte": start_round}})
                conditions.append({"round_number": {"$lte": end_round}})

            # 合并其他过滤条件 (每个 field 独立成一个条件项)
            if filters:
                for k, v in filters.items():
                    conditions.append({k: v})

            # 组装最终 search_filter
            if not conditions:
                search_filter = None
            elif len(conditions) == 1:
                search_filter = conditions[0]
            else:
                search_filter = {"$and": conditions}

            assert self._embeddings is not None
            query_embedding = await self._embeddings.aembed_query(query)

            loop = asyncio.get_running_loop()
            chroma_results = await loop.run_in_executor(
                self._get_executor(),
                lambda: self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=max_results,
                    where=search_filter,
                ),
            )

            docs_and_scores = _results_to_docs_and_scores(chroma_results)

            # 提取轮次号和相似度得分(仅返回轮次号和得分,不返回完整内容)
            round_score_pairs = []
            for doc, distance in docs_and_scores:
                if "round_number" in doc.metadata:
                    round_number = doc.metadata["round_number"]

                    # ChromaDB 默认返回 L2 distance, 转换为 0-1 相似度得分
                    similarity_score = distance_to_similarity(distance)

                    round_score_pairs.append((round_number, similarity_score))

            logger.debug(
                f"🎯 向量搜索返回 {len(round_score_pairs)} 个轮次号(带得分), "
                f"平均相似度: {sum(s for _, s in round_score_pairs) / len(round_score_pairs) if round_score_pairs else 0:.3f}",
            )
            return round_score_pairs

        except Exception as e:
            logger.error("❌ 向量搜索轮次号失败: %s", e, exc_info=True)
            raise


# 便利函数
def create_langchain_vector_store(
    user_id: str,
    thread_id: str,
    agent_id: str,
    persist_directory: str | None = None,
    embedding_model: str | None = None,
) -> LangChainVectorStore:
    """创建对话历史向量存储实例的便利函数 (Agent物理隔离).

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID (用于物理隔离)
        persist_directory: 持久化目录,如果不提供则使用统一路径管理
        embedding_model: 嵌入模型,如果未提供则从 inference.embeddings.model 读取

    Returns:
        配置好的对话历史向量存储实例

    Note:
        - 固定使用 "conversations" 作为集合名称
        - 自动使用统一配置系统和路径管理
        - 支持延迟初始化,资源在首次使用时创建

    """
    return LangChainVectorStore(
        collection_name="conversations",
        user_id=user_id,
        thread_id=thread_id,
        agent_id=agent_id,
        persist_directory=persist_directory,
        embedding_model=embedding_model,
    )


__all__ = [
    "LangChainVectorStore",
    "create_langchain_vector_store",
]
