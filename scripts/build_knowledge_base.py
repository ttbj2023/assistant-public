"""知识库离线索引脚本 - 目录即书: 领域语料根 → 自动发现 → 结构感知分块 → 全局向量库.

用法:
    python scripts/build_knowledge_base.py --corpus data/tea
    python scripts/build_knowledge_base.py --corpus data/tea --kb-name tea
    python scripts/build_knowledge_base.py --corpus data/tea --rebuild
    python scripts/build_knowledge_base.py --corpus data/tea --max-chunk-chars 600 --overlap-chars 80

说明:
- 语料根下每个含 >=1 个 Markdown 的一级子目录即一部资料(书/论文/评测),
  book.yaml 提供书目元数据(缺省时 title=目录名, type=book).
- kb_name 默认取语料根目录名(如 data/tea → tea), 与运行时领域工具共享向量库.
- 增量幂等: 未变更文档跳过; 文档变更自动删旧块重建; --rebuild 全量重建.
- 向量库落 BASE_DATA_PATH/_knowledge_base/{kb_name}/.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 允许脚本直接运行时导入 src 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.knowledge_base.chunker import MarkdownChunker  # noqa: E402
from src.knowledge_base.indexer import KnowledgeBaseIndexer  # noqa: E402
from src.knowledge_base.store import KnowledgeBaseStore  # noqa: E402

logger = logging.getLogger("build_knowledge_base")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_knowledge_base",
        description="知识库离线索引: 领域语料目录 → 向量库",
    )
    parser.add_argument(
        "--corpus", required=True, help="领域语料根目录(一级子目录 = 一部资料)"
    )
    parser.add_argument(
        "--kb-name",
        default=None,
        help="知识库名称(默认: 语料根目录名, 如 data/tea → tea)",
    )
    parser.add_argument("--rebuild", action="store_true", help="忽略索引状态, 全量重建")
    parser.add_argument(
        "--max-chunk-chars", type=int, default=800, help="块大小上限(默认 800)"
    )
    parser.add_argument(
        "--overlap-chars", type=int, default=100, help="块重叠字符数(默认 100)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    return parser.parse_args()


async def _run(args: argparse.Namespace, corpus_root: Path, kb_name: str) -> int:
    logger.info(
        "开始索引知识库 '%s': corpus=%s, rebuild=%s",
        kb_name,
        corpus_root,
        args.rebuild,
    )

    store = KnowledgeBaseStore(kb_name)
    try:
        chunker = MarkdownChunker(
            max_chunk_chars=args.max_chunk_chars,
            overlap_chars=args.overlap_chars,
        )
        indexer = KnowledgeBaseIndexer(store, chunker)
        stats = await indexer.build(corpus_root, rebuild=args.rebuild)
    finally:
        store.close()

    logger.info(
        "索引完成: 共%d | 新增%d | 更新%d | 跳过%d | 失败%d | 库内总块数已由向量库持久化",
        stats.total,
        stats.indexed,
        stats.updated,
        stats.skipped,
        stats.failed,
    )
    if stats.errors:
        for err in stats.errors:
            logger.error("  %s", err)

    return 1 if stats.failed else 0


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    corpus_root = Path(args.corpus)
    if not corpus_root.is_dir():
        logger.error("语料目录不存在: %s", corpus_root)
        sys.exit(1)
    kb_name = args.kb_name or corpus_root.resolve().name
    exit_code = asyncio.run(_run(args, corpus_root, kb_name))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
