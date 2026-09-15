"""MarkdownChunker 单元测试 - 结构感知分块.

测试范围:
1. 标题链构建(嵌套标题 → 层级链)
2. 多节切分(每块携带标题链上下文)
3. 超长节二次切分(重叠 + 共享标题链)
4. 前言(首个标题前的内容)独立成块
5. 空文本返回空列表
"""

from __future__ import annotations

from src.knowledge_base.chunker import MarkdownChunker


class TestHeadingChain:
    def test_nested_headings_build_chain(self):
        text = (
            "# 中国茶经\n\n"
            "总述内容。\n\n"
            "## 第一章 茶树\n\n"
            "茶树总述。\n\n"
            "### 一、品种\n\n"
            "品种内容。"
        )
        chunks = MarkdownChunker().chunk(text)
        variety = [c for c in chunks if "品种内容" in c.page_content]
        assert len(variety) == 1
        assert (
            variety[0].metadata["heading_chain"] == "中国茶经 / 第一章 茶树 / 一、品种"
        )


class TestMultiSection:
    def test_sibling_sections_each_carry_chain(self):
        text = "# 书\n\n## 甲\n\n甲内容。\n\n## 乙\n\n乙内容。"
        chunks = MarkdownChunker().chunk(text)
        assert len(chunks) == 2
        assert chunks[0].metadata["heading_chain"] == "书 / 甲"
        assert chunks[1].metadata["heading_chain"] == "书 / 乙"
        assert chunks[0].metadata["section_title"] == "甲"

    def test_chunk_content_carries_context_prefix(self):
        text = "# 书\n\n## 甲\n\n甲内容。"
        chunks = MarkdownChunker().chunk(text)
        section = next(c for c in chunks if "甲内容" in c.page_content)
        assert section.page_content.startswith("[章节] 书 / 甲")

    def test_sibling_resets_deeper_level(self):
        text = "# 书\n\n## 甲\n\n### 子甲\n\n子甲内容。\n\n## 乙\n\n乙内容。"
        chunks = MarkdownChunker().chunk(text)
        zi = next(c for c in chunks if "子甲内容" in c.page_content)
        yi = next(c for c in chunks if "乙内容" in c.page_content)
        assert zi.metadata["heading_chain"] == "书 / 甲 / 子甲"
        # 乙 与 甲 同级, 不应继承 甲 的子标题
        assert yi.metadata["heading_chain"] == "书 / 乙"


class TestPreamble:
    def test_content_before_first_heading_is_own_chunk(self):
        text = "这是前言, 没有标题。\n\n# 第一章\n\n正文。"
        chunks = MarkdownChunker().chunk(text)
        preamble = [c for c in chunks if "前言" in c.page_content]
        assert len(preamble) == 1
        assert preamble[0].metadata["heading_chain"] == ""
        assert not preamble[0].page_content.startswith("[章节]")


class TestOversizedSplit:
    def test_long_section_split_sharing_chain(self):
        para = "茶" * 300
        body = "\n\n".join([para] * 6)  # ~1800 字, 超默认 800
        text = f"# 书\n\n## 长节\n\n{body}"
        chunks = MarkdownChunker().chunk(text)
        long_chunks = [c for c in chunks if "长节" in c.metadata["heading_chain"]]
        assert len(long_chunks) > 1
        assert all(c.metadata["heading_chain"] == "书 / 长节" for c in long_chunks)
        assert all(len(c.page_content) <= 800 + 100 for c in long_chunks)

    def test_small_section_not_split(self):
        text = "# 书\n\n## 短节\n\n短内容。"
        chunks = MarkdownChunker().chunk(text)
        assert len(chunks) == 1


class TestEmpty:
    def test_empty_string_returns_empty(self):
        assert MarkdownChunker().chunk("") == []

    def test_whitespace_only_returns_empty(self):
        assert MarkdownChunker().chunk("   \n\n  ") == []

    def test_chunk_index_is_sequential(self):
        text = "# 书\n\n## 甲\n\n甲。\n\n## 乙\n\n乙。"
        chunks = MarkdownChunker().chunk(text)
        assert [c.metadata["chunk_index"] for c in chunks] == [0, 1]


