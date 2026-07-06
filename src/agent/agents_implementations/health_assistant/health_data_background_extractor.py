"""后台健康数据自动提取器 - 对话结束后静默提取健康数据.

设计原则:
- 单次 LLM 调用完成检测+分类+转录 (DeepSeek V4 Flash + JSON Mode)
- 不传图片, 依赖主流程的图片描述文本
- Fire-and-forget: 不阻塞主对话流程, 失败仅记录日志
- 不推断营养数据, 只精确转录用户提供的数值
- 纯粹转录, 不做去重判断 (去重由独立审计任务负责)
"""

from __future__ import annotations

import logging
from typing import Any

from src.storage.service.health_data_extraction_service import (
    get_health_data_extraction_service,
)

logger = logging.getLogger(__name__)


class HealthDataBackgroundExtractor:
    """后台健康数据自动提取器.

    单次 DeepSeek Flash 调用提取所有健康数据, 不做推断.
    纯粹从用户输入转录数据, 不查重不去重.
    """

    def __init__(self, user_id: str, thread_id: str, *, agent_id: str) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_id = agent_id

    async def extract_from_conversation(
        self,
        user_message: str,
        attachment_infos: list[Any] | None = None,
        round_number: int | None = None,
    ) -> None:
        """从对话内容中提取健康数据 (后台静默任务主入口).

        Args:
            user_message: 用户原始消息 (已含时间前缀)
            attachment_infos: 附件信息列表 (含AI生成的图片描述)
            round_number: 来源对话轮次

        """
        try:
            from src.inference.health_data_extraction.unified_extractor import (
                UnifiedHealthExtractor,
            )

            extractor = UnifiedHealthExtractor()
            if not extractor.is_available():
                logger.debug("健康数据提取器不可用, 跳过")
                return

            parts = [f"用户消息:\n{user_message}"]

            if attachment_infos:
                image_descriptions = []
                for i, info in enumerate(attachment_infos, 1):
                    desc = getattr(info, "detail", None)
                    if desc and desc != "图片":
                        image_descriptions.append(f"[图片{i}描述]: {desc}")
                if image_descriptions:
                    parts.append("图片内容:\n" + "\n".join(image_descriptions))

            combined_text = "\n\n".join(parts)

            results = await extractor.extract(combined_text)

            if not results:
                logger.debug(
                    f"后台健康数据检测: 无健康数据 ({self.user_id}:{self.thread_id})",
                )
                return

            logger.info(
                f"后台健康数据提取: 发现 {len(results)} 条 "
                f"({self.user_id}:{self.thread_id})",
            )

            await self._store_results(results, round_number)

        except Exception as e:
            logger.warning(
                f"后台健康数据提取异常 ({self.user_id}:{self.thread_id}): {e}",
            )

    async def _store_results(
        self,
        results: list[Any],
        round_number: int | None = None,
    ) -> None:
        """存储提取结果到数据库."""
        service = get_health_data_extraction_service(
            self.user_id,
            self.thread_id,
            agent_id=self.agent_id,
        )

        for result in results:
            try:
                store_result = await service.store_extraction(
                    data_type=result.data_type,
                    data=result.data,
                    round_number=round_number,
                )
                if store_result.get("success"):
                    logger.info(
                        f"后台健康数据存储成功: {result.data_type} "
                        f"({self.user_id}:{self.thread_id})",
                    )
                else:
                    logger.warning(
                        f"后台健康数据存储失败: {store_result.get('error')} "
                        f"({self.user_id}:{self.thread_id})",
                    )
            except Exception as e:
                logger.warning(f"后台健康数据存储异常 ({result.data_type}): {e}")
