"""置顶记忆审计 - 真实 LLM 实现(eval 接口).

设计要点(吸取旧 PinnedMemoryBootstrap 教训):
- 编号引用: 模型输出 number, 代码按 number 映射原文, 避免逐字复制长文本(转义爆炸根因)
- 无 add: 只 delete/change, 不提取新信息(噪音入口封死)
- 三类判断: keep(合格) / delete(明确不合格) / change(灰色提炼)
- 不看 TODO: 纯置顶内容判断(可选用对话历史辅助时序判断)

阶段2/3 在 eval 目录快速迭代 prompt; 稳定后(阶段4)迁入 src/.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from run_eval import AuditConfig, AuditOutput

logger = logging.getLogger(__name__)

_FIELD_LABELS = (("basic_info", "基本画像"), ("preferences", "口味偏好"))

PINNED_MEMORY_AUDIT_PROMPT = """你是置顶记忆审计员. 置顶记忆只保存"用户是谁"的稳定信息(身份事实 + 口味偏好). 审计职责: 逐条审视, 清理混入的不合格条目, 提炼范畴内表述差的.

核心判据 — 只保留"用户是谁", 清除以下三类(偏激进清理, 这是审计核心职责):
- ④过渡状态: 当前项目/公司架构/团队动态/正在用的技术栈/迁移动作/团队构成(换工作即失效, 无惯性)
- 对助手的要求(如回复风格/格式): 归 requirement_memory 工具, 不属于置顶
- 行为习惯/作息(如晨跑/通勤/工作时段): 归用户建模机制, 不属于置顶

## 判断示例

### 保留 — "用户是谁"(身份事实 + 口味)
- "对花生过敏" / "当前体重95kg" / "服用左洛复200mg/日" — 健康/生理事实(当前状态, 该留; 裹数值变化过程的提炼为当前值, 不删)
- "偏好暗色主题" — 稳定口味
- "2020年领养了狗, 叫小黑" — 宠物(身份)
- "主力Mac, 用Cursor开发" / "我会Go和Python" — 个人稳定工具/技能选型(身份)
- "毕业于浙大计算机系" — 身份(教育)

### 删除 — "用户在做什么" 或 要求 或 习惯 或 无持久价值
- "所在公司是电商平台, 后端20个Go微服务, 用gRPC, 网关Envoy" — 公司/项目架构(换工作即失效)
- "团队推进GitOps, 用ArgoCD做持续部署" — 团队当前动态
- "用OpenTelemetry替换Jaeger" — 当前迁移动作
- "监控用Prometheus+Grafana, 考虑引入PagerDuty" — 当前技术栈+未定选型
- "团队6个后端2个前端1个SRE" — 团队当前构成
- "希望助手回复更简洁" / "偏好简洁沟通" — 对助手的要求(归 requirement_memory 工具)
- "每天通勤骑自行车" / "每天晨跑3公里" — 行为习惯/作息(归用户建模机制)
- "今早备份了数据库" — 一次性动作
- "准备下周出差" — 临时任务(属TODO)
- "在研究要不要考研" — 未确定探索

### 关键区分: 个人 vs 组织
- 保留: 用户"个人"的稳定技能/工具选型 (我会Go, 用VSCode) —— "用户是谁"
- 删除: "公司/团队/项目"当前技术栈与架构 (我们/公司用Envoy, 团队接了Prometheus) —— "在做什么"

### 提炼 — 范畴内(身份/口味)但表述冗长/裹过程
- "我是一名自由职业者, 做翻译三年了, 接中日互译法律稿件" → "自由译者, 中日法律翻译"
- "近期体重从99kg降到95.7kg" → "体重95.7kg" (去数值变化过程, 留当前健康事实, 不删)
- 注意: ④条目不提炼(直接删); 已简洁稳定的偏好(如"用VSCode, 装了某插件")不必提炼

## 置顶记忆(逐条带编号, 跨字段连续编号)
{memory_block}

{history_section}## 规则
1. 范畴外(④/要求/习惯): 默认删除, 拿不准是否过渡状态时倾向删除
2. 范畴内(身份/口味): 默认保留, 拿不准保留(防误删身份)
3. 禁止 add(不提取新信息)
4. delete / change 用编号(number)引用条目

