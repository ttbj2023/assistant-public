"""微信公众号发布服务编排测试.

验证 run_publish 的正文完整性契约: 正文由主 agent 产出终稿后,
发布管道不再经任何 LLM 重写, 原文直接进入草稿.
文风/格式责任在主 agent (经 skill 挂载约束), 工具只做机械转换.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SERVICE = "src.tools.internal.wechat_publish.service"


def _mock_inference_config() -> MagicMock:
    """构造 inference config mock (含 wechat_publish 与 image_generation)."""
    cfg = MagicMock()
    cfg.wechat_publish.model = "m-analyze"
    cfg.wechat_publish.model_params = {}
    cfg.image_generation.model_id = "m-image"
    return cfg


def _mock_config_service() -> MagicMock:
    """构造渠道配置服务 mock: wechat_mp 凭证齐备 + 默认作者空."""
    service = MagicMock()
    service.get_config_for_channel = AsyncMock(
        return_value={"appid": "wx-app", "secret": "sec", "default_author": ""}
    )
    service.upsert_channel_config = AsyncMock()
    return service


def _mock_client() -> MagicMock:
    """构造微信 API 客户端 mock: 素材上传成功 + 草稿创建成功."""
    client = MagicMock()
    client.upload_media = AsyncMock(
        return_value={"media_id": "m-cover", "url": "http://cdn/cover.png"}
    )
    client.upload_news_draft = AsyncMock(return_value="draft-123")
    return client


def _mock_image_service() -> MagicMock:
    """构造图片生成服务 mock: 返回合法图片字节."""
    img = MagicMock()
    img.generate_image = AsyncMock(
        return_value=SimpleNamespace(image_data=b"fake-png")
    )
    return img


class TestRunPublishContentIntegrity:
    """run_publish 正文完整性: 正文不经 LLM 重写原样进草稿."""

    @pytest.mark.asyncio
    async def test_content_preserved_without_llm_rewrite(self) -> None:
        """LLM 返回分析 JSON 时, 草稿正文仍是用户原文而非 LLM 输出."""
        client = _mock_client()

        with (
            patch(
                _SERVICE + ".invoke_with_fallback",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        content='{"summary": "摘要", "cover_prompt": "封面画面"}'
                    )
                ),
            ),
            patch(
                _SERVICE + ".ImageGenerationService",
                return_value=_mock_image_service(),
            ),
            patch(_SERVICE + ".WechatApiClient", return_value=client),
            patch(
                "src.config.inference_config.get_config",
                return_value=_mock_inference_config(),
            ),
            patch(
                "src.storage.service.user_channel_config_service"
                ".get_user_channel_config_service",
                new=AsyncMock(return_value=_mock_config_service()),
            ),
        ):
            from src.tools.internal.wechat_publish.service import run_publish

            result = await run_publish(
                content="洞察句一.\n\n洞察句二.",
                title="测试标题",
                author=None,
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
            )

        assert result["success"] is True

        articles = client.upload_news_draft.call_args.args[0]
        draft_html = articles[0]["content"]
        assert "洞察句一" in draft_html
        assert "洞察句二" in draft_html
        assert "summary" not in draft_html
        assert "cover_prompt" not in draft_html
