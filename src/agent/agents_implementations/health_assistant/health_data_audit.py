"""健康数据审计任务 - 基于轮次驱动的定期查漏补缺机制.

轮次驱动架构:
- 每10轮触发一次审计, 替代该轮的常规提取器
- 一次调用同时完成: 本轮新数据提取 + 历史数据审计
- 模型输出 extractions(新数据) + operations(CUD操作), 逐条执行
- Fire-and-forget: 不阻塞主流程, 失败仅记录日志
- 进程级 dict 追踪, 重启自然重置
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from src.inference.llm.response_utils import content_to_text
from src.storage.service.health_data_extraction_service import (
    get_health_data_extraction_service,
)

logger = logging.getLogger(__name__)

AUDIT_INTERVAL = 10
AUDIT_WINDOW = 15

_last_audit_round: dict[str, int] = {}

_PROMPTS_PATH = Path(__file__).parent / "prompts" / "health_data_audit.yaml"

_VALID_DATA_TYPES = {
    "weight_record",
    "meal_record",
    "workout_record",
    "shopping_list",
    "food_product",
    "medical_report",
}


def _make_key(user_id: str, thread_id: str, agent_id: str) -> str:
    return f"{user_id}:{thread_id}:{agent_id}"


def should_audit(
    user_id: str,
    thread_id: str,
    agent_id: str,
    current_round: int,
) -> bool:
    """检查是否应该触发审计 (基于轮次间隔)."""
    key = _make_key(user_id, thread_id, agent_id)
    last = _last_audit_round.get(key, 0)
    return current_round - last >= AUDIT_INTERVAL


def mark_audited(
    user_id: str,
    thread_id: str,
    agent_id: str,
    round_number: int,
) -> None:
    """记录已完成审计的轮次."""
    key = _make_key(user_id, thread_id, agent_id)
    _last_audit_round[key] = round_number
    logger.info("健康数据审计标记完成: %s, round=%s", key, round_number)


def clear_audit_state() -> None:
    """清空所有审计状态(主要用于测试)."""
    _last_audit_round.clear()


async def load_data_snapshot(
    user_id: str,
    thread_id: str,
    agent_id: str,
    current_round: int,
) -> str:
    """加载最近N轮的健康数据快照.

    按类型分组序列化, 每条记录带 [ID:N] 前缀供模型引用.

    Returns:
        格式化的数据快照字符串, 无数据返回空字符串

    """
    try:
        service = get_health_data_extraction_service(
            user_id,
            thread_id,
            agent_id=agent_id,
        )
        min_round = max(1, current_round - AUDIT_WINDOW + 1)
        return await service.get_extraction_snapshot(min_round)
    except Exception as e:
        logger.warning("加载数据快照失败 (%s:%s): %s", user_id, thread_id, e)
        return ""


async def run_audit(
    user_id: str,
    thread_id: str,
    agent_id: str,
    current_round: int,
    user_message: str | None = None,
    attachment_infos: list[Any] | None = None,
) -> None:
    """执行健康数据审计+提取 (替代该轮的常规提取器).

    流程:
    1. 拼接本轮用户消息 (含图片描述)
    2. 加载最近N轮数据快照
    3. 发送给 LLM: 提取新数据 + 审计历史数据
    4. 存储 extractions + 执行 operations
    5. 记录审计轮次
    """
    try:
        message_text = _build_message_text(user_message, attachment_infos)

        snapshot = await load_data_snapshot(user_id, thread_id, agent_id, current_round)

        if not message_text and not snapshot:
            logger.debug("健康数据审计: 无数据, 跳过 (%s:%s)", user_id, thread_id)
            mark_audited(user_id, thread_id, agent_id, current_round)
            return

        logger.info(
            "健康数据审计+提取: 开始 (%s:%s) round=%s",
            user_id,
            thread_id,
            current_round,
        )

        result = await _call_audit_llm(message_text, snapshot)

        extractions = result.get("extractions", [])
        operations = result.get("operations", [])

        if extractions:
            logger.info(f"健康数据审计: 提取到 {len(extractions)} 条新数据")
            await _store_extractions(
                user_id,
                thread_id,
                agent_id,
                extractions,
                current_round,
            )

        if operations:
            logger.info(f"健康数据审计: 收到 {len(operations)} 条操作")
            await _execute_operations(user_id, thread_id, agent_id, operations)

        if not extractions and not operations:
            logger.debug("健康数据审计: 无需操作 (%s:%s)", user_id, thread_id)

        mark_audited(user_id, thread_id, agent_id, current_round)
        logger.info("健康数据审计+提取: 完成 (%s:%s)", user_id, thread_id)

    except Exception as e:
        logger.warning("健康数据审计异常 (%s:%s): %s", user_id, thread_id, e)
        mark_audited(user_id, thread_id, agent_id, current_round)


def _build_message_text(
    user_message: str | None,
    attachment_infos: list[Any] | None,
) -> str:
    """拼接用户消息和图片描述."""
    if not user_message:
        return ""
    parts = [f"用户消息:\n{user_message}"]
    if attachment_infos:
        image_descriptions = []
        for i, info in enumerate(attachment_infos, 1):
            desc = getattr(info, "detail", None)
            if desc and desc != "图片":
                image_descriptions.append(f"[图片{i}描述]: {desc}")
        if image_descriptions:
            parts.append("图片内容:\n" + "\n".join(image_descriptions))
    return "\n\n".join(parts)


async def _call_audit_llm(
    user_message: str,
    data_snapshot: str,
) -> dict[str, list[dict[str, Any]]]:
    """通过项目标准 LLM 调用体系调用审计模型, 返回 extractions + operations."""
    from langchain_core.messages import HumanMessage

    from src.inference.llm.model_loader import create_llm
    from src.inference.usage import usage_source

    prompt_template = _load_audit_prompt()
    current_date = date.today().strftime("%Y-%m-%d")

    prompt = (
        prompt_template
        .replace("{current_date}", current_date)
        .replace("{user_message}", user_message or "本轮无用户消息")  # noqa: RUF027
        .replace("{data_snapshot}", data_snapshot or "暂无历史数据")  # noqa: RUF027
    )

    model_id = _get_model_id()
    params = _get_model_params()
    llm = create_llm(model_id)
    if params:
        llm = llm.bind(**params)

    json_config = _get_json_mode_config(model_id)
    with usage_source("health_extraction"):
        response = await llm.ainvoke(
            [HumanMessage(content=prompt)],
            **json_config,
        )

    content = response.content
    text_content = content_to_text(content)

    if not text_content:
        return {"extractions": [], "operations": []}

    try:
        raw = json.loads(text_content)
    except json.JSONDecodeError:
        return {"extractions": [], "operations": []}

    raw_extractions = raw.get("extractions", [])
    if not isinstance(raw_extractions, list):
        raw_extractions = []
    extractions = [
        e
        for e in raw_extractions
        if isinstance(e, dict)
        and e.get("data_type") in _VALID_DATA_TYPES
        and e.get("data")
    ]

    raw_operations = raw.get("operations", [])
    if not isinstance(raw_operations, list):
        raw_operations = []
    valid_actions = {"create", "update", "delete"}
    operations = []
    for op in raw_operations:
        if not isinstance(op, dict):
            continue
        action = op.get("action", "")
        data_type = op.get("data_type", "")
        if action not in valid_actions or data_type not in _VALID_DATA_TYPES:
            continue
        if action in {"update", "delete"} and not op.get("record_id"):
            continue
        if action == "create" and not op.get("data"):
            continue
        operations.append(op)

    return {"extractions": extractions, "operations": operations}


async def _store_extractions(
    user_id: str,
    thread_id: str,
    agent_id: str,
    extractions: list[dict[str, Any]],
    round_number: int,
) -> None:
    """存储提取的新健康数据."""
    service = get_health_data_extraction_service(user_id, thread_id, agent_id=agent_id)
    for ext in extractions:
        try:
            result = await service.store_extraction(
                data_type=ext["data_type"],
                data=ext["data"],
                round_number=round_number,
            )
            if result.get("success"):
                logger.info(f"审计提取: {ext['data_type']}")
            else:
                logger.warning(f"审计提取失败: {result.get('error')}")
        except Exception as e:
            logger.warning(f"审计提取异常 ({ext.get('data_type')}): {e}")


async def _execute_operations(
    user_id: str,
    thread_id: str,
    agent_id: str,
    operations: list[dict[str, Any]],
) -> None:
    """逐条执行审计操作."""
    service = get_health_data_extraction_service(user_id, thread_id, agent_id=agent_id)

    for op in operations:
        action = op["action"]
        data_type = op["data_type"]
        reason = op.get("reason", "")

        try:
            result = await service.execute_audit_operation(
                action=action,
                data_type=data_type,
                record_id=op.get("record_id"),
                data=op.get("data"),
            )
            if result.get("success"):
                label = (
                    f"{data_type}#{op.get('record_id', '')}"
                    if action != "create"
                    else data_type
                )
                logger.info("审计%s: %s (%s)", action, label, reason)
            else:
                logger.warning(f"审计{action}失败: {result.get('error')}")

        except Exception as e:
            logger.warning("审计操作执行异常 (%s %s): %s", action, data_type, e)


def _load_audit_prompt() -> str:
    """加载审计 prompt 模板."""
    if not _PROMPTS_PATH.exists():
        raise FileNotFoundError(f"审计 prompt 文件不存在: {_PROMPTS_PATH}")
    with _PROMPTS_PATH.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    template = config.get("audit", "")
    if not template:
        raise ValueError("YAML 中未找到 audit 模板")
    return template


def _get_model_id() -> str:
    """获取审计用的模型ID (优先使用审计专属模型)."""
    try:
        from src.config.inference_config import get_config

        inference = get_config()
        audit_model = getattr(inference.health_data_extraction, "audit_model", "")
        if audit_model and ":" in audit_model:
            return audit_model
        model_id = inference.health_data_extraction.model
        if model_id and ":" in model_id:
            return model_id
    except Exception as e:
        logger.warning("审计模型配置获取失败, 使用兜底默认模型: %s", e)
    # 兜底值对齐 audit_model Field default (审计任务用 pro)
    return "ark-agent-plan:doubao-seed-2.0-pro"


def _get_model_params() -> dict[str, Any]:
    """获取审计用的模型bind参数 (优先使用审计专属参数)."""
    try:
        from src.config.inference_config import get_config

        inference = get_config()
        audit_model = getattr(inference.health_data_extraction, "audit_model", "")
        if audit_model and ":" in audit_model:
            audit_params = getattr(
                inference.health_data_extraction, "audit_model_params", {}
            )
            if audit_params:
                return audit_params
        return inference.health_data_extraction.model_params
    except Exception as e:
        logger.warning("审计模型参数获取失败, 使用空参数: %s", e)
        return {}


def _get_json_mode_config(model_id: str) -> dict[str, Any]:
    """获取模型的 JSON 模式配置."""
    from src.inference.llm.json_mode_config import get_json_mode_config

    return get_json_mode_config(model_id)


__all__ = [
    "AUDIT_INTERVAL",
    "AUDIT_WINDOW",
    "clear_audit_state",
    "load_data_snapshot",
    "mark_audited",
    "run_audit",
    "should_audit",
]
