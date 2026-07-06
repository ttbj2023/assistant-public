"""会话消息执行编排 (业务逻辑, 非核心基础设施).

原位于 src/core/chat_helpers.py, 现迁至 session 编排层. 这些函数依赖
files/inference/storage, 属于上层编排职责, 不应放在叶子层 (core).

包含:
- allocate_round_number: 对话轮次号分配
- prepare_image_attachments: 多模态图片附件准备与异步描述生成
- background_generate_description: 后台图片描述生成 (由 spawn_background_task 触发)

纯展示格式化函数 (format_user_message_with_attachments / build_file_links /
build_media_lines) 已拆分至 src/utils/message_formatting.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.core.path_resolver import get_user_path_resolver
from src.files.paths import FILES_IMAGES
from src.files.repository import get_file_repository
from src.inference.image_description.describer import ImageDescriber
from src.storage.service import create_conversation_service
from src.utils.async_utils import spawn_background_task

logger = logging.getLogger(__name__)


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


__all__ = [
    "allocate_round_number",
    "background_generate_description",
    "prepare_image_attachments",
]
