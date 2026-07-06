"""MemoryAssembler - 原生 messages 数组形式的记忆组装器.

输出结构:
  [HumanMessage("[过往对话回顾]"), AIMessage("<conversation_index>...")]  # 伪对话轮 (索引区)
  [HumanMessage(轮N原文), AIMessage(轮N回复), ...]                       # 真实历史
  + system_prompt_extension (置顶记忆, 含引导语前缀)
  + todo_list (单独返回, 由 processor 注入 current_content)

预算机制 (字符级, 主历史/索引区独立预算):
- 主历史: total_char_budget (默认 20000), 内存二分查找, 判定 sum(len(user_message) + len(assistant_response))
- 索引区: budget 驱动级联 —— fine 行从 end_round 往前填 index_char_budget, 溢出的
  group 降级为弧短语展示(group 粒度, 跨界全弧); 弧短语不受 budget 约束(线性增长)
- 两区独立解耦, 不再按比例切分 total
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.storage.formatters.conversation_formatter import (
    create_conversation_formatter,
)
from src.storage.service import (
    create_conversation_service,
    create_memory_service,
    create_todo_service,
    create_user_requirement_service,
)
from src.storage.service.conversation_messages_builder import (
    build_messages_from_conversations,
)

from .cache import (
    get_conversation,
    get_pinned_memory,
    get_todolist,
    set_conversation,
    set_pinned_memory,
    set_todolist,
)
from .history_budget import (
    resolve_total_char_budget,
    select_index_fine_suffix,
    select_main_history_suffix,
)

# 冷启动种子化时取的近期窗口轮数(只这一次读 DB, 之后滚动维护). 慷慨取值,
# 取回后再裁到 total_char_budget 以内; 若小于可容纳轮数, 后续滚动会自愈补齐.
_COLD_START_WINDOW = 500

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig
    from src.storage.models.conversation import ConversationIndex

logger = logging.getLogger(__name__)


@dataclass
class MemoryContext:
    """MemoryAssembler 的输出结构.

    Attributes:
        history_messages: 索引区伪对话轮 + 主历史真实轮次, 顺序为时间正序.
        system_prompt_extension: 置顶记忆(含引导语前缀), 由 orchestrator 拼到
            system_prompt 尾部. 空字符串表示无置顶记忆.
        todo_list: 格式化 TODO markdown, 由 processor 注入 current_content
            (与时间/missed_messages 同区). 空字符串表示无 TODO 或未启用.

    """

    history_messages: list[BaseMessage] = field(default_factory=list)
    system_prompt_extension: str = ""
    todo_list: str = ""


class MemoryAssembler:
    """原生 messages 数组形式的记忆组装器.

    内置 pinned/TODO 缓存获取逻辑 (cache + DB 回退),
    历史 + 索引区组装产出 BaseMessage 列表.
    """

    def __init__(
        self,
        agent_id: str,
        agent_config: AgentConfig | None = None,
        user_id: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        """初始化.

        Args:
            agent_id: Agent ID
            agent_config: Agent 配置对象
            user_id: 用户ID
            thread_id: 线程ID

        """
        self.agent_id = agent_id
        self.agent_config = agent_config
        self.user_id = user_id
        self.thread_id = thread_id
        self._formatter = create_conversation_formatter()

    async def assemble_memory_context(
        self,
        user_id: str,
        thread_id: str,
        total_budget: int | None = None,
        agent_id: str | None = None,
    ) -> MemoryContext:
        """组装原生 messages 上下文.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            total_budget: 总字符预算 (None 则从 agent_config 解析)
            agent_id: Agent ID (可选, 回退到 self.agent_id)

        Returns:
            MemoryContext: 含 history_messages / system_prompt_extension / todo_list

        Raises:
            RuntimeError: 组装失败时

        """
        effective_agent_id = agent_id or self.agent_id
        budget = self._resolve_total_char_budget(total_budget)

        try:
            pinned_str = (
                await self._get_pinned_memory_with_cache(user_id, thread_id)
            ).strip()

            todo_str = ""
            if self._resolve_include_todo():
                todo_str = (
                    await self._get_todo_list_with_cache(user_id, thread_id)
                ).strip()

            # 用户要求记事本 (独立分库, 主模型工具维护); 失败不阻塞记忆组装
            req_str = ""
            try:
                req_service = await create_user_requirement_service(
                    user_id,
                    thread_id,
                    agent_id=effective_agent_id,
                )
                req_str = (await req_service.get_formatted(user_id, thread_id)).strip()
            except Exception as e:
                logger.warning("读取用户要求记事本失败(非致命): %s", e)

            history_messages = await self._build_history_messages(
                user_id,
                thread_id,
                budget,
                effective_agent_id,
            )

            extension = ""
            if pinned_str:
                extension = (
                    "以下是你需要长期记住的关键事实:\n"
                    f"<pinned_memory>\n{pinned_str}\n</pinned_memory>"
                )
            if req_str:
                req_block = (
                    "以下是用户对你的长期要求, 除非用户明确要求修改否则不要主动更改, "
                    "并在回复中遵守:\n"
                    f"<user_requirements>\n{req_str}\n</user_requirements>"
                )
                extension = f"{extension}\n\n{req_block}" if extension else req_block

            logger.debug(
                "MemoryAssembler 组装完成 for %s:%s, messages=%d, "
                "extension=%d, todo=%d",
                user_id,
                thread_id,
                len(history_messages),
                len(extension),
                len(todo_str),
            )
            return MemoryContext(
                history_messages=history_messages,
                system_prompt_extension=extension,
                todo_list=todo_str,
            )

        except Exception as e:
            logger.error(
                "MemoryAssembler 组装失败 for %s:%s: %s",
                user_id,
                thread_id,
                e,
            )
            raise RuntimeError(f"记忆上下文组装失败: {e}") from e

    async def _build_history_messages(
        self,
        user_id: str,
        thread_id: str,
        total_budget: int,
        agent_id: str,
    ) -> list[BaseMessage]:
        """构建历史 messages (索引区伪对话轮 + 主历史真实轮次).

        流程:
        1. 主历史: 从滚动有界缓存取窗口; 命中直接用(零 DB), 未命中冷启动种子化
        2. 主历史最早轮决定索引区边界 index_end = earliest_in_primary - 1
        3. 索引区 budget 驱动级联组装(_fetch_index_in_budget):
           - fine 行从 index_end 往前填 budget, 溢出 group 全弧展示(不拆分)
        4. 主历史 -> build_messages_from_conversations 重建为 Human/AI 交替
        """
        conv_service = await create_conversation_service(
            user_id,
            thread_id,
            agent_id=agent_id,
        )
        latest_round = await conv_service.get_latest_round_number(
            user_id,
            thread_id,
        )
        if latest_round <= 0:
            return []

        primary_budget = max(total_budget, 0)
        primary_convs = await self._get_main_history_with_cache(
            conv_service,
            user_id,
            thread_id,
            agent_id,
            latest_round,
            primary_budget,
        )

        messages: list[BaseMessage] = []
        if not primary_convs:
            return messages

        earliest_in_primary = primary_convs[0].round_number
        index_end = earliest_in_primary - 1
        secondary_budget = max(self._resolve_index_char_budget(), 0)
        if index_end >= 1 and secondary_budget > 0:
            index_md = await self._fetch_index_in_budget(
                conv_service,
                user_id,
                thread_id,
                index_end,
                secondary_budget,
            )
            if index_md:
                messages.append(HumanMessage(content="[过往对话回顾]"))
                messages.append(
                    AIMessage(
                        content=(
                            f"<conversation_index>\n{index_md}\n</conversation_index>"
                        ),
                    ),
                )

        messages.extend(build_messages_from_conversations(primary_convs))
        return messages

    async def _get_main_history_with_cache(
        self,
        conv_service: Any,
        user_id: str,
        thread_id: str,
        agent_id: str,
        latest_round: int,
        budget: int,
    ) -> list[ConversationIndex]:
        """取有界主历史窗口: 命中直接用(零 DB), 未命中冷启动种子化.

        - 命中: 缓存已是滚动裁剪后的有界窗口, 直接返回(信任缓存, 读路径不查 DB)
        - 未命中: 取一个慷慨近期窗口冷启动种子化 -> 裁到预算 -> 写回缓存

        Args:
            conv_service: ConversationService 实例
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID
            latest_round: 当前最新轮次号
            budget: 主历史字符预算

        """
        cached = get_conversation(user_id, thread_id, agent_id=agent_id)
        if isinstance(cached, list) and cached:
            return cached

        start = max(1, latest_round - _COLD_START_WINDOW + 1)
        fetched = await conv_service.get_conversations_in_range(
            start,
            latest_round,
            user_id,
            thread_id,
        )
        if not fetched:
            return []
        bounded = select_main_history_suffix(fetched, budget)
        set_conversation(user_id, thread_id, bounded, agent_id=agent_id)
        return bounded

    async def _fetch_index_in_budget(
        self,
        conv_service: Any,
        user_id: str,
        thread_id: str,
        end_round: int,
        budget: int,
    ) -> str:
        """组装索引区 markdown (budget 驱动级联): [老期溢出弧短语] + [近期 fine 行].

        级联溢出(对齐"全对话-索引-fine 行溢出为弧短语"设计): fine 行从 end_round
        往前填 budget, 溢出的 group 降级为弧短语展示. group 粒度判定, 不拆分:
        - group.round_start < raw_fine_start => 起点溢出, 整个 group 全弧展示
        - 跨界 group(round_end >= raw_fine_start)亦全弧, fine 区从其 round_end+1 起
        - 弧短语不受 budget 约束(线性增长, 永不丢); budget 仅约束 fine 行部分

        冻结(index_run_service 预计算)与展示解耦: 弧短语提前存 DB, 此处按需展示.
        """
        # 1. fine 行 budget 裁剪: [1, end_round] 从后往前填 budget
        all_index = await conv_service.get_conversations_in_range(
            1,
            end_round,
            user_id,
            thread_id,
        )
        fine_suffix = select_index_fine_suffix(all_index, budget)
        raw_fine_start = fine_suffix[0].round_number if fine_suffix else end_round + 1

        # 2. 溢出 group: 起点溢出 fine 区的全弧展示(group 粒度, 不拆分)
        groups = await conv_service.get_index_groups_up_to(
            user_id,
            thread_id,
            end_round,
        )
        arc_groups = [g for g in groups if g.round_start < raw_fine_start]
        if arc_groups:
            # 跨界 group 全弧, fine 区从其末尾+1 起(避免与 fine 行重复)
            effective_fine_start = max(raw_fine_start, arc_groups[-1].round_end + 1)
        else:
            effective_fine_start = raw_fine_start

        parts: list[str] = []

        # 3. 老期溢出弧短语(全展示, 不受 budget 约束)
        if arc_groups:
            groups_data = [
                {
                    "round_start": g.round_start,
                    "round_end": g.round_end,
                    "arc_phrase": g.arc_phrase,
                }
                for g in arc_groups
            ]
            timeline = await self._formatter.format_index_groups(groups_data)
            if timeline:
                parts.append(timeline)
            self._log_index_tokens(len(arc_groups), timeline)

        # 4. 近期 fine 行
        if effective_fine_start <= end_round:
            fine_md = await conv_service.get_formatted_index_range(
                user_id,
                thread_id,
                effective_fine_start,
                end_round,
                format_template="markdown",
            )
            if fine_md:
                parts.append(fine_md)
                self._log_index_tokens(0, fine_md)

        return "\n".join(p for p in parts if p)

    def _log_index_tokens(self, group_count: int, rendered: str) -> None:
        """记录索引区字符数(老期分组数 / 渲染长度) —— 压缩逼近的观测尺子."""
        if rendered:
            logger.debug(
                "索引区[group=%d]: %d 字符",
                group_count,
                len(rendered),
            )

    # ==================== 缓存集成的数据获取方法 ====================

    async def _get_pinned_memory_with_cache(self, user_id: str, thread_id: str) -> str:
        """获取置顶记忆 - 缓存优先, DB 回退."""
        try:
            cached_pinned = get_pinned_memory(
                user_id,
                thread_id,
                agent_id=self.agent_id,
            )
            if cached_pinned is not None:
                if isinstance(cached_pinned, str):
                    return cached_pinned
                if isinstance(cached_pinned, dict):
                    from src.storage.formatters.pinned_memory_formatter import (
                        create_pinned_memory_formatter,
                    )

                    formatter = create_pinned_memory_formatter()
                    sanitized = formatter.sanitize_pinned_memory_data(cached_pinned)
                    formatted = await formatter.format_pinned_memory(
                        sanitized,
                        "markdown",
                    )
                    set_pinned_memory(
                        user_id,
                        thread_id,
                        formatted,
                        agent_id=self.agent_id,
                    )
                    return formatted
                logger.warning("缓存数据类型错误: %s, 重新获取", type(cached_pinned))  # type: ignore[unreachable]

            memory_service = await create_memory_service(
                user_id,
                thread_id,
                agent_id=self.agent_id,
            )
            pinned_memory_dict = await memory_service.get_pinned_memory_as_dict(
                user_id,
                thread_id,
            )
            pinned_memory_str = await memory_service.format_pinned_memory_dict(
                pinned_memory_dict,
                "markdown",
            )

            if not isinstance(pinned_memory_dict, dict):
                logger.error("置顶记忆数据类型错误: %s", type(pinned_memory_dict))
                pinned_memory_dict = {
                    "basic_info": "",
                    "preferences": "",
                }

            set_pinned_memory(
                user_id,
                thread_id,
                pinned_memory_str,
                agent_id=self.agent_id,
            )
            return pinned_memory_str

        except Exception as e:
            logger.error("获取置顶记忆失败 for %s:%s: %s", user_id, thread_id, e)
            return ""

    async def _get_todo_list_with_cache(self, user_id: str, thread_id: str) -> str:
        """获取TODO列表 - 缓存优先, DB 回退."""
        try:
            cached_todolist = get_todolist(user_id, thread_id, agent_id=self.agent_id)
            if cached_todolist is not None:
                if isinstance(cached_todolist, str):
                    return cached_todolist
                if isinstance(cached_todolist, list):
                    from src.storage.formatters.todo_formatter import (
                        create_todo_formatter,
                    )

                    formatter = create_todo_formatter()
                    todo_dicts: list[dict[str, Any]] = []
                    for todo in cached_todolist:
                        if isinstance(todo, dict):
                            todo_dicts.append(todo)
                        elif hasattr(todo, "to_dict"):
                            todo_dicts.append(todo.to_dict())
                    formatted = await formatter.format_todolist(
                        todo_dicts,
                        include_section_title=False,
                        format_template="markdown",
                    )
                    set_todolist(user_id, thread_id, formatted, agent_id=self.agent_id)
                    return formatted
                logger.warning("TODO缓存类型错误: %s, 重新获取", type(cached_todolist))  # type: ignore[unreachable]

            try:
                from src.storage.models.todo import TodoStatus

                todo_service = await create_todo_service(
                    user_id,
                    thread_id,
                    agent_id=self.agent_id,
                )
                todo_list_str = await todo_service.get_formatted_todolist(
                    user_id,
                    thread_id,
                    statuses=[TodoStatus.PENDING, TodoStatus.IN_PROGRESS],
                    limit=50,
                    include_section_title=False,
                    format_template="markdown",
                )
            except Exception as e:
                logger.error("获取TODO列表失败: %s", e)
                todo_list_str = ""

            set_todolist(user_id, thread_id, todo_list_str, agent_id=self.agent_id)
            return todo_list_str

        except Exception as e:
            logger.error("获取TODO列表失败: %s", e)
            return ""

    # ==================== 配置解析 ====================

    def _resolve_total_char_budget(self, total_budget: int | None) -> int:
        """解析总字符预算 (委托共享实现)."""
        return resolve_total_char_budget(self.agent_config, total_budget)

    def _resolve_index_char_budget(self) -> int:
        """解析索引区独立字符预算 (0=禁用, 回退到 10000)."""
        if self.agent_config is not None:
            try:
                cfg_budget = getattr(
                    self.agent_config.memory,
                    "index_char_budget",
                    None,
                )
                if isinstance(cfg_budget, int) and cfg_budget >= 0:
                    return cfg_budget
            except Exception as e:
                logger.debug("索引区预算配置获取失败, 使用默认值: %s", e)
        return 10000

    def _resolve_include_todo(self) -> bool:
        """解析是否在上下文中包含 TODO 列表."""
        if self.agent_config is None or self.agent_config.memory is None:
            return False
        return bool(
            getattr(self.agent_config.memory, "include_todo_in_context", False),
        )


__all__ = ["MemoryAssembler", "MemoryContext"]
