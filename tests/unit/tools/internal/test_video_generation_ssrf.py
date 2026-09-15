"""VideoGenerationTool SSRF 校验测试.

验证参考视频/音频 URL 经 _add_video_blocks / _add_audio_blocks 时拦截
私网/回环/链路本地/元数据地址 (IP 字面量, 无 DNS 依赖).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.inference.video_generation import GeneratedVideo
from src.tools.internal.video_generation_tool import VideoGenerationTool
from src.tools.shared.tool_runtime import inject_identity


@pytest.fixture
def video_tool() -> VideoGenerationTool:
    tool = VideoGenerationTool()
    inject_identity(tool, "user1", "thread1", "agent1")
    return tool


class TestVideoGenerationSsrf:
    def test_add_video_blocks_rejects_private_ip(self):
        with pytest.raises(ValueError, match="10.0.0.1"):
            VideoGenerationTool._add_video_blocks([], ["http://10.0.0.1/x.mp4"])

    def test_add_video_blocks_rejects_metadata_endpoint(self):
        with pytest.raises(ValueError):
            VideoGenerationTool._add_video_blocks(
                [],
                ["http://169.254.169.254/latest/meta-data/"],
            )

    def test_add_video_blocks_rejects_loopback(self):
        with pytest.raises(ValueError):
            VideoGenerationTool._add_video_blocks([], ["http://127.0.0.1:8080/x.mp4"])

    def test_add_audio_blocks_rejects_private_ip(self):
        with pytest.raises(ValueError):
            VideoGenerationTool._add_audio_blocks([], ["http://10.0.0.1/x.mp3"])

    def test_add_audio_blocks_rejects_metadata_endpoint(self):
        with pytest.raises(ValueError):
            VideoGenerationTool._add_audio_blocks(
                [],
                ["http://169.254.169.254/x.mp3"],
            )

    def test_add_video_blocks_allows_public_ip(self):
        blocks: list = []
        VideoGenerationTool._add_video_blocks(blocks, ["http://8.8.8.8/x.mp4"])
        assert len(blocks) == 1


@pytest.mark.asyncio
async def test_arun_registers_video_with_source_detail(
    video_tool: VideoGenerationTool, tmp_path: Path
) -> None:
    """生成参数 detail 应经 register_tool_output(source=) 落 desc (统一结构)."""
    mock_service = MagicMock()
    mock_service.generate_video = AsyncMock(
        return_value=GeneratedVideo(
            video_data=b"mp4-data",
            mime_type="video/mp4",
            task_id="task-1",
        )
    )
    object.__setattr__(video_tool, "_service", mock_service)

    resolver = MagicMock()
    resolver.get_shared_storage_path.return_value = tmp_path

    mock_reg_result = {
        "success": True,
        "file_id": "abc12345",
        "filename": "clip.mp4",
        "format": "mp4",
        "size_bytes": 8,
    }

    with (
        patch(
            "src.tools.internal.video_generation_tool.get_user_path_resolver",
            return_value=resolver,
        ),
        patch(
            "src.tools.shared.file_output.register_tool_output",
            new=AsyncMock(return_value=mock_reg_result),
        ) as mock_register,
    ):
        result_text = await video_tool._arun(prompt="海浪拍岸", ratio="16:9")

    result = json.loads(result_text)
    assert result["success"] is True
    call_kwargs = mock_register.call_args.kwargs
    # 生成参数 detail 经 source 参数落 desc
    assert "生成提示词: 海浪拍岸" in call_kwargs["source"]
    assert "宽高比: 16:9" in call_kwargs["source"]
