"""置顶记忆 1-step 提取评估脚本.

评估每轮置顶记忆提取(analyze_pinned_memory_update)在评测样本上的表现.

样本类型:
- negative: user_message 是噪音(元操作/探索结果/计划/请求/一次性), GT 应该 0 add
- positive: user_message 透露高价值细节, GT 应该 add 某条
- mixed:    混合(部分该记部分不该)

指标(precision 优先, 1-step 是噪音入口):
- recall:    该 add 的(should_add 关键词)被记了的比例. negative 样本 N/A
- precision: add 的内容里, 该 add 的比例
- false_add: 不该 add 却 add 了的数量(噪音, 最关键)
- unexpected: 既非 should_add 也非 should_not_add 的 add(人工复查)

实验维度:
- prompt: old(现有类型列举式) / new(目标+few-shot 式)

用法:
    python run_eval.py                          # 默认 new prompt + config模型
    python run_eval.py --prompt old             # 旧prompt基线
    python run_eval.py --model doubao:...       # 指定模型
    python run_eval.py --verbose                # 打印每个add详情
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import pathlib
import re
import sys
from dataclasses import dataclass, field

_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

FIX_DIR = pathlib.Path(__file__).parent / "fixtures"
_FIELDS = ("basic_info", "preferences")

_FIELD_LABELS = {"basic_info": "基本画像", "preferences": "偏好与要求"}

# 旧基线 prompt (scope_v2 提升生产前) 仅作历史参照, 不再用于评测.
# 当前生产 prompt = SimpleContentAnalyzer.PINNED_MEMORY_UPDATE_PROMPT,
# eval "--prompt new" 直接读生产 (不本地复制, 杜绝漂移).


# scope_v2: 已提升生产 (与 SimpleContentAnalyzer.PINNED_MEMORY_UPDATE_PROMPT 同步).
# 保留为可编辑实验基线, 供后续 prompt A/B 迭代; "要求"已移交 requirement_memory 工具.
SCOPE_V2_PROMPT = """你是置顶记忆维护助手. 置顶记忆只保存"用户是谁"的稳定信息(身份事实 + 口味偏好), 让助手长期了解这个人. 严禁记录"用户在做什么"——当前项目、公司架构、团队动态、正在用的技术栈、正在考虑的选型, 这些会随项目/工作变化, 属过渡状态, 不归置顶记忆.

注意: 用户对助手"该如何响应/运作的要求"(如"回复简洁""用英文回复")不归置顶记忆, 由 requirement_memory 工具处理, 不要记录.

## 准入判据(同时满足才记)
1. 陈述可得: 用户明确说出口, 非从行为推断
2. 是"谁"不是"做什么": 描述用户的稳定属性/口味, 不是当前工作/项目/团队/基础设施
3. 有惯性: 测试——"这条信息一周不联系, 还该默认成立吗?" 必须明显为"是"

## 用户本轮输入
{user_message}

## 当前TODO列表 (动态任务, 不记入置顶记忆)
{todo_list}

## 当前置顶记忆
{memory_block}

## 两个字段
- basic_info: 用户身份事实(姓名/所在地/职业/技能/宠物/联系方式/长期生理事实如过敏)
- preferences: 口味偏好(喜欢什么: 书/游戏/食物/风格/稳定的工具选型)

## 该记 — "用户是谁"
- "我叫张三, 在杭州做产品经理" — 身份
- "养了一只叫Nemo的美短猫" — 宠物(身份)
- "对海鲜过敏" — 长期生理事实
- "喜欢科幻小说, 最爱三体" — 口味
- "主力Mac, 用Cursor开发" / "我用VSCode写Python" — 用户个人的稳定工具选型(身份)

## 不该记 — "用户在做什么" 或 无持久价值 或 属其他工具
- "我们公司是电商平台, 后端20个Go微服务, 用gRPC, 网关Envoy" — 公司/项目架构(换工作即失效)
- "团队最近在推进GitOps, 用ArgoCD做持续部署" — 团队当前动态
- "用OpenTelemetry替换了Jaeger" — 当前迁移动作
- "API网关配置了限流熔断, 接了Prometheus" — 当前基础设施配置
- "监控用Prometheus+Grafana, 考虑引入PagerDuty" — 当前栈+未定选型
- "团队6个后端2个前端1个SRE" — 团队当前构成
- "偏好简洁直接的回复" — 对助手的要求(归 requirement_memory 工具, 不记入置顶)
- "每天早上6点晨跑3公里" — 行为习惯/作息(归用户建模机制, 非置顶记忆)
- "今早把季度报告交了" — 一次性动作
- "打算开始学吉他" — 未确定意向(属TODO)

