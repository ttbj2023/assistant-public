"""知识库全局向量存储 - 直连 ChromaDB, 按 kb_name 物理隔离.

区别于对话记忆的 LangChainVectorStore(强绑 user/thread/agent 三级隔离):
知识库是全局共享文料, 以 kb_name 为键落 BASE_DATA_PATH/_knowledge_base/{kb_name}/,
collection 用 cosine 空间, embedding 经统一入口 create_embeddings() 创建(可注入便于测试).
"""

from __future__ import annotations

import inspect
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import chromadb
from langchain_core.documents import Document

from src.config.runtime_env import get_base_data_path

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

_KB_ROOT_DIRNAME = "_knowledge_base"
_UNSAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")
# embed 分批大小: 整篇文档数百块一次请求会打爆本地 embedding 的 10s 超时
# (实测 Ollama CPU bge-m3 约 8块/秒, 16块/批留足余量)
_EMBED_BATCH_SIZE = 16


def _safe_name(name: str) -> str:
    """清洗 kb_name 为目录/collection 安全名(保留字母数字_-)."""
    return _UNSAFE_NAME_RE.sub("_", name.strip()) or "default"


class KnowledgeBaseStore:
    """按 kb_name 隔离的 Chroma 向量库, 提供增/查/按文档删除."""

    def __init__(
        self,
        kb_name: str,
        *,
        persist_directory: str | None = None,
        embeddings: Embeddings | None = None,
    ) -> None:
        if not kb_name or not kb_name.strip():
            raise ValueError("kb_name 必须提供")
        self.kb_name = kb_name
        self._safe = _safe_name(kb_name)
        self._persist_directory = persist_directory or str(
            self._default_dir(self._safe)
        )
        self._embeddings: Embeddings | None = embeddings
        self._client: Any = None
        self._collection: Any = None

    @staticmethod
    def _default_dir(safe_name: str) -> Path:
        return get_base_data_path() / _KB_ROOT_DIRNAME / safe_name

    @classmethod
    def default_persist_directory(cls, kb_name: str) -> Path:
        """kb_name 对应的默认持久化目录(供工具/脚本定位)."""
        return cls._default_dir(_safe_name(kb_name))

    @classmethod
    def has_data(cls, kb_name: str) -> bool:
        """廉价判断知识库是否已索引(仅查目录, 不加载 embedding)."""
        return cls.directory_has_data(str(cls.default_persist_directory(kb_name)))

    @staticmethod
    def directory_has_data(directory: str) -> bool:
        """判断某目录是否存在且非空(同步, 供 async 调用方避免 ASYNC240)."""
        path = Path(directory)
        return path.exists() and any(path.iterdir())

    @property
    def persist_directory(self) -> str:
        """向量库持久化目录(索引状态文件亦落此处)."""
        return self._persist_directory

    def _ensure_initialized(self) -> None:
        """延迟初始化 Chroma 客户端与 embedding(首次调用时)."""
        if self._collection is not None:
            return
        Path(self._persist_directory).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=self._persist_directory)
        self._collection = self._client.get_or_create_collection(
            name=f"kb_{self._safe}",
            metadata={"hnsw:space": "cosine"},
        )
        if self._embeddings is None:
            from src.config.inference_config import get_config
            from src.inference.embeddings.embeddings import create_embeddings

            model_id = get_config().embeddings.model
            self._embeddings = create_embeddings(model=model_id)
        logger.info(
            "知识库向量库已初始化: kb=%s, dir=%s", self.kb_name, self._persist_directory
        )

    async def _embed_documents(self, texts: list[str]) -> list[list[float]]:
        assert self._embeddings is not None
        vectors: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[i : i + _EMBED_BATCH_SIZE]
            result = self._embeddings.aembed_documents(batch)
            if inspect.isawaitable(result):
                result = await result
            vectors.extend(result)
        return vectors

    async def _embed_query(self, text: str) -> list[float]:
        assert self._embeddings is not None
        result = self._embeddings.aembed_query(text)
        if inspect.isawaitable(result):
            return await result
        return result  # type: ignore[return-value]

    async def add_documents(
        self, documents: list[Document], ids: list[str]
    ) -> list[str]:
        """写入文档块(元数据须为标量, 非标量转字符串)."""
        self._ensure_initialized()
        texts = [d.page_content for d in documents]
        metadatas = [self._clean_metadata(d.metadata) for d in documents]
        embeddings = await self._embed_documents(texts)
        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        return ids

    async def search(
        self,
        query: str,
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[Document]:
        """相似检索, 返回带 score(cosine 相似度, 越大越相关)的 Document 列表."""
        self._ensure_initialized()
        query_embedding = await self._embed_query(query)
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where or None,
            include=["documents", "distances", "metadatas"],
        )
        if not results["documents"] or not results["documents"][0]:
            return []

        docs: list[Document] = []
        for doc, distance, meta in zip(
            results["documents"][0],
            results["distances"][0],
            results["metadatas"][0],
            strict=False,
        ):
            metadata = dict(meta or {})
            metadata["score"] = 1.0 - distance
            docs.append(Document(page_content=doc, metadata=metadata))
        return docs

    async def delete_by_doc_ref(self, doc_ref: str) -> None:
        """删除某文档的全部块(增量重建用, 按元数据 doc_ref 过滤)."""
        self._ensure_initialized()
        self._collection.delete(where={"doc_ref": doc_ref})

    def count(self) -> int:
        """当前块总数."""
        self._ensure_initialized()
        return int(self._collection.count())

    def close(self) -> None:
        """释放客户端引用."""
        self._client = None
        self._collection = None

    @staticmethod
    def _clean_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        """Chroma 元数据仅接受标量; 非标量转 JSON 字符串."""
        import json

        cleaned: dict[str, Any] = {}
        for key, value in (metadata or {}).items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                cleaned[key] = value
            else:
                cleaned[key] = json.dumps(value, ensure_ascii=False)
        return cleaned


__all__ = ["KnowledgeBaseStore"]
