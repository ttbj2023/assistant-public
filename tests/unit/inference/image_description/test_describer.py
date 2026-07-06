"""ImageDescriber 单元测试.

从 tests/unit/storage/service/test_attachment_service.py 迁移.
覆盖 _extract_description_from_response 的 JSON/纯文本/代码块/空值解析.
"""

from __future__ import annotations

from src.inference.image_description.describer import ImageDescriber


class TestExtractDescriptionFromResponse:
    """测试_extract_description_from_response - 模型响应解析."""

    def test_extract_description_from_json_response(self):
        """应从JSON格式响应中提取brief和detail字段."""
        describer = ImageDescriber()
        response = '{"brief": "橘猫照片", "detail": "这是一张橘猫的照片"}'
        result = describer._extract_description_from_response(response)

        assert result == ("橘猫照片", "这是一张橘猫的照片")

    def test_extract_description_from_json_with_extra_text(self):
        """应从包含额外文本的响应中提取JSON中的brief/detail."""
        describer = ImageDescriber()
        response = '分析结果如下:\n{"brief": "表格", "detail": "一个表格图片"}\n以上是分析结果.'
        result = describer._extract_description_from_response(response)

        assert result == ("表格", "一个表格图片")

    def test_extract_description_from_plain_text(self):
        """非JSON响应应返回清理后的文本作为brief和detail."""
        describer = ImageDescriber()
        response = "这张图片显示了一个红色汽车"
        result = describer._extract_description_from_response(response)

        assert result == ("这张图片显示了一个红色汽车", "这张图片显示了一个红色汽车")

    def test_extract_description_from_code_block(self):
        """应去除代码块标记, 兼容旧 description 字段."""
        describer = ImageDescriber()
        response = '```json\n{"description": "风景照"}\n```'
        result = describer._extract_description_from_response(response)

        brief, detail = result
        assert "风景照" in detail

    def test_extract_description_from_empty_string(self):
        """空字符串应返回空元组."""
        describer = ImageDescriber()
        result = describer._extract_description_from_response("")

        assert result == ("", "")

    def test_extract_description_from_whitespace_only(self):
        """纯空白字符串应返回空元组."""
        describer = ImageDescriber()
        result = describer._extract_description_from_response("   \n  ")

        assert result == ("", "")

    def test_extract_description_from_json_without_description_key(self):
        """JSON中没有brief/detail键时应尝试返回整个JSON匹配."""
        describer = ImageDescriber()
        response = '{"text": "这是一段描述"}'
        result = describer._extract_description_from_response(response)

        assert result is not None
