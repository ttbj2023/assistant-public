"""文档摘要生成器 - 后台调用小模型生成文档摘要.

Fire-and-forget 模式:
- LLM 未提供 summary 时自动触发
- 不阻塞导出工具返回
- 完成后更新 AttachmentDTO.document_meta.summary
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = """请为以下文档内容生成一段简洁的中文摘要 (150-300字).

要求:
- 概括文档的核心主题和关键信息
- 保持客观, 不添加主观评价
- 不使用"本文""该文档"等元描述, 直接陈述内容

文档内容:
{content}"""


async def generate_summary(gfm_content: str) -> str:
    """调用小模型生成文档摘要.

    Args:
        gfm_content: 文档 GFM 源码

    Returns:
        摘要文本, 失败时返回空字符串
    """
    truncated = gfm_content[:8000]
    try:
        from src.config.inference_config import get_config as get_inference_config
        from src.inference.llm.model_loader import create_llm
        from src.inference.llm.response_utils import content_to_text
        from src.inference.usage import usage_source

        inference_config = get_inference_config()
        model_id = inference_config.content_analyzer.model
        llm = create_llm(model_id)

        prompt = _SUMMARY_PROMPT.format(content=truncated)
        with usage_source("tool_llm"):
            response = await llm.ainvoke(prompt)

        text = (
            content_to_text(response.content)
            if hasattr(response, "content")
            else str(response)
        )
        return text.strip()[:500]
    except Exception as e:
        logger.warning("文档摘要生成失败 (将使用空摘要): %s", e)
        return ""


async def update_document_meta_summary(
    file_id: str,
    summary: str,
    *,
    user_id: str,
    thread_id: str,  # noqa: ARG001
    agent_id: str,  # noqa: ARG001
) -> None:
    """更新已注册附件的 document_meta 中的 summary 字段.

    Args:
        file_id: 附件 ID
        summary: 生成的摘要
        user_id: 用户 ID
        thread_id: 会话 ID
        agent_id: Agent ID
    """
    try:
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        registry = await create_file_registry_service(user_id)
        db_entry = await registry.get(file_id)
        if not db_entry or not db_entry.document_meta:
            logger.debug("跳过摘要更新: file_id=%s 无文件或无 document_meta", file_id)
            return

        meta = json.loads(db_entry.document_meta)
        meta["summary"] = summary
        db_entry.document_meta = json.dumps(meta, ensure_ascii=False)
        await registry.upsert(db_entry)

        # 描述外置: 后台 LLM 摘要完成后, 写入 .desc.md (覆盖导出时的临时摘要)
        from src.files.desc_writer import write_desc

        write_desc(user_id, file_id, summary)

        logger.info("文档摘要已更新: file_id=%s, 摘要长度=%d", file_id, len(summary))
    except Exception as e:
        logger.warning("文档摘要更新失败: file_id=%s, error=%s", file_id, e)


def schedule_summary_generation(
    file_id: str,
    gfm_content: str,
    *,
    user_id: str,
    thread_id: str,
    agent_id: str,
) -> None:
    """Fire-and-forget 摘要生成 + 更新.

    Args:
        file_id: 附件 ID
        gfm_content: 文档 GFM 源码
        user_id: 用户 ID
        thread_id: 会话 ID
        agent_id: Agent ID
    """
    import asyncio

    async def _task() -> None:
        summary = await generate_summary(gfm_content)
        if summary:
            await update_document_meta_summary(
                file_id,
                summary,
                user_id=user_id,
                thread_id=thread_id,
                agent_id=agent_id,
            )

    try:
        asyncio.get_running_loop().create_task(_task())
    except RuntimeError:
        logger.debug("无运行中的事件循环, 跳过摘要生成")


def extract_auto_summary(gfm_content: str, max_chars: int = 200) -> str:
    """从 GFM 源码自动提取首段作为临时摘要.

    用于导出时 summary 为空且后台摘要尚未完成时的临时替代.

    Args:
        gfm_content: GFM 源码
        max_chars: 最大字符数

    Returns:
        首段文本或空字符串
    """
    lines = gfm_content.split("\n")
    paragraph_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            if paragraph_lines:
                break
            continue
        if stripped == "---":
            continue
        if not stripped:
            if paragraph_lines:
                break
            continue
        paragraph_lines.append(stripped)

    text = " ".join(paragraph_lines)
    return text[:max_chars]