class TestImageStripping:
    def test_chunk_with_image_line_image_removed_caption_kept(self):
        text = (
            "# 书\n\n## 图版\n\n"
            "真茶与假茶的区别。\n\n"
            "![图版](../images/page0806_174.jpg)\n\n"
            "**茶树叶片上叶脉的分布**"
        )
        chunks = MarkdownChunker().chunk(text)
        content = "\n".join(c.page_content for c in chunks)
        assert "![" not in content
        assert "page0806_174" not in content
        # 图注与正文是独立行, 剥离图片行后仍保留
        assert "茶树叶片上叶脉的分布" in content
        assert "真茶与假茶" in content


class TestImageAnchors:
    def test_section_with_image_path_preserved_in_metadata(self):
        text = (
            "# 书\n\n## 图版\n\n"
            "真茶与假茶的区别。\n\n"
            "![图版](../images/page0806_174.jpg)\n\n"
            "**茶树叶片上叶脉的分布**"
        )
        chunks = MarkdownChunker().chunk(text)
        section = next(c for c in chunks if "真茶与假茶" in c.page_content)
        assert section.metadata["images"] == "../images/page0806_174.jpg"

    def test_images_scoped_to_own_section(self):
        text = (
            "# 书\n\n## 甲\n\n![图一](../images/a1.jpg)\n\n甲内容。\n\n"
            "## 乙\n\n![图二](../images/b2.jpg)\n\n乙内容。"
        )
        chunks = MarkdownChunker().chunk(text)
        jia = next(c for c in chunks if "甲内容" in c.page_content)
        yi = next(c for c in chunks if "乙内容" in c.page_content)
        assert jia.metadata["images"] == "../images/a1.jpg"
        assert yi.metadata["images"] == "../images/b2.jpg"

    def test_multiple_images_in_section_comma_joined(self):
        text = "# 书\n\n## 图集\n\n![a](../images/x.jpg)\n\n正文。\n\n![b](../images/y.jpg)"
        chunks = MarkdownChunker().chunk(text)
        section = next(c for c in chunks if "正文" in c.page_content)
        assert section.metadata["images"] == "../images/x.jpg,../images/y.jpg"

    def test_section_without_images_has_no_images_key(self):
        text = "# 书\n\n## 甲\n\n甲内容。"
        chunks = MarkdownChunker().chunk(text)
        assert "images" not in chunks[0].metadata

    def test_oversized_section_all_pieces_carry_images(self):
        para = "茶" * 300
        body = "\n\n".join([para] * 6)
        text = f"# 书\n\n## 长节\n\n![图](../images/big.jpg)\n\n{body}"
        chunks = MarkdownChunker().chunk(text)
        long_chunks = [c for c in chunks if "长节" in c.metadata["heading_chain"]]
        assert len(long_chunks) > 1
        assert all(c.metadata["images"] == "../images/big.jpg" for c in long_chunks)


class TestHardSplitLineIntegrity:
    """hard_split 兜底路径的行完整性: 绝不在行中间截断多行块."""

    def test_mixed_paragraph_table_oversized_no_row_truncated(self):
        """段落+表格混合累积超限后, pipe 行不被拦腰截断, 且行完整出现在某块."""
        table = (
            "<table><tr><td>刊名</td><td>备注</td></tr>"
            "<tr><td>茶业研究报告</td><td>主要刊登茶树育种、栽培、病虫及其防治、茶叶加工、"
            "贮藏、化学成分分析，以及经营管理等方面的研究论文、简报、资料、综述和文摘</td></tr>"
            "<tr><td>Sri Lanka Journal of Tea Science</td><td>系学术性刊物，"
            "主要刊登茶叶各学科的研究报告和学术论文</td></tr></table>"
        )
        para = "茶" * 700
        text = f"# 书\n\n## 刊物\n\n{para}\n\n{table}"
        chunks = MarkdownChunker().chunk(text)
        content = "\n".join(c.page_content for c in chunks)
        # 完整行存在(未截断)
        assert (
            "| Sri Lanka Journal of Tea Science | 系学术性刊物，"
            "主要刊登茶叶各学科的研究报告和学术论文 |" in content
        )
        # 无半截 pipe 行
        for c in chunks:
            for ln in c.page_content.split("\n"):
                s = ln.strip()
                assert not (s.startswith("|") and not s.endswith("|")), f"截断行: {s}"

    def test_oversized_single_table_row_not_cut_mid_row(self):
        """单行超长的表格行(编年史单元格), 允许整行成块但不从行中间切开."""
        long_cell = "1949年12月，中国茶叶公司在北京成立。" + "历年大事记内容。" * 130
        table = (
            "<table><tr><td>时期</td><td>大事记</td></tr>"
            f"<tr><td>中华人民共和国</td><td>{long_cell}</td></tr></table>"
        )
        text = f"# 书\n\n## 大事记\n\n{table}"
        chunks = MarkdownChunker().chunk(text)
        assert len(long_cell) > 800  # 前提: 单行确实超限
        for c in chunks:
            for ln in c.page_content.split("\n"):
                s = ln.strip()
                assert not (s.startswith("|") and not s.endswith("|")), f"截断行: {s}"
        # 超长行的完整内容分布在块中可重组
        joined = "\n".join(c.page_content for c in chunks)
        assert "中国茶叶公司在北京成立" in joined

    def test_hard_split_multiline_cuts_at_line_boundaries(self):
        """多行块超限走行边界切分, 行绝不在行中被切开(直测 _hard_split)."""
        long_row = "| 长行 | " + "数据" * 200 + " |"
        lines = ["甲" * 500, "| 表头 | 值 |", long_row, "乙" * 300]
        text = "\n".join(lines)
        pieces = MarkdownChunker()._hard_split(text)
        assert len(pieces) > 1
        # 超长行必须完整存在于某一片(未被行中切开)
        assert any(long_row in piece for piece in pieces)

    def test_plain_paragraph_oversized_still_windowed(self):
        """无换行的纯文本长段维持字符级滑窗(带 overlap)."""
        text = "# 书\n\n## 甲\n\n" + "茶学内容。" * 300
        chunks = MarkdownChunker().chunk(text)
        assert len(chunks) > 1
        assert all(len(c.page_content) <= 800 + 100 for c in chunks)


