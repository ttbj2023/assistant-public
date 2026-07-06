"""图片生成工具单元测试."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.inference.image_generation import GeneratedImage
from src.tools.internal.image_generation_tool import ImageGenerationTool


@pytest.fixture
def tool() -> ImageGenerationTool:
    return ImageGenerationTool(
        "user1",
        "thread1",
        model_id="doubao:doubao-seedream-5-0-260128",
        agent_id="personal-assistant",
    )


def test_build_filename_rejects_unsafe_name() -> None:
    """非法文件名应被拒绝."""
    with pytest.raises(ValueError, match="文件名包含非法字符"):
        ImageGenerationTool._build_filename("../bad")


@pytest.mark.asyncio
async def test_arun_writes_image_and_calls_register(tool: ImageGenerationTool, tmp_path: Path) -> None:
    """生成成功后应保存图片并调 register_tool_output 统一注册."""
    tool._service.generate_image = AsyncMock(
        return_value=GeneratedImage(image_data=b"png-data", mime_type="image/png")
    )

    resolver = MagicMock()
    resolver.get_shared_storage_path.return_value = tmp_path

    mock_reg_result = {
        "success": True,
        "message": "文件已生成: [file: abc12345] cat.png",
        "file_id": "abc12345",
        "filename": "cat.png",
        "format": "png",
        "size_bytes": 8,
    }

    with (
        patch(
            "src.tools.internal.image_generation_tool.get_user_path_resolver",
            return_value=resolver,
        ),
        patch(
            "src.tools.shared.file_output.register_tool_output",
            new=AsyncMock(return_value=mock_reg_result),
        ) as mock_register,
    ):
        result_text = await tool._arun(
            prompt="画一只猫",
            size="2048x2048",
            filename="cat",
        )

    result = json.loads(result_text)
    assert result["success"] is True
    assert result["file_id"] == "abc12345"
    assert (tmp_path / "cat.png").read_bytes() == b"png-data"
    mock_register.assert_awaited_once()
    call_kwargs = mock_register.call_args.kwargs
    assert call_kwargs["file_type"] == "image"
    assert call_kwargs["output_format"] == "png"
    assert call_kwargs["user_id"] == "user1"
    assert call_kwargs["thread_id"] == "thread1"
