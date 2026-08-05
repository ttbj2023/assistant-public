"""VideoGenerationTool SSRF 校验测试.

验证参考视频/音频 URL 经 _add_video_blocks / _add_audio_blocks 时拦截
私网/回环/链路本地/元数据地址 (IP 字面量, 无 DNS 依赖).
"""

from __future__ import annotations

import pytest

from src.tools.internal.video_generation_tool import VideoGenerationTool


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
