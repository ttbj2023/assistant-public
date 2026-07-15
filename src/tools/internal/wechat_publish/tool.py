"""微信公众号文章推送工具 - 将Markdown文章发布到草稿箱."""

from __future__ import annotations

import json
import logging
from typing import ClassVar, override

from pydantic import ConfigDict, Field

from src.tools.shared.base_internal_tool import BaseInternalTool
from src.tools.shared.query_alias_model import QueryAliasModel

logger = logging.getLogger(__name__)


class WechatPublishInput(QueryAliasModel):
    """微信推送输入."""

    _field_aliases: ClassVar[dict[str, str]] = {"query": "content"}

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    content: str = Field(
        min_length=1,
        max_length=500000,
        description="Markdown格式的文章正文",
    )
    title: str = Field(
        min_length=1,
        max_length=64,
        description="文章标题",
    )
    author: str | None = Field(
        default=None,
        max_length=50,
        description="作者名称(可选, 不填则不显示). 指定后记住为默认值",
    )


class WechatPublishTool(BaseInternalTool):
    """微信公众号推送工具 - 将Markdown文章发布到草稿箱."""

    name: str = "wechat_publish"
    summary: str = "微信公众号推送工具, 将Markdown文章发布到草稿箱"
    search_keywords: ClassVar[list[str]] = [
        "微信公众号",
        "公众号",
        "推送",
        "发布",
        "草稿",
        "文章",
        "wechat",
    ]
    description: str = (
        "微信公众号推送工具, 将Markdown文章发布到公众号草稿箱.\n\n"
        "如有已生成的图片附件, 可在文中用 [file: id] 引用.\n"
        '示例: {"content": "# 文章\\n正文...", "title": "我的文章"}'
    )
    args_schema: type = WechatPublishInput
    timeout: float = 300.0

    @override
    async def is_available(self) -> bool:
        """检查用户是否配置了微信公众号凭证 (appid + secret)."""
        try:
            from src.storage.service.user_channel_config_service import (
                get_user_channel_config_service,
            )

            config_service = await get_user_channel_config_service(
                self.user_id,
                self.thread_id,
                self.agent_id,
            )
            mp_config = await config_service.get_config_for_channel("wechat_mp")
            return bool(
                mp_config and mp_config.get("appid") and mp_config.get("secret")
            )
        except Exception as e:
            logger.warning("检查微信公众号配置失败: %s", e)
            return False

    @override
    async def _arun(
        self,
        content: str,
        title: str,
        author: str | None = None,
    ) -> str:
        try:
            from .service import run_publish

            result = await run_publish(
                content=content,
                title=title,
                author=author,
                user_id=self.user_id,
                thread_id=self.thread_id,
                agent_id=self.agent_id,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.exception("WechatPublishTool 执行失败: %s", e)
            return self._format_error(e)


__all__ = ["WechatPublishInput", "WechatPublishTool"]