## 关键区分: 个人 vs 组织
- 该记: 用户"个人"的稳定工具/技能选型 (我用VScode/Mac, 会Go/Python) —— 这是"用户是谁"
- 不记: "公司/团队/项目"当前用的技术栈与架构 (我们/公司用Envoy, 团队接了Prometheus, 后端20个服务) —— 这是"在做什么"

## 判定原则
- 拿不准是否持久时, 倾向拒绝(误记的噪音污染长期记忆; 漏记可由对话历史补回)
- 涉及"公司/项目/团队/正在做的迁移/正在考虑的选型"语境, 默认拒绝
- 注意: "我用X开发/我会X" 属个人技能工具选型, 不属上述拒绝语境, 该记

## 操作
- 新细节 -> add (field + content; 用中文; 记简洁结论, 不裹时间/过程表述)
- 与现有条目语义重叠 -> 不操作
- 现有条目已不适用 -> delete (content须与某行逐字一致)
- 现有条目需更新 -> change (old_content逐字一致; new_content为替换全文)

返回JSON:
{{
    "has_operations": true/false,
    "operations": [
        {{"action": "add", "field": "basic_info", "content": "..."}},
        {{"action": "delete", "field": "preferences", "content": "原文"}},
        {{"action": "change", "field": "preferences", "old_content": "原文", "new_content": "新全文"}}
    ]
}}"""


def format_memory_block(memory: dict[str, str]) -> str:
    """复刻 SimplePinnedMemoryManager.get_memory_for_analysis 的格式."""
    lines: list[str] = []
    for fld in _FIELDS:
        lines.append(f"### {_FIELD_LABELS[fld]}")
        content = (memory.get(fld) or "").strip()
        if content:
            for item in content.split("\n"):
                item = re.sub(r"^\[\d+\]\s*", "", item.strip())
                if item:
                    lines.append(item)
        else:
            lines.append("(空)")
        lines.append("")
    return "\n".join(lines)


@dataclass
class EvalConfig:
    model: str = ""
    prompt: str = "new"
    model_params: dict = field(default_factory=dict)


@dataclass
class SampleMetrics:
    sample_id: str
    sample_type: str
    add_count: int
    tp: int
    false_add: int
    unexpected: int
    covered: int
    recall: float
    precision: float
    unexpected_details: list[str] = field(default_factory=list)
    false_details: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    """归一化: 去标点/空格, 便于关键词子串匹配."""
    return re.sub(r"[\s，。、；：！？“”‘’（）()\[\]·/\-]", "", text)


def _match_any(text: str, keywords: list[str]) -> bool:
    """归一化后, text 是否含任一关键词(也归一化)."""
    if not keywords:
        return False
    norm = _normalize(text)
    return any(_normalize(k) in norm for k in keywords)


def compute_metrics(
    sample_id: str,
    sample_type: str,
    operations: list[dict],
    gt: dict,
) -> SampleMetrics:
    """计算指标. 只看 add 操作(1-step 主要测噪音入口)."""
    adds = [
        (op.get("field", ""), op.get("content", ""))
        for op in operations
        if op.get("action") == "add" and op.get("content")
    ]

    should_add = gt.get("should_add", [])
    should_not_add = gt.get("should_not_add", [])

    tp = 0
    false_add = 0
    unexpected = 0
    unexpected_details: list[str] = []
    false_details: list[str] = []

    for _field, content in adds:
        if _match_any(content, should_add):
            tp += 1
        elif _match_any(content, should_not_add):
            false_add += 1
            false_details.append(f"{_field}: {content[:50]}")
        else:
            unexpected += 1
            unexpected_details.append(f"{_field}: {content[:50]}")

    # recall: should_add 每个关键词被任一 add 覆盖的比例(一条add可覆盖多个词)
    covered = sum(
        1 for kw in should_add if any(_match_any(content, [kw]) for _f, content in adds)
    )
    recall = covered / len(should_add) if should_add else float("nan")
    total_add = len(adds)
    precision = tp / total_add if total_add else 1.0

    return SampleMetrics(
        sample_id=sample_id,
        sample_type=sample_type,
        add_count=total_add,
        tp=tp,
        false_add=false_add,
        unexpected=unexpected,
        covered=covered,
        recall=recall,
        precision=precision,
        unexpected_details=unexpected_details,
        false_details=false_details,
    )


def load_fixtures() -> list[dict]:
    return [
        json.loads((FIX_DIR / f).read_text(encoding="utf-8"))
        for f in sorted(FIX_DIR.glob("*.json"))
        if not f.name.startswith("_")
    ]


async def run_sample(
    fixture: dict, config: EvalConfig
) -> tuple[SampleMetrics, list[dict]]:
    """对单个样本跑 1-step 提取, 返回指标 + 原始 operations."""
    from src.inference.content_analyzer.simple_analyzer import SimpleContentAnalyzer

    override: dict = {}
    if config.model:
        override["model_id"] = config.model
    if config.model_params:
        override["model_params"] = config.model_params

    analyzer = SimpleContentAnalyzer(config_override=override if override else None)
    # "new" 直接用生产 prompt (SimpleContentAnalyzer 类默认), 不本地复制以杜绝漂移;
    # "scope_v2" 为可编辑的实验变体 (当前同步为生产设计, 供后续 A/B 迭代)
    if config.prompt == "scope_v2":
        analyzer.PINNED_MEMORY_UPDATE_PROMPT = SCOPE_V2_PROMPT

    memory_block = format_memory_block(fixture["current_memory"])
    todo_list = fixture.get("todo_list") or "(无)"

    result = await analyzer.analyze_pinned_memory_update(
        user_message=fixture["user_message"],
        todo_list=todo_list,
        memory_block=memory_block,
    )

    ops = [dict(op) for op in result.operations]
    metrics = compute_metrics(
        fixture["sample_id"],
        fixture["sample_type"],
        ops,
        fixture["ground_truth"],
    )
    return metrics, ops


def _fmt_pct(x: float) -> str:
    if math.isnan(x):
        return "  N/A"
    return f"{x:.0%}"


def format_row(m: SampleMetrics) -> str:
    return (
        f"{m.sample_id:28s} {m.sample_type:9s} add={m.add_count}  "
        f"tp={m.tp}  false={m.false_add}  unexp={m.unexpected}  "
        f"{_fmt_pct(m.recall):>6s}  {_fmt_pct(m.precision):>6s}"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="置顶记忆 1-step 提取评估")
    parser.add_argument(
        "--prompt",
        default="new",
        choices=["new", "scope_v2"],
        help="new=生产prompt(直接读SimpleContentAnalyzer); scope_v2=实验变体",
    )
    parser.add_argument("--model", default="", help="指定模型ID(空=用config.yaml)")
    parser.add_argument("--verbose", action="store_true", help="打印每个add/误判详情")
    parser.add_argument(
        "--reasoning", action="store_true", help="开思考模式(local模型)"
    )
    parser.add_argument(
        "--no-reasoning",
        action="store_true",
        help="显式关闭思考(local模型如 qwen3.5:9b 默认开思考, 非思考模式需显式关闭)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=0,
        help="num_predict(思考模式需给大值, 如8192)",
    )
    parser.add_argument("--limit", type=int, default=0, help="只跑前N个样本")
    args = parser.parse_args()

    fixtures = load_fixtures()
    if args.limit:
        fixtures = fixtures[: args.limit]
    if not fixtures:
        print("无评测样本(请先在 fixtures/ 建样本)")
        return

    params: dict = {}
    if args.reasoning:
        params["reasoning"] = True
    if args.no_reasoning:
        params["reasoning"] = False
    if args.max_tokens:
        params["num_predict"] = args.max_tokens
    config = EvalConfig(model=args.model, prompt=args.prompt, model_params=params)

    model_desc = args.model or "(config.yaml)"
    print(f"=== 1-step 提取评估 | prompt={args.prompt} | model={model_desc} ===\n")
    header = f"{'sample':28s} {'type':9s} {'adds':16s} {'recall':>6s}  {'prec':>6s}"
    print(header)
    print("-" * 82)

    total_false = 0
    total_unexp = 0
    total_tp = 0
    total_covered = 0
    total_should_add = 0
    total_add = 0

    for fx in fixtures:
        m, ops = await run_sample(fx, config)
        print(format_row(m))
        total_false += m.false_add
        total_unexp += m.unexpected
        total_tp += m.tp
        total_covered += m.covered
        total_add += m.add_count
        total_should_add += len(fx["ground_truth"].get("should_add", []))

        if args.verbose and (m.false_details or m.unexpected_details):
            for d in m.false_details:
                print(f"      ✗ FALSE_ADD {d}")
            for d in m.unexpected_details:
                print(f"      ? UNEXPECTED {d}")

    print("-" * 82)
    overall_prec = total_tp / total_add if total_add else 1.0
    overall_rec = total_covered / total_should_add if total_should_add else float("nan")
    print(
        f"{'TOTAL':28s} {'':9s} add={total_add}  "
        f"false={total_false}  unexp={total_unexp}  "
        f"{_fmt_pct(overall_rec):>6s}  {_fmt_pct(overall_prec):>6s}"
    )
    print(
        f"\n提取成功率 recall = {_fmt_pct(overall_rec)} "
        f"[primary: 漏记审计补不回] | 噪音 = {total_false + total_unexp} [audit可清]"
    )


if __name__ == "__main__":
    asyncio.run(main())
