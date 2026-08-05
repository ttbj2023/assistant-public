"""知识库子系统 - 异构文料(书籍/论文/评测)的离线索引与运行时检索.

组织方式(目录即书): 领域语料根(如 data/tea/)下每个含 Markdown 的一级子目录
即一部资料, book.yaml 提供书目元数据; 领域名(语料根目录名)即 kb_name.

数据流(两阶段离线 + 运行时检索):
- 索引: 语料目录自动发现 → 结构感知分块(表格 pipe 化/图片剥离) → embed → 全局向量库
- 检索: query → dense 召回 → top-K + 出处 + 字符预算 → 返回主对话

与 assistant 主包解耦: 仅消费 Markdown + book.yaml, 不依赖任何文档转换工具.
"""

from __future__ import annotations

__all__: list[str] = []