## 输出JSON
对每条置顶记忆给出判断和理由(每条都必须判断):
{{
  "judgments": [
    {{"number": 1, "action": "keep", "reason": "为什么保留"}},
    {{"number": 3, "action": "delete", "reason": "为什么删除"}},
    {{"number": 8, "action": "change", "new_content": "提炼后的精炼偏好", "reason": "为什么提炼"}}
  ]
}}
action ∈ keep/delete/change. 每条都必须给出判断和理由."""


def format_memory_with_numbers(pinned: dict) -> tuple[str, dict[int, dict[str, str]]]:
    """格式化置顶记忆为带编号块, 返回 (block, number_map).

    number_map: {编号: {field, content}} 供解析时映射原文.
    """
    items: list[tuple[int, str, str]] = []
    block_parts: list[str] = []
    num = 0
    for fld, label in _FIELD_LABELS:
        block_parts.append(f"### {label}")
        content = pinned.get(fld, "") or ""
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


def format_index(history: list[dict]) -> str:
    """格式化对话索引为紧凑文本(远期概览)."""
    lines = []
    for h in history:
        summary = (h.get("summary") or "").strip()
        topic = (h.get("topic") or "").strip()
        lines.append(f"R{h.get('round', '?')}: {topic} - {summary}")
    return "\n".join(lines)


def format_recent(turns: list[dict]) -> str:
    """格式化近轮完整对话(近期细节)."""
    parts = []
    for t in turns:
        parts.append(
            f"R{t.get('round', '?')}\n用户: {t.get('user', '')}\n助手: {t.get('assistant', '')}"
        )
    return "\n\n".join(parts)


def build_prompt(
    block: str, index_block: str, recent_block: str, with_history: bool
) -> str:
    """构建审计prompt. with_history 时给"索引(远期)+近轮完整(近期)"混合上下文."""
    if with_history:
        sections = []
        if index_block.strip():
            sections.append(
                "## 近期对话索引(远期概览, 供判断时序/过时性参考)\n" + index_block
            )
        if recent_block.strip():
            sections.append("## 最近完整对话(近期细节)\n" + recent_block)
        history_section = "\n\n".join(sections) + "\n\n" if sections else ""
    else:
        history_section = ""
    return PINNED_MEMORY_AUDIT_PROMPT.format(
        memory_block=block,
        history_section=history_section,
    )


def _extract_json(content: str) -> dict:
    """从 LLM 响应提取 JSON."""
    text = content.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"响应中未找到有效JSON: {text[:200]}")


def parse_judgments(
    content: str, number_map: dict[int, dict[str, str]]
) -> tuple[list[dict], dict[int, dict]]:
    """解析 LLM 的 judgments 输出(每条都判断+理由), 返回 (operations, all_judgments).

    operations: 仅 delete/change(供 apply_operations).
    all_judgments: number -> {action, reason, new_content?} 全量判断(含keep, 供诊断).
    拒绝 add.
    """
    data = _extract_json(content)
    raw = data.get("judgments", [])
    if not isinstance(raw, list):
        return [], {}

    operations: list[dict] = []
    all_judgments: dict[int, dict] = {}
    for j in raw:
        if not isinstance(j, dict):
            continue
        action = str(j.get("action", "")).strip().lower()
        num = j.get("number")
        reason = str(j.get("reason", "")).strip()
        if not isinstance(num, int) or num not in number_map:
            continue
        info = number_map[num]
        norm_action = action if action in ("keep", "delete", "change") else "keep"
        entry: dict = {"action": norm_action, "reason": reason}
        all_judgments[num] = entry

        if action == "delete":
            operations.append({
                "action": "delete",
                "field": info["field"],
                "content": info["content"],
            })
        elif action == "change":
            new_content = str(j.get("new_content", "")).strip()
            if new_content:
                operations.append({
                    "action": "change",
                    "field": info["field"],
                    "old_content": info["content"],
                    "new_content": new_content,
                })
                entry["new_content"] = new_content
    return operations, all_judgments


def _json_config(model_id: str) -> dict[str, Any]:
    """获取 JSON mode 配置."""
    try:
        from src.inference.llm.definitions.model_registry import get_model

        m = get_model(model_id)
        if m:
            return m.get_json_mode_config()
    except Exception as e:
        logger.debug("获取模型元数据失败: %s", e)
    if model_id.startswith("local:"):
        return {"format": "json"}
    return {"response_format": {"type": "json_object"}}


def _normalize_content(content: Any) -> str:
    """标准化多模态响应为纯文本."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            c.get("text", "") if isinstance(c, dict) else str(c) for c in content
        )
    return str(content)


def _extract_tokens(resp: Any) -> int:
    """提取 token 用量."""
    for attr in ("usage_metadata",):
        um = getattr(resp, attr, None)
        if isinstance(um, dict) and "total_tokens" in um:
            return int(um["total_tokens"])
    rm = getattr(resp, "response_metadata", {}) or {}
    token_usage = rm.get("token_usage") or rm.get("usage") or {}
    return int(token_usage.get("total_tokens", 0))


async def real_audit(fixture: dict, config: AuditConfig) -> AuditOutput:
    """真实审计: 调 LLM 逐条判断置顶记忆, 返回 operations."""
    from src.inference.llm.model_loader import create_llm

    pinned = fixture["pinned_memory"]
    history = fixture.get("conversation_index", [])

    block, number_map = format_memory_with_numbers(pinned)
    windowed = history[-config.window :] if config.window > 0 else history
    index_block = format_index(windowed)
    recent_block = format_recent(fixture.get("recent_turns", [])[-2:])

    prompt = build_prompt(block, index_block, recent_block, config.with_history)

    llm = create_llm(config.model)
    resp = await llm.ainvoke(
        [HumanMessage(content=prompt)],
        config=RunnableConfig(callbacks=[]),
        max_tokens=8192,
        **_json_config(config.model),
    )
    content = _normalize_content(resp.content)

    operations, judgments = parse_judgments(content, number_map)
    tokens = _extract_tokens(resp)

    logger.info(
        "审计完成 %s: %d ops, %d tokens",
        fixture.get("sample_id", "?"),
        len(operations),
        tokens,
    )
    return AuditOutput(operations=operations, tokens=tokens, judgments=judgments)
