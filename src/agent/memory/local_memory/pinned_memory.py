"""置顶记忆管理器(简化版).

职责边界:
- 读取当前 2 字段置顶记忆, 格式化为prompt上下文(无编号)
- 根据分析器返回的操作(add/delete/change)增量更新记忆
- 更新成功后触发缓存失效

字段处理:
- basic_info / preferences: 均按行累积, 每行一条
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from src.agent.memory.local_memory.semantic_dedup import is_semantically_duplicate
from src.storage.models.simple_pinned_memory import SimplePinnedMemoryType
from src.storage.service import create_memory_service

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings

    from src.core.types import MemoryOperation
    from src.storage.service.memory_service import MemoryService

logger = logging.getLogger(__name__)

# LLM 偶尔把 prompt 中的临时编号 [N] 抄进 content, 用正则清理
_STRIP_ID_PREFIX = re.compile(r"^\[\d+\]\s*")


def _clean_line(line: str) -> str:
    """剥离 LLM 输出中残留的临时编号前缀, 如 '[7] 宠物:...' -> '宠物:...'."""
    return _STRIP_ID_PREFIX.sub("", line.strip())


_FIELD_LABELS = {
    "basic_info": "基本画像",
    "preferences": "口味偏好",
}

_FIELD_TYPES = {
    "basic_info": SimplePinnedMemoryType.BASIC_INFO,
    "preferences": SimplePinnedMemoryType.PREFERENCES,
}

# 两个字段均按行累积
_LINE_FIELDS = ("basic_info", "preferences")


class SimplePinnedMemoryManager:
    """简化的置顶记忆管理器.

    负责 2 字段置顶记忆的读取,操作化更新与缓存失效.
    """

    def __init__(self, user_id: str, thread_id: str, *, agent_id: str) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_id = agent_id
        self._memory_service: MemoryService | None = None

        # 读一次去重配置(避免 add 热路径重复读配置)
        from src.config.inference_config import get_config as get_inference_config

        dedup_cfg = get_inference_config().content_analyzer
        self._dedup_enabled = dedup_cfg.dedup_enabled
        self._dedup_threshold = dedup_cfg.dedup_threshold
        # 懒加载, 仅 dedup 启用且精确匹配未命中时才可能取用
        self._embeddings: Embeddings | None = None

        logger.debug("📍 初始化SimplePinnedMemoryManager: %s/%s", user_id, thread_id)

    async def _get_memory_service(self) -> MemoryService:
        """获取记忆服务实例(懒加载)."""
        if self._memory_service is None:
            self._memory_service = await create_memory_service(
                self.user_id,
                self.thread_id,
                agent_id=self.agent_id,
            )
        assert self._memory_service is not None
        return self._memory_service

    async def _get_embeddings(self) -> Embeddings:
        """获取嵌入模型实例(懒加载, 复用项目统一 create_embeddings)."""
        if self._embeddings is None:
            from src.inference.embeddings.embeddings import create_embeddings

            self._embeddings = create_embeddings()
        return self._embeddings

    async def _is_semantic_duplicate(
        self,
        field: str,
        content: str,
        existing_lines: list[str],
    ) -> bool:
        """判断 add 的 content 是否与同字段已有条目语义重复.

        dedup 关闭或无对比对象时返回 False; 任何环节失败均返回 False
        (不判重), 保证不阻断 add 主流程, 回退到精确字符串匹配.
        """
        if not self._dedup_enabled or not existing_lines:
            return False
        try:
            embeddings = await self._get_embeddings()
            return await is_semantically_duplicate(
                content,
                existing_lines,
                embeddings,
                self._dedup_threshold,
            )
        except Exception as e:
            logger.warning("语义去重失败, 回退精确匹配 %s: %s", field, e)
            return False

    async def get_pinned_memory_content(self) -> dict[str, str]:
        """获取置顶记忆 2 字段内容(均为字符串)."""
        try:
            memory_service = await self._get_memory_service()
            content = await memory_service.get_pinned_memory_as_dict(
                self.user_id,
                self.thread_id,
            )
            if not isinstance(content, dict):
                return {"basic_info": "", "preferences": ""}
            return {
                "basic_info": str(content.get("basic_info", "") or ""),
                "preferences": str(content.get("preferences", "") or ""),
            }
        except Exception as e:
            logger.error("获取置顶记忆内容失败: %s", e)
            return {"basic_info": "", "preferences": ""}

    async def get_memory_for_analysis(self) -> str:
        """读取记忆, 返回prompt格式化字符串(无编号, 原文逐行).

        Returns:
            formatted_prompt_block

        """
        raw = await self.get_pinned_memory_content()
        lines: list[str] = []

        for field in _LINE_FIELDS:
            lines.append(f"### {_FIELD_LABELS[field]}")
            content = raw.get(field, "")
            if content and content.strip():
                for item in content.strip().split("\n"):
                    item = _clean_line(item)
                    if not item:
                        continue
                    lines.append(item)
            else:
                lines.append("(空)")
            lines.append("")

        return "\n".join(lines)

    async def get_memory_for_audit(self) -> tuple[str, dict[int, dict[str, str]]]:
        """返回带[N]编号的记忆块 + number_map, 供审计模型引用条目.

        与 get_memory_for_analysis(无编号, 供1-step)不同, 审计需要编号让模型
        精准引用条目(避免逐字复制长文本导致转义问题).

        Returns:
            (block, number_map): block 是带编号的格式化文本;
            number_map 是 {编号: {field, content}} 供解析时映射原文.

        """
        raw = await self.get_pinned_memory_content()
        items: list[tuple[int, str, str]] = []
        block_parts: list[str] = []
        num = 0
        for fld, label in _FIELD_LABELS.items():
            block_parts.append(f"### {label}")
            content = raw.get(fld, "") or ""
            field_lines: list[str] = []
            for ln in content.split("\n"):
                ln = ln.strip()
                if not ln:
                    continue
                num += 1
                items.append((num, fld, ln))
                field_lines.append(f"[{num}] {ln}")
            block_parts.append("\n".join(field_lines) if field_lines else "(空)")
        block = "\n".join(block_parts)
        number_map = {n: {"field": f, "content": c} for n, f, c in items}
        return block, number_map

    async def apply_operations(self, operations: list[MemoryOperation]) -> bool:
        """根据精确字符串匹配应用操作, 只写回被修改的字段.

        Args:
            operations: 操作列表, 每条操作携带 field 与目标字符串

        Returns:
            是否有字段被更新

        """
        if not operations:
            return False

        raw = await self.get_pinned_memory_content()
        field_lines: dict[str, list[str]] = {}

        for field in _LINE_FIELDS:
            content = raw.get(field, "")
            field_lines[field] = [
                line.strip() for line in content.split("\n") if line.strip()
            ]

        modified_fields: set[str] = set()

        for op in operations:
            field = op.field
            if field not in field_lines:
                continue

            if op.action == "add":
                clean_content = _clean_line(op.content) if op.content else ""
                if not clean_content:
                    continue
                # 防御性去重: LLM可能对同一信息返回多次add
                if clean_content in field_lines[field]:
                    logger.debug(
                        "⏭️ 跳过精确重复条目 %s: %s",
                        field,
                        clean_content[:50],
                    )
                    continue
                # 语义去重: 小模型换表述重复 add 的容错(仅 add)
                if await self._is_semantic_duplicate(
                    field, clean_content, field_lines[field]
                ):
                    logger.debug(
                        "⏭️ 跳过语义重复条目 %s: %s",
                        field,
                        clean_content[:50],
                    )
                    continue
                field_lines[field].append(clean_content)
                modified_fields.add(field)
                logger.debug("📍 ADD %s: %s", field, clean_content[:50])

            elif op.action == "delete":
                target = _clean_line(op.content) if op.content else ""
                if not target:
                    continue
                # 精确字符串匹配, 未命中则跳过
                if target in field_lines[field]:
                    field_lines[field].remove(target)
                    modified_fields.add(field)
                    logger.debug("📍 DELETE %s: %s", field, target[:50])
                else:
                    logger.debug(
                        "⏭️ DELETE 未命中精确匹配 %s: %s",
                        field,
                        target[:50],
                    )

            elif op.action == "change":
                old = _clean_line(op.old_content) if op.old_content else ""
                new = _clean_line(op.new_content) if op.new_content else ""
                if not old or not new:
                    continue
                # 精确字符串匹配 old_content, 替换为 new_content
                try:
                    idx = field_lines[field].index(old)
                except ValueError:
                    logger.debug(
                        "⏭️ CHANGE 未命中精确匹配 %s: %s",
                        field,
                        old[:50],
                    )
                    continue
                field_lines[field][idx] = new
                modified_fields.add(field)
                logger.debug(
                    "📍 CHANGE %s: %s -> %s",
                    field,
                    old[:50],
                    new[:50],
                )

        if not modified_fields:
            return False

        for field in modified_fields:
            new_content = "\n".join(field_lines[field])
            if new_content.strip():
                await self._update_single_field(field, new_content)

        self._clear_related_cache()
        return True

    async def _update_single_field(self, field: str, content: str) -> None:
        """更新单个字段的记忆."""
        memory_service = await self._get_memory_service()
        await memory_service.update_memory(
            _FIELD_TYPES[field],
            content,
            self.user_id,
            self.thread_id,
        )

    def _clear_related_cache(self) -> None:
        from .cache import clear_pinned_memory

        clear_pinned_memory(self.user_id, self.thread_id, agent_id=self.agent_id)


__all__ = ["SimplePinnedMemoryManager"]
