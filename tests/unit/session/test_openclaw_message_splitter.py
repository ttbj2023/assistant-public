"""openclaw_message_splitter 单元测试.

覆盖:
- split_message 拆分逻辑 (短文本不拆 / 长文本拆分 / 标记格式)
- send_openclaw_followup 流程 (配置缺失 / send_message 成功 / 失败)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.session.openclaw_message_splitter import (
    send_openclaw_followup,
    split_message,
)


class TestSplitMessage:
    """split_message 拆分逻辑测试."""

    def test_short_text_no_split(self):
        text = "短文本"
        parts = split_message(text)
        assert len(parts) == 1
        assert parts[0] == text

    def test_empty_text(self):
        parts = split_message("")
        assert len(parts) == 1
        assert parts[0] == ""

    def test_long_text_splits_at_paragraph(self):
        para1 = "A" * 1500
        para2 = "B" * 1500
        text = f"{para1}\n\n{para2}"
        parts = split_message(text, limit=2000)
        assert len(parts) == 2
        # 第一段是 para1, 第二段是 para2
        assert parts[0].startswith(f"{para1}\n\n[1/2]")
        assert parts[1].startswith("[2/2]\n" + para2)

    def test_long_text_has_index_markers(self):
        """长文本拆分后每段含 [N/total] 标记."""
        para1 = "X" * 1500
        para2 = "Y" * 1500
        text = f"{para1}\n\n{para2}"
        parts = split_message(text, limit=2000)
        assert len(parts) == 2
        assert "[1/2]" in parts[0]
        assert "[2/2]" in parts[1]

    def test_three_way_split(self):
        """三段式拆分: 每段在 limit 内, 总长超 limit."""
        para1 = "A" * 1500
        para2 = "B" * 1500
        para3 = "C" * 1500
        text = f"{para1}\n\n{para2}\n\n{para3}"
        parts = split_message(text, limit=2000)
        # 每段 1500 < limit 2000, 按段落断开, 应该是 3 段
        assert len(parts) == 3
        assert "[1/3]" in parts[0]
        assert "[2/3]" in parts[1]
        assert "[3/3]" in parts[2]

    def test_no_overshort_first_part_with_heading(self):
        """回归: 开头有 H1+分隔线+H2+长主体时, 第一段至少 limit*0.7 字符.

        历史 bug: 旧实现把 Markdown 标题优先级置于位置优先级之上,
        会在文本开头的 H2 前切, 导致第一段只有 10~20 字符.
        """
        text = "# 概览\n短摘要\n---\n## 详细\n" + "a" * 2100
        parts = split_message(text, limit=2000)
        assert len(parts) >= 2
        first_content = parts[0].split("\n\n[")[0]
        assert len(first_content) >= int(2000 * 0.7)

    def test_no_overshort_first_part_with_dash_separator(self):
        """回归: 开头短摘要+---+长内容, 第一段至少 limit*0.7 字符."""
        text = "短摘要\n---\n" + "a" * 2100
        parts = split_message(text, limit=2000)
        assert len(parts) >= 2
        first_content = parts[0].split("\n\n[")[0]
        assert len(first_content) >= int(2000 * 0.7)

    def test_no_overshort_first_part_single_short_line(self):
        """回归: 单行短内容+长主体, 第一段至少 limit*0.7 字符."""
        text = "简短一句\n" + "a" * 2100
        parts = split_message(text, limit=2000)
        assert len(parts) >= 2
        first_content = parts[0].split("\n\n[")[0]
        assert len(first_content) >= int(2000 * 0.7)

    def test_sentence_break_preferred_over_hard_cut(self):
        """句末标点优先于硬截断: 切割点应落在窗口内的句末标点之后."""
        text = "x" * 1899 + "。后续内容" + "y" * 300
        parts = split_message(text, limit=2000)
        assert len(parts) == 2
        first_content = parts[0].split("\n\n[")[0]
        assert first_content.endswith("。")

    def test_paragraph_break_preferred_over_sentence(self):
        """段落分隔优先于句末标点: 同时存在时选段落分隔."""
        text = "x" * 1500 + "。\n\n" + "y" * 1500 + "。"
        parts = split_message(text, limit=2000)
        assert len(parts) == 2
        first_content = parts[0].split("\n\n[")[0]
        assert first_content.endswith("。")
        assert len(first_content) < 2000

    def test_code_block_protected_from_split(self):
        """代码块内部不允许切割, 切口应落在代码块之外 (每段 fence 数为偶数)."""
        code_block = (
            "```python\n" + "\n".join([f"line {i}" for i in range(50)]) + "\n```"
        )
        text = "前导内容 " * 200 + "\n\n" + code_block + "\n\n" + "z" * 800
        parts = split_message(text, limit=2000)
        assert len(parts) >= 2
        for part in parts:
            stripped = part.rstrip()
            assert stripped.count("```") % 2 == 0, (
                f"fence 数量为奇数, 代码块被拆开: tail={stripped[-80:]!r}"
            )

    def test_protected_ranges_recomputed_per_iteration(self):
        """多段切割时, 后续段的代码块仍受保护 (回归 protected_ranges 偏移量 bug).

        构造两块代码块在 limit 之后, 验证每段 fence 数仍为偶数.
        """
        text = (
            "x" * 1900
            + "\n\n```\ncode1\n```\n\n"
            + "y" * 1900
            + "\n\n```\ncode2\n```\n\n"
            + "z" * 500
        )
        parts = split_message(text, limit=2000)
        assert len(parts) >= 2
        for part in parts:
            stripped = part.rstrip()
            assert stripped.count("```") % 2 == 0, (
                f"fence 数量为奇数, 代码块被拆开: tail={stripped[-80:]!r}"
            )

    def test_large_table_crossing_limit_kept_intact(self):
        """大表格 (<= limit 但跨窗口边界) 应整体成段, 不被内部切割.

        回归: 旧实现把整个表格作为一个 protected range, 当表格跨 limit 时
        窗口内全被覆盖, 回退到硬截断, 把一行表格拆成两半.
        """
        header = "| 列1 | 列2 | 列3 |\n|---|---|---|\n"
        rows = "".join(
            [f"| 数据{i:03d} | 内容{i} | 备注{i} |\n" for i in range(40)],
        )
        table = header + rows
        text = "前导内容\n\n" + table + "\n后续内容"
        parts = split_message(text, limit=2000)
        for part in parts:
            content = part
            if part.startswith("["):
                content = part.split("]\n", 1)[1]
            else:
                content = part.split("\n\n[")[0]
            lines = content.split("\n")
            table_lines = [ln for ln in lines if ln.startswith("|")]
            for line in table_lines:
                assert line.endswith("|"), f"表格行被拆开 (不以 | 结尾): {line!r}"

    def test_oversize_table_split_with_header(self):
        """超大表格 (> limit) 自动拆为多个表格, 每段都补齐表头."""
        header = "| col1 | col2 | col3 |\n|---|---|---|\n"
        rows = "".join(
            [f"| row_{i:03d} | data_{i}_long | note_{i} |\n" for i in range(150)],
        )
        table = header + rows
        text = "前导\n\n" + table + "\n\n后续"
        parts = split_message(text, limit=2000)

        table_parts = []
        for part in parts:
            content = part.split("\n\n[")[0] if "\n\n[" in part else part
            if part.startswith("["):
                content = part.split("]\n", 1)[1]
            if content.lstrip().startswith("|"):
                table_parts.append(content)

        assert len(table_parts) >= 2, "大表格应该被拆为至少 2 个独立表格"
        for tp in table_parts:
            assert tp.startswith("| col1"), f"表格段缺少表头: {tp[:60]!r}"
            assert "|---|---|---|" in tp, f"表格段缺少分隔行: {tp[:60]!r}"
            assert len(tp) <= 2000, f"表格段超过 limit: {len(tp)}"

    def test_oversize_table_no_row_split(self):
        """超大表格拆分时, 单行不被拆到中间."""
        header = "| col1 | col2 |\n|---|---|\n"
        rows = "".join([f"| row_{i:04d} | data |\n" for i in range(200)])
        text = header + rows
        parts = split_message(text, limit=2000)

        for part in parts:
            content = part.split("]\n", 1)[1] if part.startswith("[") else part
            content = content.split("\n\n[")[0]
            for line in content.split("\n"):
                if line.startswith("|"):
                    assert line.endswith("|"), f"表格行被拆开: {line!r}"

    def test_large_code_block_crossing_limit_kept_intact(self):
        """大代码块 (<= limit 但跨窗口) 整体成段, fence 数为偶数."""
        code = "```python\n" + "\n".join([f"line {i}" for i in range(80)]) + "\n```"
        text = "前导 " * 200 + "\n\n" + code + "\n\n后续 " * 100
        parts = split_message(text, limit=2000)
        for part in parts:
            stripped = part.rstrip()
            if "```" in stripped:
                assert stripped.count("```") % 2 == 0, (
                    f"fence 数量为奇数: tail={stripped[-80:]!r}"
                )

    def test_oversize_code_split_with_fence(self):
        """超大代码块 (> limit) 按行拆分, 每段都补齐 ``` fence."""
        code_inner = "\n".join([f"line {i}: " + "x" * 50 for i in range(120)])
        text = "```python\n" + code_inner + "\n```"
        parts = split_message(text, limit=2000)

        code_parts = []
        for part in parts:
            content = part.split("]\n", 1)[1] if part.startswith("[") else part
            content = content.split("\n\n[")[0]
            if "```" in content:
                code_parts.append(content)

        assert len(code_parts) >= 2, "超大代码块应拆为至少 2 段"
        for cp in code_parts:
            assert cp.startswith("```"), f"代码段缺少开 fence: {cp[:40]!r}"
            assert cp.rstrip().endswith("```"), f"代码段缺少闭 fence: tail={cp[-40:]!r}"
            assert len(cp) <= 2000, f"代码段超 limit: {len(cp)}"

    def test_oversize_code_preserves_language_hint(self):
        """超大代码块拆分后, 每段都保留 ```python 等语言标记."""
        code_inner = "\n".join([f"echo line {i}" + " x" * 30 for i in range(150)])
        text = "```bash\n" + code_inner + "\n```"
        parts = split_message(text, limit=2000)
        for part in parts:
            content = part.split("]\n", 1)[1] if part.startswith("[") else part
            content = content.split("\n\n[")[0]
            if "```" in content:
                assert content.startswith("```bash"), f"丢失语言标记: {content[:40]!r}"

    def test_math_block_detected_and_kept_intact(self):
        """数学块 ($$...$$) 应被识别为 atomic, 不被内部切割."""
        inner = "E = mc^2\n" + "x" * 500 + "\n" + "y" * 500
        text = "前导 " * 200 + "\n\n$$\n" + inner + "\n$$\n\n" + "后续 " * 100
        parts = split_message(text, limit=2000)
        for part in parts:
            stripped = part.rstrip()
            if "$$" in stripped:
                count = stripped.count("$$")
                assert count % 2 == 0, (
                    f"$$ 数量为奇数, 数学块被拆开: tail={stripped[-80:]!r}"
                )

    def test_oversize_math_split_with_fence(self):
        """超大数学块 (> limit) 拆分, 每段都补齐 $$ fence."""
        inner_lines = "\n".join([
            f"a_{i} = b_{i} + c_{i}" + " + d" * 10 for i in range(150)
        ])
        text = "$$\n" + inner_lines + "\n$$"
        parts = split_message(text, limit=2000)

        math_parts = []
        for part in parts:
            content = part.split("]\n", 1)[1] if part.startswith("[") else part
            content = content.split("\n\n[")[0]
            if "$$" in content:
                math_parts.append(content)

        assert len(math_parts) >= 2, "超大数学块应拆为至少 2 段"
        for mp in math_parts:
            assert mp.startswith("$$"), f"数学段缺少开 fence: {mp[:40]!r}"
            assert mp.rstrip().endswith("$$"), f"数学段缺少闭 fence: tail={mp[-40:]!r}"

    def test_table_followed_by_long_text(self):
        """表格 + 长文本: 表格整体保留, 长文本单独成段."""
        header = "| 列1 | 列2 |\n|---|---|\n"
        rows = "".join([f"| 数据{i} | 内容{i} |\n" for i in range(15)])
        table = header + rows
        text = table + "\n后续 " + "x" * 2500
        parts = split_message(text, limit=2000)
        assert len(parts) >= 2
        for part in parts:
            content = part.split("]\n", 1)[1] if part.startswith("[") else part
            content = content.split("\n\n[")[0]
            table_lines = [ln for ln in content.split("\n") if ln.startswith("|")]
            for line in table_lines:
                assert line.endswith("|"), f"表格行被拆开: {line!r}"

    def test_code_then_table_in_sequence(self):
        """连续的代码块 + 表格: 各自整体保留, 互不干扰."""
        code = "```python\nprint('hello')\n```"
        header = "| 列1 | 列2 |\n|---|---|\n"
        rows = "".join([f"| 数据{i} | 内容{i} |\n" for i in range(30)])
        table = header + rows
        text = "前导 " * 100 + "\n\n" + code + "\n\n" + table + "\n\n后续 " + "z" * 1500
        parts = split_message(text, limit=2000)
        for part in parts:
            stripped = part.rstrip()
            assert stripped.count("```") % 2 == 0, (
                f"代码块被拆开: tail={stripped[-80:]!r}"
            )
            content = part.split("]\n", 1)[1] if part.startswith("[") else part
            content = content.split("\n\n[")[0]
            for line in content.split("\n"):
                if line.startswith("|"):
                    assert line.endswith("|"), f"表格行被拆开: {line!r}"