class TestTableConversion:
    def test_single_line_html_table_converted_to_pipe_rows(self):
        text = (
            "# 书\n\n## 名茶表\n\n"
            "<table><tr><td>茶名</td><td>产地</td></tr>"
            "<tr><td>龙井茶</td><td>杭州</td></tr>"
            "<tr><td>蒙顶茶</td><td>四川雅安</td></tr></table>"
        )
        chunks = MarkdownChunker().chunk(text)
        content = "\n".join(c.page_content for c in chunks)
        assert "<table>" not in content
        assert "<td>" not in content
        assert "| 茶名 | 产地 |" in content
        assert "| 龙井茶 | 杭州 |" in content
        # 首行(表头)后补 GFM 分隔行
        assert "| --- | --- |" in content


class TestOversizedTableSplit:
    def test_oversized_table_split_at_row_boundary_with_header_repeated(self):
        header = "<tr><td>茶名</td><td>产地</td></tr>"
        rows = "".join(
            f"<tr><td>第{i}号名茶</td><td>{'某产地' * 20}</td></tr>" for i in range(60)
        )
        text = f"# 书\n\n## 名茶表\n\n<table>{header}{rows}</table>"
        chunks = MarkdownChunker().chunk(text)
        table_chunks = [c for c in chunks if "| 茶名 | 产地 |" in c.page_content]
        assert len(table_chunks) > 1
        for c in table_chunks:
            piece = c.page_content.split("\n\n", 1)[1]  # 去掉 [章节] 前缀
            lines = piece.split("\n")
            # 每个续块都带表头 + 分隔行
            assert lines[0] == "| 茶名 | 产地 |"
            assert lines[1] == "| --- | --- |"
            # 无行被截断: 每行都是完整 pipe 行
            assert all(ln.startswith("|") and ln.endswith("|") for ln in lines)
        # 数据行全部完整保留
        all_lines = "\n".join(c.page_content for c in table_chunks)
        assert "| 第59号名茶 |" in all_lines


class TestTableMixedWithProse:
    def test_mixed_content_chain_and_rows_intact(self):
        para = "茶" * 300
        header = "<tr><td>茶名</td><td>产地</td></tr>"
        rows = "".join(
            f"<tr><td>第{i}号名茶</td><td>{'某产地' * 20}</td></tr>" for i in range(60)
        )
        table = f"<table>{header}{rows}</table>"
        text = f"# 书\n\n## 混排\n\n{para}\n\n{table}\n\n{para}"
        chunks = MarkdownChunker().chunk(text)
        # 标题链不因表格切分而混乱
        assert all(c.metadata["heading_chain"] == "书 / 混排" for c in chunks)
        # 所有 pipe 行完整(跨块无截断行)
        pipe_lines = [
            ln for c in chunks for ln in c.page_content.split("\n") if "|" in ln
        ]
        assert pipe_lines
        assert all(
            ln.strip().startswith("|") and ln.strip().endswith("|") for ln in pipe_lines
        )
