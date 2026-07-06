"""MemoryAssembler 记忆组装集成测试.

验证记忆组装器协调多数据源 (ConversationService/MemoryService/TodoService) +
字符预算分配 + 缓存协同的真实行为, 补充单元测试过度 Mock 的部分:

- 首轮空历史边界
- 4 部分组装格式 (pinned XML / Human-AI 交替历史 / TODO markdown)
- 字符预算分配 (主历史/索引区独立预算) 与索引区伪对话轮生成
- pinned 缓存命中跳过 DB
- 增量 fetch (新轮次不全量重读)

测试策略: 灰盒且零外部 Mock - 全部真实组件 (MemoryAssembler / ConversationService /
MemoryService / TodoService / SQLite / SplittableMemoryCache), 组装过程不调 LLM、
不涉及向量, 故无需任何 Mock, 是真实度最高的集成测试.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.memory.local_memory.assembler import MemoryAssembler
from src.config.agent_config import AgentConfig, AgentMemoryConfig
from src.storage.models.simple_pinned_memory import SimplePinnedMemoryType
from src.storage.service.service_factory import (
    create_conversation_service,
    create_memory_service,
    create_todo_service,
)

_AGENT_ID = "test-agent"


def _make_assembler(
    user_id: str,
    thread_id: str,
    *,
    include_todo: bool = True,
) -> MemoryAssembler:
    """构造真实 MemoryAssembler 实例 (include_todo 默认启用以便验证 TODO 部分)."""
    return MemoryAssembler(
        agent_id=_AGENT_ID,
        agent_config=AgentConfig(
            agent_id=_AGENT_ID,
            memory=AgentMemoryConfig(include_todo_in_context=include_todo),
        ),
        user_id=user_id,
        thread_id=thread_id,
    )


async def _seed_conversations(
    user_id: str,
    thread_id: str,
    count: int,
    *,
    msg_len: int = 40,
) -> None:
    """预置 count 轮真实对话 (每轮 user/assistant 各 msg_len 字符)."""
    conv_service = await create_conversation_service(
        user_id, thread_id, agent_id=_AGENT_ID
    )
    pad = "x" * msg_len
    for rn in range(1, count + 1):
        await conv_service.create_conversation(
            user_message=f"第{rn}轮用户消息{pad}",
            assistant_response=f"第{rn}轮助手回复{pad}",
            user_id=user_id,
            thread_id=thread_id,
            agent_id=_AGENT_ID,
            round_number=rn,
        )


@pytest.mark.integration
class TestMemoryAssemblyIntegration:
    """MemoryAssembler.assemble_memory_context 记忆组装集成测试."""

    @pytest.mark.asyncio
    async def test_integration_assembler_first_turn_empty_history(
        self,
        test_user,
        test_thread_id,
    ):
        """测试首轮对话 (空数据库) 返回空记忆上下文.

        协作场景: 空库 + MemoryAssembler → get_latest_round_number 返回 0
        Mock 边界: 无 (零 Mock, 全真实)
        验证重点: history_messages=[] / system_prompt_extension="" / todo_list=""
        业务价值: 首轮对话边界正确性, 不生成空标签
        """
        assembler = _make_assembler(test_user, test_thread_id)
        ctx = await assembler.assemble_memory_context(test_user, test_thread_id)

        assert ctx.history_messages == []
        assert ctx.system_prompt_extension == ""
        assert ctx.todo_list == ""

    @pytest.mark.asyncio
    async def test_integration_assembler_four_parts_format(
        self,
        test_user,
        test_thread_id,
    ):
        """测试 4 部分组装格式 (pinned XML + Human/AI 交替历史 + TODO markdown).

        协作场景: 预置 5 轮对话 + 1 条置顶 + 1 条 TODO → MemoryAssembler
        Mock 边界: 无
        验证重点: history_messages 为 Human/AIMessage 交替;
                  extension 含 <pinned_memory>...</pinned_memory> XML 包裹;
                  todo_list 非空 (含 TODO 标题)
        业务价值: 记忆组装输出格式契约, 供 LLM 正确解析
        """
        await _seed_conversations(test_user, test_thread_id, count=5)

        mem_service = await create_memory_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await mem_service.update_memory(
            SimplePinnedMemoryType.BASIC_INFO,
            "用户是软件工程师",
            test_user,
            test_thread_id,
        )

        todo_service = await create_todo_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await todo_service.create_todo(
            title="完成集成测试报告", user_id=test_user, thread_id=test_thread_id
        )

        assembler = _make_assembler(test_user, test_thread_id)
        ctx = await assembler.assemble_memory_context(test_user, test_thread_id)

        assert len(ctx.history_messages) == 10, "5 轮历史应为 10 条 Human/AI 交替消息"
        for i in range(0, 10, 2):
            assert isinstance(ctx.history_messages[i], HumanMessage)
            assert isinstance(ctx.history_messages[i + 1], AIMessage)

        assert "<pinned_memory>" in ctx.system_prompt_extension
        assert "</pinned_memory>" in ctx.system_prompt_extension
        assert "用户是软件工程师" in ctx.system_prompt_extension

        assert ctx.todo_list != ""
        assert "完成集成测试报告" in ctx.todo_list

    @pytest.mark.asyncio
    async def test_integration_assembler_budget_split_and_index_pseudo_round(
        self,
        test_user,
        test_thread_id,
    ):
        """测试字符预算截断 + 索引区伪对话轮生成.

        协作场景: 预置 20 轮长对话 (超出预算) + total_budget=2000 →
                  主历史按预算截断, 未覆盖部分由索引区独立预算补全为伪对话轮
        Mock 边界: 无
        验证重点: 主历史轮数 < 20 (被预算截断);
                  history_messages 含 [过往对话回顾] 伪对话轮 + <conversation_index>
        业务价值: 长对话场景下记忆压缩正确, 索引区补全早期上下文
        """
        await _seed_conversations(test_user, test_thread_id, count=20, msg_len=100)

        assembler = _make_assembler(test_user, test_thread_id)
        ctx = await assembler.assemble_memory_context(
            test_user, test_thread_id, total_budget=2000
        )

        real_history_msgs = [
            m for m in ctx.history_messages if m.content != "[过往对话回顾]"
        ]
        assert len(real_history_msgs) < 40, "主历史应被预算截断 (< 20 轮)"

        has_index_pseudo = any(
            isinstance(m, HumanMessage) and m.content == "[过往对话回顾]"
            for m in ctx.history_messages
        )
        assert has_index_pseudo, "主历史未覆盖第 1 轮时应生成索引区伪对话轮"

        index_msg = next(
            m
            for m in ctx.history_messages
            if isinstance(m, AIMessage) and "<conversation_index>" in str(m.content)
        )
        assert "</conversation_index>" in str(index_msg.content)

    @pytest.mark.asyncio
    async def test_integration_assembler_pinned_cache_hit_skips_db(
        self,
        test_user,
        test_thread_id,
    ):
        """测试 pinned 缓存命中后跳过 DB 读取.

        协作场景: 预置置顶 → assemble (缓存未命中, 读 DB 写缓存) →
                  直接 DB 改置顶 (绕过缓存) → 再 assemble (缓存命中, 不读新 DB 值)
        Mock 边界: 无
        验证重点: 第二次 assemble 返回缓存旧值, 不含 DB 新值 (证明缓存命中)
        业务价值: 缓存协同正确, 避免每轮重复读 DB
        """
        await _seed_conversations(test_user, test_thread_id, count=2)

        mem_service = await create_memory_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await mem_service.update_memory(
            SimplePinnedMemoryType.BASIC_INFO,
            "原始置顶内容",
            test_user,
            test_thread_id,
        )

        assembler = _make_assembler(test_user, test_thread_id)
        ctx1 = await assembler.assemble_memory_context(test_user, test_thread_id)
        assert "原始置顶内容" in ctx1.system_prompt_extension

        await mem_service.update_memory(
            SimplePinnedMemoryType.BASIC_INFO,
            "被绕过缓存的新内容",
            test_user,
            test_thread_id,
        )

        ctx2 = await assembler.assemble_memory_context(test_user, test_thread_id)
        assert "原始置顶内容" in ctx2.system_prompt_extension, "应命中缓存返回旧值"
        assert "被绕过缓存的新内容" not in ctx2.system_prompt_extension

    @pytest.mark.asyncio
    async def test_integration_assembler_cold_start_then_cache_hit(
        self,
        test_user,
        test_thread_id,
    ):
        """冷启动读 DB 种子化 -> 命中缓存(与 DB 解耦) -> 写路径滚动纳入新轮.

        协作场景: 预置 5 轮 -> assemble(冷启动, 读 DB 种子化) ->
                  assemble(命中缓存, 结果一致) ->
                  DB 新增 round 6 但不经写路径 -> assemble 仍命中旧缓存(round 6 不出现,
                  证明读路径信任缓存、与 DB 解耦) ->
                  写路径滚动 round 6 进缓存 -> assemble 命中含 round 6.
        Mock 边界: 无 (全真实组件)
        业务价值: 验证滚动缓存核心契约 —— 读路径只冷启动读一次 DB, 之后信任缓存,
                  新轮次经写路径滚动入窗.
        """
        from src.agent.memory.local_memory.cache import reset_global_cache
        from src.agent.memory.local_memory.core import ConversationMemoryCore
        from tests.mocks.memory.local_memory import create_mock_conversation_data

        reset_global_cache()
        try:
            await _seed_conversations(test_user, test_thread_id, count=5)
            assembler = _make_assembler(test_user, test_thread_id)

            # 冷启动: 读 DB 种子化缓存
            ctx1 = await assembler.assemble_memory_context(test_user, test_thread_id)
            contents1 = [str(m.content) for m in ctx1.history_messages]
            assert any("第5轮用户消息" in c for c in contents1)

            # 命中缓存: 第二次 assemble 结果一致(缓存服务, 非重读 DB)
            ctx2 = await assembler.assemble_memory_context(test_user, test_thread_id)
            contents2 = [str(m.content) for m in ctx2.history_messages]
            assert contents2 == contents1

            # DB 新增 round 6 但不经写路径滚动 -> 读路径仍信任旧缓存, round 6 不出现
            await _seed_conversations(test_user, test_thread_id, count=6, msg_len=40)
            ctx3 = await assembler.assemble_memory_context(test_user, test_thread_id)
            contents3 = [str(m.content) for m in ctx3.history_messages]
            assert not any("第6轮用户消息" in c for c in contents3), (
                "读路径信任缓存, 不应自动拉取 DB 新轮"
            )

            # 写路径滚动 round 6 进缓存 -> 命中时 round 6 出现
            core = ConversationMemoryCore(
                user_id=test_user,
                thread_id=test_thread_id,
                agent_config=AgentConfig(agent_id=_AGENT_ID),
            )
            data6 = create_mock_conversation_data(
                user_id=test_user,
                thread_id=test_thread_id,
                agent_id=_AGENT_ID,
                round_number=6,
                user_message="第6轮用户消息" + "x" * 40,
                assistant_response="第6轮助手回复" + "x" * 40,
            )
            await core._update_conversation_cache(data6)
            ctx4 = await assembler.assemble_memory_context(test_user, test_thread_id)
            contents4 = [str(m.content) for m in ctx4.history_messages]
            assert any("第6轮用户消息" in c for c in contents4), (
                "写路径滚动后 round 6 应进入缓存"
            )
        finally:
            reset_global_cache()
