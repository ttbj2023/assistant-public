"""会话消息执行编排 (业务逻辑, 非核心基础设施).

原位于 src/core/chat_helpers.py, 现迁至 session 编排层. 这些函数依赖
files/inference/storage, 属于上层编排职责, 不应放在叶子层 (core).

包含:
- allocate_round_number: 对话轮次号分配
- prepare_image_attachments: 多模态图片附件准备与异步描述生成
- background_generate_description: 后台图片描述生成 (由 spawn_background_task 触发)
- background_generate_code_summary: 后台代码文件摘要生成
- background_generate_document_summary: 后台文档概要生成
- prepare_binary_document_attachments: 二进制文档 (docx/pdf/pptx) 解析编排
- background_convert_scanned_document: 扫描件后台 OCR + 概要 + 推送

纯展示格式化函数 (format_user_message_with_attachments / build_file_links /
build_media_lines) 已拆分至 src/utils/message_formatting.py.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from src.core.path_resolver import get_user_path_resolver
from src.files.code_extensions import is_code_file
from src.files.doc2md_client import Doc2mdError, get_doc2md_client
from src.files.paths import FILES_IMAGES
from src.files.repository import get_file_repository
from src.inference.image_description.describer import ImageDescriber
from src.storage.service import create_conversation_service
from src.utils.async_utils import spawn_background_task

logger = logging.getLogger(__name__)

# 扫描件 OCR 页数上限, 超出仅保存原件不解析 (OCR 分钟级耗时不可接受)
OCR_PAGE_LIMIT = 30


async def background_generate_description(
    file_id: str,
    image_path: Path,
    mime_type: str,
) -> None:
    """后台生成图片描述并更新注册表 (fire-and-forget, 不阻塞对话)."""
    try:
        describer = ImageDescriber()
        brief, detail = await describer.describe(image_path, mime_type)
        repository = get_file_repository()
        await repository.update_description(file_id, brief, detail)
        logger.info("🖼️ 后台图片描述生成完成: %s", file_id)
    except Exception as e:
        logger.warning("⚠️ 后台图片描述生成失败: %s", e)


async def background_generate_code_summary(
    file_id: str,
    filename: str,
    content: str,
) -> None:
    """后台生成代码文件摘要并更新注册表 (fire-and-forget, 不阻塞对话).

    desc 最终形态: 结构化摘要 (语言/功能/结构) + 代码原文 (统一结构),
    DB brief = AI 一句话概要.
    """
    try:
        from src.inference.code_description.describer import CodeDescriber

        brief, summary = await CodeDescriber().describe(filename, content)
        if not brief and not summary:
            logger.info("📐 代码摘要为空, 保持注册时占位: %s", file_id)
            return
        repository = get_file_repository()
        await repository.update_description(
            file_id,
            brief,
            content,
            desc_summary=summary or brief,
        )
        logger.info("📐 后台代码摘要生成完成: %s", file_id)
    except Exception as e:
        logger.warning("⚠️ 后台代码摘要生成失败: %s", e)


async def background_generate_document_summary(
    file_id: str,
    filename: str,
    content: str,
) -> None:
    """后台生成普通文档概要并更新注册表 (fire-and-forget, 不阻塞对话).

    desc 最终形态: AI 概要 + 文档原文 (统一结构),
    DB brief = AI 一句话概要.
    """
    try:
        from src.inference.document_description.describer import DocDescriber

        brief, summary = await DocDescriber().describe(filename, content)
        if not brief and not summary:
            logger.info("📄 文档概要为空, 保持注册时占位: %s", file_id)
            return
        repository = get_file_repository()
        await repository.update_description(
            file_id,
            brief,
            content,
            desc_summary=summary or brief,
        )
        logger.info("📄 后台文档概要生成完成: %s", file_id)
    except Exception as e:
        logger.warning("⚠️ 后台文档概要生成失败: %s", e)


async def prepare_binary_document_attachments(
    *,
    user_id: str,
    thread_id: str,
    binary_docs: list[dict],
    round_number: int,
) -> list[Any]:
    """二进制文档 (docx/pdf/pptx) 解析编排.

    流程 (每个文档独立容错):
    - doc2md 服务未配置/不可用 → 降级: 仅存原件, brief 说明
    - defer_scan 探测: 数字版 → 同步转换 (秒级) + 内嵌图入库 + spawn AI 概要
    - 扫描件 (≤页数上限) → 延迟存储 (brief 含预计时长) + spawn 后台 OCR
    - 扫描件 (>页数上限) → 仅存原件, brief 说明超出上限
    """
    if not binary_docs:
        return []

    logger.info("处理 %s 份二进制文档", len(binary_docs))

    client = get_doc2md_client()
    repository = get_file_repository()
    attachment_infos: list[Any] = []

    for doc in binary_docs:
        filename = doc.get("filename", "document.bin")
        try:
            data = base64.b64decode(doc.get("content_b64", ""))
        except (ValueError, TypeError) as e:
            logger.warning("⚠️ 二进制文档 base64 解码失败 (跳过): %s, %s", filename, e)
            continue

        try:
            attachment_infos.append(
                await _process_single_binary_doc(
                    client=client,
                    repository=repository,
                    user_id=user_id,
                    thread_id=thread_id,
                    round_number=round_number,
                    filename=filename,
                    data=data,
                ),
            )
        except (ValueError, OSError) as e:
            logger.warning("⚠️ 二进制文档处理失败 (跳过): %s, %s", filename, e)

    return attachment_infos


async def _process_single_binary_doc(
    *,
    client: Any,
    repository: Any,
    user_id: str,
    thread_id: str,
    round_number: int,
    filename: str,
    data: bytes,
) -> Any:
    """单份二进制文档分流处理 (探测/转换/降级)."""

    async def _store_original(brief: str, **kwargs: Any) -> Any:
        return await repository.store_binary_document(
            user_id=user_id,
            thread_id=thread_id,
            round_number=round_number,
            filename=filename,
            data=data,
            brief=brief,
            **kwargs,
        )

    if not client.is_configured():
        logger.info("doc2md 服务未配置, 仅保存原件: %s", filename)
        return await _store_original(f"{filename}, 解析服务未配置, 仅保存原件")

    try:
        result = await client.convert(filename, data, defer_scan=True)
    except Doc2mdError as e:
        logger.warning("⚠️ doc2md 转换失败 (仅保存原件): %s, %s", filename, e)
        return await _store_original(f"{filename}, 解析服务不可用, 仅保存原件")

    if result is None:
        return await _store_original(f"{filename}, 解析服务未配置, 仅保存原件")

    if result.deferred:
        if result.pages > OCR_PAGE_LIMIT:
            logger.info(
                "扫描件超出页数上限 (%d > %d), 仅保存原件: %s",
                result.pages,
                OCR_PAGE_LIMIT,
                filename,
            )
            return await _store_original(
                f"{filename} 扫描版 {result.pages}页, 超出解析上限({OCR_PAGE_LIMIT}页), 已保存原件",
                mode=result.mode,
                pages=result.pages,
            )

        attachment = await repository.store_binary_document(
            user_id=user_id,
            thread_id=thread_id,
            round_number=round_number,
            filename=filename,
            data=data,
            markdown="",
            mode=result.mode,
            pages=result.pages,
        )
        spawn_background_task(
            background_convert_scanned_document(
                file_id=attachment.file_id,
                filename=filename,
                data=data,
                user_id=user_id,
                thread_id=thread_id,
                agent_id=_current_agent_id(),
            ),
        )
        return attachment

    # 数字版: 内嵌图入库 + 引用替换 → 存原件 (desc=md) → spawn AI 概要
    final_md, _ = await repository.resolve_embedded_images(
        user_id=user_id,
        thread_id=thread_id,
        round_number=round_number,
        markdown=result.markdown,
        images=result.images,
    )
    attachment = await repository.store_binary_document(
        user_id=user_id,
        thread_id=thread_id,
        round_number=round_number,
        filename=filename,
        data=data,
        markdown=final_md,
        mode=result.mode,
        pages=result.pages,
    )
    if attachment.file_id:
        spawn_background_task(
            background_generate_document_summary(
                file_id=attachment.file_id,
                filename=filename,
                content=final_md,
            ),
        )
    return attachment


def _current_agent_id() -> str:
    """从用户上下文取当前 agent_id (后台任务携带)."""
    from src.core.context import get_user_context_or_none

    ctx = get_user_context_or_none()
    return ctx.agent_id if ctx else "unknown"


async def background_convert_scanned_document(
    file_id: str,
    filename: str,
    data: bytes,
    user_id: str,
    thread_id: str,
    agent_id: str,
) -> None:
    """扫描件后台全量 OCR + 概要 + 完成推送 (fire-and-forget).

    上传轮已存原件并应答"解析中"; 本任务完成后重组 desc 并经渠道推送
    概要, 用户无需再发消息即可看到结果.
    """
    try:
        client = get_doc2md_client()
        result = await client.convert(filename, data)
        if result is None:
            raise Doc2mdError("doc2md 服务未配置")

        repository = get_file_repository()
        final_md, _ = await repository.resolve_embedded_images(
            user_id=user_id,
            thread_id=thread_id,
            round_number=0,
            markdown=result.markdown,
            images=result.images,
        )

        from src.inference.document_description.describer import DocDescriber

        brief, summary = await DocDescriber().describe(filename, final_md)
        await repository.update_description(
            file_id,
            brief or filename,
            final_md,
            desc_summary=summary or brief,
        )
        logger.info("📄 扫描件后台解析完成: %s - %s", file_id, brief)

        await _push_parse_done(user_id, thread_id, agent_id, filename, brief)
    except Exception as e:
        logger.warning("⚠️ 扫描件后台解析失败: %s, %s", filename, e)
        try:
            repository = get_file_repository()
            await repository.update_description(
                file_id,
                f"{filename} 解析失败, 原件已保存可下载",
                "",
            )
        except Exception as update_error:
            logger.warning("⚠️ 解析失败状态更新失败: %s", update_error)


async def _push_parse_done(
    user_id: str,
    thread_id: str,
    agent_id: str,
    filename: str,
    brief: str,
) -> None:
    """经渠道推送解析完成通知 (未配置渠道时静默跳过)."""
    from src.core.notification import (
        get_notification_service,
        resolve_delivery,
    )

    delivery = await resolve_delivery(user_id, thread_id, agent_id, "wechat")
    if delivery is None:
        logger.info("未配置 wechat 渠道, 跳过解析完成推送: %s", filename)
        return

    await get_notification_service().send(
        delivery,
        f"📄 《{filename}》解析完成: {brief}",
    )


async def allocate_round_number(
    user_id: str,
    thread_id: str,
    agent_id: str,
) -> int:
    """为指定 user-thread-agent 对话分配递增轮次号."""
    conv_service = await create_conversation_service(
        user_id,
        thread_id,
        agent_id=agent_id,
    )
    return await conv_service.allocate_round_number(user_id, thread_id)


async def prepare_image_attachments(
    *,
    user_id: str,
    thread_id: str,
    is_multimodal: bool,
    image_datas: list[dict],
    round_number: int,
) -> list[Any]:
    """准备图片附件: 保存图片, 按模型能力决定同步/异步生成描述.

    - 多模态模型: 仅保存图片, 描述交由 agent 处理, 后台异步补充注册表.
    - 非多模态模型: 保存时同步生成描述作为补偿.
    """
    if not image_datas:
        return []

    logger.info(
        "处理 %s 张图片, 模型多模态: %s",
        len(image_datas),
        is_multimodal,
    )

    repository = get_file_repository()
    describer = ImageDescriber() if not is_multimodal else None
    attachment_infos: list[Any] = []

    for idx, img_data in enumerate(image_datas):
        logger.info("处理图片 %s/%s", idx + 1, len(image_datas))
        attachment_info = await repository.store_image(
            user_id=user_id,
            thread_id=thread_id,
            round_number=round_number,
            image_data=img_data["data"],
            mime_type=img_data["mime_type"],
            image_describer=describer,
        )

        attachment_infos.append(attachment_info)
        logger.info("图片保存成功: %s", attachment_info.internal_path)

        if is_multimodal and attachment_info.file_id:
            resolver = get_user_path_resolver()
            images_dir = resolver.get_shared_storage_path(
                user_id,
                thread_id,
                FILES_IMAGES,
            )
            filename = attachment_info.internal_path.split("/")[-1]
            image_path = images_dir / filename

            spawn_background_task(
                background_generate_description(
                    file_id=attachment_info.file_id,
                    image_path=image_path,
                    mime_type=img_data["mime_type"],
                ),
            )

    logger.info("全部 %s 张图片处理完成", len(attachment_infos))
    return attachment_infos


async def prepare_document_attachments(
    *,
    user_id: str,
    thread_id: str,
    document_datas: list[dict],
    round_number: int,
) -> list[Any]:
    """准备文档附件: 纯文本直存, 二进制 (content_b64) 走 doc2md 解析编排.

    与 prepare_image_attachments 平行, 无描述生成器 (desc 即原文).
    单个文档保存失败跳过并继续, 不中断整轮对话.
    """
    if not document_datas:
        return []

    binary_docs = [d for d in document_datas if "content_b64" in d]
    plain_docs = [d for d in document_datas if "content_b64" not in d]

    attachment_infos: list[Any] = []
    if binary_docs:
        attachment_infos.extend(
            await prepare_binary_document_attachments(
                user_id=user_id,
                thread_id=thread_id,
                binary_docs=binary_docs,
                round_number=round_number,
            ),
        )

    if not plain_docs:
        return attachment_infos

    logger.info("处理 %s 份纯文本文档", len(plain_docs))

    repository = get_file_repository()

    for doc in plain_docs:
        try:
            attachment_info = await repository.store_document(
                user_id=user_id,
                thread_id=thread_id,
                round_number=round_number,
                filename=doc["filename"],
                content=doc["content"],
            )
            attachment_infos.append(attachment_info)
            logger.info("文档保存成功: %s", attachment_info.internal_path)

            # 代码文件: 后台生成 AI 摘要, desc 更新为 摘要+代码原文
            if is_code_file(doc["filename"]) and attachment_info.file_id:
                spawn_background_task(
                    background_generate_code_summary(
                        file_id=attachment_info.file_id,
                        filename=doc["filename"],
                        content=doc["content"],
                    ),
                )
            # 其余文档: 后台生成 AI 概要, desc 更新为 概要+原文
            elif attachment_info.file_id:
                spawn_background_task(
                    background_generate_document_summary(
                        file_id=attachment_info.file_id,
                        filename=doc["filename"],
                        content=doc["content"],
                    ),
                )
        except (ValueError, OSError) as e:
            logger.warning("⚠️ 文档保存失败 (跳过): %s, %s", doc.get("filename"), e)

    return attachment_infos


__all__ = [
    "OCR_PAGE_LIMIT",
    "allocate_round_number",
    "background_convert_scanned_document",
    "background_generate_code_summary",
    "background_generate_description",
    "background_generate_document_summary",
    "prepare_binary_document_attachments",
    "prepare_document_attachments",
    "prepare_image_attachments",
]
