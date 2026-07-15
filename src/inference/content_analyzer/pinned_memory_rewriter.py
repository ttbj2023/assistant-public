"""主模型每轮全文覆写统一置顶记忆.

每轮对话后, 由主对话模型(非小模型)接收本轮完整 messages 快照 + 回复,
判断是否需要更新长期记忆, 全文覆写单一块存储.

设计要点:
- 复用 invoke_with_fallback (JSON mode + fallback 统一路径)
- 按 mode 加载不同覆写 prompt (local=身份/口味/要求; simple=洞察/偏好/经验)
- 输出 JSON: {"needs_update": bool, "content": str}
- needs_update=false 时短路跳过写库
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, Field

from src.inference.llm.model_loader import invoke_with_fallback
from src.inference.llm.response_utils import content_to_text

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)


class RewriteResult(BaseModel):
    """主模型覆写结果."""

    needs_update: bool = Field(description="本轮是否需要更新记忆")
    content: str = Field(default="", description="完整新记忆全文(仅 needs_update=true)")


class PinnedMemoryRewriter:
    """主模型每轮全文覆写统一置顶记忆."""

    LOCAL_REWRITE_PROMPT = """以上是本轮完整对话.

以下是你的长期记忆区当前内容, 需要你维护更新:

<current_memory>
{current_memory}
</current_memory>

请根据本轮对话内容, 判断是否需要更新长期记忆, 并全文覆写.

## 记什么 (准入判据)
- 用户是谁: 稳定的身份事实(姓名/所在地/职业/技能/设备/健康事实等)
- 用户偏好: 稳定的口味偏好(书/食物/休闲/艺术等, 非一次性兴趣)
- 用户对助手的要求: 关于你该如何响应/运作的长期要求(回复风格/格式/语言等)
- 用户常用信息: 反复会用到的稳定信息(常用路线/常用账号/常用工具等)

## 不记什么
- 一次性任务/当前工作/项目状态(会变的, 不是"用户是谁")
- 对话中的临时上下文/当前关注的事件(如天气/航班/近期计划)
- 助手的操作流程/SOP(这是你的工作方式, 不是用户的信息)

## 核心测试
每条记忆问自己: "这条信息会不会改变我在下一轮对话中的响应方式?"
如果不会, 它只是背景噪音, 不要记.

## 准入三问
1. 用户明确说出口了(陈述可得), 还是从行为推断的?
2. 这是"用户是谁"还是"用户在做什么"?
3. 一周不联系, 这条还成立吗?

## 容量约束
- 一行一条, 用精炼的语言
- 总计不超过20行/800字
- 宁可少记不要噪音; 拿不准倾向不记

输出JSON:
{{"needs_update": true, "content": "完整的新记忆, 一行一条"}}
或
{{"needs_update": false}}
"""

    SIMPLE_REWRITE_PROMPT = """以上是本轮完整对话.

以下是你的长期记忆区当前内容, 需要你维护更新:

<current_memory>
{current_memory}
</current_memory>

请根据本轮对话内容, 判断是否需要更新长期记忆, 并全文覆写.

## 记什么 (准入判据)
- 领域洞察: 从对话中提炼的可复用经验/模式/行业认知/判断
- 输出偏好: 用户对内容输出的稳定偏好(写作风格/格式/选题方向等)
- 可复用经验: 踩过的坑/验证过的判断/方法论

## 不记什么
- 一次性任务的具体内容/临时讨论细节
- 操作流程/SOP

## 核心测试
每条记忆问自己: "下次类似任务时, 这条信息能帮我做得更好吗?"

## 容量约束
- 一行一条, 用精炼的语言
- 总计不超过20行/800字
- 宁可少记不要噪音

输出JSON:
{{"needs_update": true, "content": "完整的新记忆, 一行一条"}}
或
{{"needs_update": false}}
"""

    _PROMPTS: ClassVar[dict[str, str]] = {
        "local": LOCAL_REWRITE_PROMPT,
        "simple": SIMPLE_REWRITE_PROMPT,
    }

    def __init__(
        self,
        model_id: str,
        model_params: dict[str, Any] | None = None,
    ) -> None:
        self.model_id = model_id
        self.model_params = model_params or {}

    async def rewrite(
        self,
        messages: list[BaseMessage],
        response: str,
        current_memory: str,
        mode: Literal["local", "simple"],
    ) -> RewriteResult:
        """主模型覆写长期记忆.

        Args:
            messages: 主请求 messages 快照 ([SystemMessage, *history, HumanMessage])
            response: 本轮助手回复
            current_memory: 当前置顶记忆全文
            mode: "local" 或 "simple"

        Returns:
            RewriteResult(needs_update, content)

        """
        from langchain_core.messages import AIMessage, HumanMessage

        template = self._PROMPTS.get(mode)
        if not template:
            raise ValueError(f"未知 mode: {mode}, 仅支持 local/simple")

        instruction = template.format(current_memory=current_memory or "(空)")
        full_messages = [
            *messages,
            AIMessage(content=response),
            HumanMessage(content=instruction),
        ]

        raw = await invoke_with_fallback(
            full_messages,
            self.model_id,
            self.model_params,
            use_json_mode=True,
            usage_tag="pinned_memory_rewrite",
        )

        return self._parse_result(raw)

    @staticmethod
    def _parse_result(response: Any) -> RewriteResult:
        """解析 LLM JSON 输出为 RewriteResult."""
        text = content_to_text(response.content).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end])
                except json.JSONDecodeError:
                    logger.warning("置顶记忆覆写 JSON 解析失败, 原始: %s", text[:200])
                    return RewriteResult(needs_update=False)
            else:
                logger.warning("置顶记忆覆写无 JSON 输出, 原始: %s", text[:200])
                return RewriteResult(needs_update=False)

        return RewriteResult(
            needs_update=bool(data.get("needs_update", False)),
            content=str(data.get("content", "")),
        )


__all__ = ["PinnedMemoryRewriter", "RewriteResult"]
