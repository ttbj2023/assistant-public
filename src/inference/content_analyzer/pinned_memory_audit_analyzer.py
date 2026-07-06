"""置顶记忆审计分析器 - 周期性"读全局"整理(delete/change, 无 add).

设计要点(吸取旧 PinnedMemoryBootstrap 教训):
- 编号引用: 模型输出 number, 代码按 number_map 映射原文, 避免逐字复制长文本(转义爆炸根因)
- 无 add: 只 delete/change, 新信息由每轮 1-step 负责(噪音入口封死)
- 纯摘要上下文: 远期对话索引概览; 不给近轮原文(实测近轮"变化"信息诱导误删)

经评测集(24样本, 含真实/合成/画像)验证: scope 条件式清理 ——
范畴外(④/要求/习惯)偏激进删除, 范畴内(身份/口味)默认保留(防误删身份).
跨职业/身份/年龄画像泛化良好.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

from src.core.types import MemoryOperation
from src.inference.llm.model_loader import invoke_with_fallback
from src.inference.llm.response_utils import content_to_text

logger = logging.getLogger(__name__)

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
3. 禁止 add(不提取新信息, 新信息由每轮小更新负责)
4. delete / change 用编号(number)引用条目

## 输出JSON
{{
  "operations": [
    {{"action": "delete", "number": 3, "reason": "..."}},
    {{"action": "change", "number": 8, "new_content": "精炼偏好", "reason": "..."}}
  ]
}}
无操作返回 {{"operations": []}}."""


def build_prompt(memory_block: str, index_block: str) -> str:
    """构建审计 prompt. 纯摘要上下文(远期索引概览)."""
    if index_block.strip():
        history_section = (
            "## 近期对话索引(远期概览, 供判断时序/过时性参考)\n" + index_block + "\n\n"
        )
    else:
        history_section = ""
    return PINNED_MEMORY_AUDIT_PROMPT.format(
        memory_block=memory_block,
        history_section=history_section,
    )


def _extract_json(content: str) -> dict:
    text = content.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"响应中未找到有效JSON: {text[:200]}")


def parse_operations(
    content: str, number_map: dict[int, dict[str, str]]
) -> list[MemoryOperation]:
    """解析 LLM 输出为 apply_operations 兼容的 operations.

    模型输出 number, 代码按 number_map 映射原文, 避免逐字复制. 拒绝 add.
    返回 MemoryOperation 对象(与每轮 1-step 路径入参类型一致),
    供 apply_operations 直接消费.
    """
    data = _extract_json(content)
    ops_raw = data.get("operations", [])
    if not isinstance(ops_raw, list):
        return []

    result: list[MemoryOperation] = []
    for op in ops_raw:
        if not isinstance(op, dict):
            continue
        action = str(op.get("action", "")).strip().lower()
        if action not in ("delete", "change"):
            continue
        num = op.get("number")
        if not isinstance(num, int) or num not in number_map:
            logger.warning("审计跳过无效编号: %s", num)
            continue
        info = number_map[num]

        if action == "delete":
            result.append(
                MemoryOperation(
                    action="delete",
                    field=info["field"],
                    content=info["content"],
                )
            )
        elif action == "change":
            new_content = str(op.get("new_content", "")).strip()
            if not new_content:
                continue
            result.append(
                MemoryOperation(
                    action="change",
                    field=info["field"],
                    old_content=info["content"],
                    new_content=new_content,
                )
            )
    return result


def _normalize_content(content: Any) -> str:
    return content_to_text(content)


class PinnedMemoryAuditAnalyzer:
    """置顶记忆审计分析器.

    周期触发, 读"当前置顶 + 远期摘要索引", 输出 delete/change operations(无 add).
    """

    def __init__(
        self, model_id: str, model_params: dict[str, Any] | None = None
    ) -> None:
        self.model_id = model_id
        self.model_params = model_params or {}

    async def audit(
        self,
        memory_block: str,
        number_map: dict[int, dict[str, str]],
        index_block: str,
    ) -> list[MemoryOperation]:
        """执行审计, 返回 operations.

        Args:
            memory_block: 带 [N] 编号的置顶记忆文本
            number_map: {编号: {field, content}}
            index_block: 远期对话索引概览

        Returns:
            operations 列表(action=delete/change), 供 apply_operations 应用

        """
        prompt = build_prompt(memory_block, index_block)
        from src.inference.usage import usage_source

        with usage_source("memory_analyzer"):
            resp = await invoke_with_fallback(
                [HumanMessage(content=prompt)],
                self.model_id,
                self.model_params,
                fallback_kind="text",
                usage_tag="memory_analyzer",
                primary_json_log_level=logging.DEBUG,
                max_tokens=8192,
            )
        content = _normalize_content(resp.content)
        operations = parse_operations(content, number_map)
        logger.info("置顶审计完成: %d operations", len(operations))
        return operations