class TestSendFollowup:
    """send_openclaw_followup 流程测试."""

    @pytest.fixture(autouse=True)
    def _skip_initial_sleep(self):
        """mock 掉 send_openclaw_followup 入口的 3s 等待, 避免拖慢单测."""
        with patch(
            "src.session.openclaw_message_splitter.asyncio.sleep",
            new=AsyncMock(),
        ):
            yield

    @pytest.mark.asyncio
    async def test_empty_parts_returns_immediately(self):
        """parts 为空时立即返回."""
        result = await send_openclaw_followup("user-1", "main", "personal-assistant", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_no_channel_config_skips(self):
        """无渠道配置 (resolve_delivery 返回 None), 警告日志后跳过."""
        with patch(
            "src.core.notification.resolve_delivery",
            new=AsyncMock(return_value=None),
        ):
            await send_openclaw_followup("user-1", "main", "personal-assistant", ["part1"])

    @pytest.mark.asyncio
    async def test_incomplete_config_skips(self):
        """渠道配置不完整 (resolve_delivery 返回 None), 警告日志后跳过."""
        with patch(
            "src.core.notification.resolve_delivery",
            new=AsyncMock(return_value=None),
        ):
            await send_openclaw_followup("user-1", "main", "personal-assistant", ["part1"])

    @pytest.mark.asyncio
    async def test_send_success(self):
        """正常发送场景."""
        from src.core.notification import DeliverySpec

        delivery = DeliverySpec(
            method="wechat",
            openclaw_channel="openclaw-weixin",
            account_id="bot-1",
            target="user-123",
        )
        mock_notifier = MagicMock()
        mock_notifier.send = AsyncMock(return_value=True)

        with (
            patch(
                "src.core.notification.resolve_delivery",
                new=AsyncMock(return_value=delivery),
            ),
            patch(
                "src.core.notification.get_notification_service",
                return_value=mock_notifier,
            ),
        ):
            await send_openclaw_followup(
                "user-1", "main", "personal-assistant", ["part1", "part2"]
            )

        # 应该调了两次 send
        assert mock_notifier.send.await_count == 2
        first_args = mock_notifier.send.await_args_list[0].args
        assert first_args[0] is delivery
        assert first_args[1] == "part1\n\u200b"

    @pytest.mark.asyncio
    async def test_send_partial_failure(self):
        """部分失败场景: 第一段成功, 第二段失败."""
        from src.core.notification import DeliverySpec

        delivery = DeliverySpec(
            method="wechat",
            openclaw_channel="openclaw-weixin",
            account_id="bot-1",
            target="user-123",
        )
        mock_notifier = MagicMock()
        mock_notifier.send = AsyncMock(side_effect=[True, False])

        with (
            patch(
                "src.core.notification.resolve_delivery",
                new=AsyncMock(return_value=delivery),
            ),
            patch(
                "src.core.notification.get_notification_service",
                return_value=mock_notifier,
            ),
        ):
            await send_openclaw_followup(
                "user-1", "main", "personal-assistant", ["part1", "part2"]
            )

        assert mock_notifier.send.await_count == 2
