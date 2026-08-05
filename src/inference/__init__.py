"""推理模块 - 统一管理 AI 推理功能.

核心子包 (经本 __init__ re-export):
- llm: LLM 模型管理 (统一入口 create_llm)
- embeddings: 嵌入模型管理 (统一入口 create_embeddings)
- rerank: 重排序客户端 (RerankClient)
- content_analyzer: 内容分析器 (对话索引 / 置顶记忆覆写)

专用服务 (按完整路径导入):
- image_generation / video_generation: 图像/视频生成 REST 服务
- image_description: 图片视觉描述
- health_data_extraction: 健康数据结构化提取
- usage: LLM/Embedding 用量采集
- shared: 子包共享工具 (provider 校验等)
"""

from __future__ import annotations

from . import content_analyzer, embeddings, llm, rerank

__all__ = ["content_analyzer", "embeddings", "llm", "rerank"]
