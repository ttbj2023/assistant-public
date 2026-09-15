"""KbReadImageTool 单元测试 - 知识库语料图片按需读图.

测试范围:
1. image_ref(kb:相对路径) 解析与原图读取
2. 路径安全: 拒绝逃逸/绝对路径/缺 kb 前缀
3. 语料根解析: kb_meta.json 优先, BASE_DATA_PATH/{kb} 兜底
4. is_available 廉价检查
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.tools.external.kb_read_image_tool import KbReadImageTool


@pytest.fixture
def tool() -> KbReadImageTool:
    return KbReadImageTool()


def _make_corpus(base: Path) -> Path:
    """在 base/tea 下构造语料图片, 返回语料根."""
    root = base / "tea"
    (root / "中国茶经" / "images").mkdir(parents=True)
    (root / "中国茶经" / "images" / "page0415_028.jpg").write_bytes(b"fake-image")
    return root


def parse_result(result: str) -> dict:
    return json.loads(result)


class TestKbReadImageTool:
    @pytest.mark.asyncio
    async def test_reads_corpus_image_by_ref(self, tool, tmp_path):
        _make_corpus(tmp_path)

        with (
            patch(
                "src.tools.external.kb_read_image_tool.get_base_data_path",
                return_value=tmp_path,
            ),
            patch.object(tool, "_read_image", return_value=("杀青机剖面细节", "m1")),
        ):
            result = await tool._arun(
                image_ref="tea:中国茶经/images/page0415_028.jpg",
                prompt="图里的机器结构",
            )

        data = parse_result(result)
        assert data["success"] is True
        assert data["image_ref"] == "tea:中国茶经/images/page0415_028.jpg"
        assert data["result"] == "杀青机剖面细节"

    @pytest.mark.asyncio
    async def test_rejects_traversal_ref(self, tool, tmp_path):
        _make_corpus(tmp_path)
        with patch(
            "src.tools.external.kb_read_image_tool.get_base_data_path",
            return_value=tmp_path,
        ):
            result = await tool._arun(image_ref="tea:../secret.jpg", prompt="x")
        assert parse_result(result)["success"] is False

    @pytest.mark.asyncio
    async def test_rejects_ref_without_kb_prefix(self, tool, tmp_path):
        result = await tool._arun(
            image_ref="中国茶经/images/page0415_028.jpg", prompt="x"
        )
        assert parse_result(result)["success"] is False

    @pytest.mark.asyncio
    async def test_missing_image_returns_error(self, tool, tmp_path):
        _make_corpus(tmp_path)
        with patch(
            "src.tools.external.kb_read_image_tool.get_base_data_path",
            return_value=tmp_path,
        ):
            result = await tool._arun(
                image_ref="tea:中国茶经/images/none.jpg", prompt="x"
            )
        data = parse_result(result)
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_kb_meta_corpus_root_takes_priority(self, tool, tmp_path):
        """kb_meta.json 记录的语料根优先于 BASE_DATA_PATH/{kb} 约定."""
        _make_corpus(tmp_path)
        # BASE_DATA_PATH 指向空目录(约定根不存在), kb_meta 记录真实语料位置
        empty_base = tmp_path / "elsewhere"
        meta_dir = empty_base / "_knowledge_base" / "tea"
        meta_dir.mkdir(parents=True)
        meta_dir.joinpath("kb_meta.json").write_text(
            json.dumps({"corpus_root": str(tmp_path / "tea")}), encoding="utf-8"
        )

        with (
            patch(
                "src.tools.external.kb_read_image_tool.get_base_data_path",
                return_value=empty_base,
            ),
            patch.object(tool, "_read_image", return_value=("ok", "m1")),
        ):
            result = await tool._arun(
                image_ref="tea:中国茶经/images/page0415_028.jpg", prompt="x"
            )
        assert parse_result(result)["success"] is True

    @pytest.mark.asyncio
    async def test_is_available_true_when_corpus_has_images(self, tool, tmp_path):
        _make_corpus(tmp_path)
        with patch(
            "src.tools.external.kb_read_image_tool.get_base_data_path",
            return_value=tmp_path,
        ):
            assert await tool.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_false_without_corpus(self, tool, tmp_path):
        with patch(
            "src.tools.external.kb_read_image_tool.get_base_data_path",
            return_value=tmp_path,
        ):
            assert await tool.is_available() is False
