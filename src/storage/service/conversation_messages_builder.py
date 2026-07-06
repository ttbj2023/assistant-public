"""ConversationIndex 到原生 LangChain messages 的转换器.

记忆系统使用: 把数据库里的纯文本对话轮次重建为
HumanMessage/AIMessage 交替序列, 让 LLM 以训练时学到的标准对话格式
看到历史, 而非压扁成单个字符串.

设计原则:
- 纯转换函数, 无副作用, 无 I/O, 易测试
- 输入已按 round_number 排序的 ConversationIndex 列表
- 输出 List[BaseMessage], 顺序为 Human/AI 交替
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.storage.models.conversation import ConversationIndex


def build_messages_from_conversations(
    conversations: list[ConversationIndex],
) -> list[BaseMessage]:
    """把 ConversationIndex 列表重建为原生 message 列表.

    每个对话轮次拆成一对 HumanMessage(user_message) + AIMessage(assistant_response),
    保持 round_number 升序的原始对话流.

    Args:
        conversations: 已按 round_number 排序的对话索引列表(可为空).

    Returns:
        Human/AI 交替的 message 列表. 输入为空时返回空列表.

    Note:
        - 调用方应保证输入已按 round_number 升序排序(DAO get_conversations_in_range
          默认就会排序).
        - user_message / assistant_response 取原值, 不做裁剪或拼接.
          若某字段为空字符串, 仍会生成对应 message(content=""), 以维持 Human/AI
          交替的严格结构(避免破坏 LLM 对话轮次预期).

    """
    messages: list[BaseMessage] = []
    for conv in conversations:
        messages.append(HumanMessage(content=conv.user_message))
        messages.append(AIMessage(content=conv.assistant_response))
    return messages


__all__ = ["build_messages_from_conversations"]
