"""文件管理子系统 - 统一的文件全生命周期管理.

收敛原本散落在 core/ + storage/ + tools/shared/ 的文件管理职责到单一子系统边界,
与 memory/health 子系统的边界风格对齐.

核心职责:
- 物理文件存储与去重 (用户级 SHA-256 内容哈希)
- 附件元数据持久化 (Agent 级 attachment_registry 表)
- 存储配额管理与自动清理
- HMAC 签名 URL 生成与校验
- 跨 Agent / 跨线程的文件引用协调

不包含的职责 (放在其他位置):
- 视觉模型描述生成: src/inference/image_description/ (推理职责)
- 消息文本格式化: 上层 agent/chat_helpers (展示职责)
- 路径解析与目录创建: src/core/path_resolver (基础设施)

依赖方向 (单向向下):
    api/agent/tools -> files/ -> storage/dao(Engine池) -> core/path_resolver
"""

from __future__ import annotations

from src.files.models import AttachmentDTO, generate_file_id

__all__ = ["AttachmentDTO", "generate_file_id"]
