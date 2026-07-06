#!/usr/bin/env python
"""chart_maker skill 端到端验证脚本.

模拟 skill 完全展开后的上下文 (L1+L2+L3+工具schema), 让真实 LLM 生成图表代码,
渲染为 PNG, Doubao Pro 做代码质量预筛, 通过的供人工评审.

用法:
    python scripts/verify_chart_skill.py                     # 默认全量运行
    python scripts/verify_chart_skill.py --cases 1,5,10      # 只跑指定用例
    python scripts/verify_chart_skill.py --no-evaluate        # 跳过 Doubao 评估
    python scripts/verify_chart_skill.py --model "deepseek:deepseek-v4-pro"

前置条件:
    - tool-runtime 容器运行在 localhost:8766
    - .env 中配置了 DEEPSEEK_API_KEY 和 DOUBAO 相关 key
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import re
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from langchain_core.messages import HumanMessage, SystemMessage

from src.inference.llm.model_loader import create_llm

logger = logging.getLogger("verify_chart_skill")

TOOL_RUNTIME_URL = "http://127.0.0.1:8766"
SKILL_DIR = PROJECT_ROOT / "skills" / "chart_maker"
OUTPUT_DIR = PROJECT_ROOT / "reports" / "chart_skill_verification"

GENERATION_MODEL_DEFAULT = "deepseek:deepseek-v4-pro"
EVALUATION_MODEL = "ark-agent-plan:doubao-seed-2.0-pro"

MERMAID_TOOL_DESC = textwrap.dedent("""\
    mermaid_chart: 渲染 mermaid 流程图/时序图/甘特图为 PNG.
    code 必须是 mermaid 语法源码 (如 'graph TD\\nA-->B').
    支持: flowchart (流程图), sequenceDiagram (时序图), gantt (甘特图), pie (饼图), stateDiagram (状态图), classDiagram (类图) 等.
    参数: code (必须, mermaid源码), title (可选, 标题), scale (可选, 1标准/3默认/6最大).""")

VEGA_TOOL_DESC = textwrap.dedent("""\
    vega_chart: 渲染 Vega-Lite 数据图表为 PNG.
    code 必须是完整的 Vega-Lite JSON spec 字符串 (如 '{"mark":"bar","encoding":{...}}').
    支持折线/柱状/饼/散点/堆叠/面积等统计图表.
    参数: code (必须, JSON spec), title (可选), width/height (可选, px), scale (可选, 1-6).""")

MARKMAP_TOOL_DESC = textwrap.dedent("""\
    markmap_chart: 渲染 markmap 思维导图为 PNG.
    code 必须是 Markdown 源码 (标题层级#/列表- 表达树状结构).
    参数: code (必须, Markdown), title (可选), width/height (可选, 默认1200x800), scale (可选, 1-6).""")


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


@dataclass
class TestCase:
    idx: int
    name: str
    engine: str
    chart_type: str
    reference: str
    difficulty: str
    prompt: str


TEST_CASES: list[TestCase] = [
    TestCase(
        1, "mermaid_flowchart_ecommerce", "mermaid", "flowchart", "mermaid",
        "10+节点, 多分支, subgraph, 异常处理",
        "画一个完整的电商订单处理流程图: 用户下单 → 库存检查(有库存/缺货两种分支) → "
        "支付(微信/支付宝/银行卡三种方式, 含支付超时分支) → 发货(顺丰/中通) → "
        "物流跟踪 → 签收 → 评价. 同时包含异常处理: 退款流程(已支付→申请退款→审核→退款到账) "
        "和退货流程(已签收→申请退货→审核→退货物流→退款).",
    ),
    TestCase(
        2, "mermaid_sequence_microservice", "mermaid", "sequenceDiagram", "mermaid",
        "多参与者, loop+alt嵌套, 异步消息",
        "画一个微服务调用链时序图: 用户 → API网关 → 认证服务(验证Token) → "
        "订单服务(创建订单) → 库存服务(扣减库存) → 支付服务(处理支付) → "
        "消息队列(发送通知) → 通知服务(推送消息). "
        "包含两个异常分支: 1) 认证失败时网关直接返回401; "
        "2) 库存不足时订单服务返回缺货提示. "
        "支付完成后有循环心跳: 订单服务定期查询支付状态直到成功.",
    ),
    TestCase(
        3, "mermaid_flowchart_approval", "mermaid", "flowchart", "mermaid",
        "多角色泳道, 多级审批, 并行分支, 回退",
        "画一个采购审批流程图: 申请人提交采购单 → "
        "部门经理初审(金额<1万直接通过, >=1万转总监审批, <0违反规则打回) → "
        "总监审批(通过/驳回/要求修改) → "
        "并行分支: 财务部审核预算 + 法务部审核合同条款 → "
        "汇总(都通过则进入下一步, 任一不通过则驳回) → "
        "采购执行(下单 → 收货 → 验收) → 归档. "
        "驳回可回到申请人修改后重新提交. 用subgraph区分各部门.",
    ),
    TestCase(
        4, "mermaid_flowchart_git", "mermaid", "flowchart", "mermaid",
        "用流程图表达状态流转(含回退/分支/合并)",
        "用流程图画一个Git工作流: 文件从Untracked开始 → git add变为Staged → "
        "git commit变为Committed → git push变为Pushed → 创建Pull Request → "
        "PR审核结果: Merged(合并到主线, 结束) 或 Rejected(打回). "
        "补充操作: Staged可git reset回Untracked; "
        "Committed可git checkout创建新Branch; "
        "Rejected修改后重新Commit再Push; "
        "Merge冲突时进入Conflict Resolving, 解决后重新提交PR.",
    ),
    TestCase(
        5, "vega_stacked_bar_3series", "vega_lite", "stacked bar", "vega_lite",
        "用户痛点: 3叠层数据, 需图例+单位",
        "用以下数据画一个堆叠柱状图, 展示3个季度各业务线的营收构成(单位万元): "
        "Q1: 云服务200, 广告150, 游戏100; "
        "Q2: 云服务250, 广告180, 游戏80; "
        "Q3: 云服务300, 广告200, 游戏120. "
        "需要: 图例标注三个业务线(云服务/广告/游戏), Y轴标注单位'万元', "
        "X轴按季度排列, 每根柱子内三层颜色区分.",
    ),
    TestCase(
        6, "vega_dual_axis", "vega_lite", "dual-axis bar+line", "vega_lite",
        "双Y轴(柱+线), 两种数据维度",
        "用以下数据画一个双轴图: "
        "左Y轴是柱状图, 显示月度营收(万元): 1月320, 2月280, 3月410, 4月520; "
        "右Y轴是折线图, 显示环比增长率(%): 1月15, 2月-12, 3月46, 4月27. "
        "需要: 左轴标签'营收(万元)', 右轴标签'增长率(%)', "
        "图例区分柱和线, X轴显示月份.",
    ),
    TestCase(
        7, "vega_grouped_bar_compare", "vega_lite", "grouped bar", "vega_lite",
        "多维度分组, 年份对比",
        "用以下数据画分组柱状图, 对比2023和2024各季度营收(万元): "
        "2023: Q1=300, Q2=350, Q3=400, Q4=450; "
        "2024: Q1=380, Q2=420, Q3=480, Q4=520. "
        "需要: 每个季度两根柱子(2023和2024), 不同颜色区分年份, "
        "图例标注年份, X轴显示季度, Y轴标注单位.",
    ),
    TestCase(
        8, "vega_pie_6slices", "vega_lite", "pie/arc", "vega_lite",
        "6+扇区, 需清晰标注",
        "用以下数据画一个饼图, 展示月度支出构成: "
        "食品 35%, 交通 15%, 住房 30%, 娱乐 10%, 医疗 5%, 其他 5%. "
        "需要: 6个扇区清晰标注类别名和百分比, 图例放在右侧.",
    ),
    TestCase(
        9, "vega_line_timeseries", "vega_lite", "line (time series)", "vega_lite",
        "时间序列, X轴日期格式化, 数据点标记",
        "用以下数据画一个折线图, 展示某网站3月每日DAU(日活用户): "
        "3/1=12000, 3/2=11500, 3/3=9000(周末低), 3/4=12500, 3/5=13000, "
        "3/6=12800, 3/7=11000(周末), 3/8=9500(周末), "
        "3/9=14000, 3/10=13500, 3/11=14200, 3/12=14800. "
        "需要: X轴显示日期(月/日格式), 有数据点标记, Y轴标注'DAU', "
        "折线连线平滑显示趋势.",
    ),
    TestCase(
        10, "markmap_knowledge_tree", "markmap", "mindmap (deep)", "markmap",
        "4+层深, 多分支, 富格式",
        "把以下知识体系画成思维导图: "
        "机器学习分为监督学习、无监督学习、强化学习、深度学习四大分支. "
        "监督学习包括分类(逻辑回归/SVM/决策树/随机森林/XGBoost)和回归(线性回归/Ridge/Lasso). "
        "无监督学习包括聚类(K-Means/DBSCAN/层次聚类)和降维(PCA/t-SNE). "
        "强化学习包括Q-Learning/Policy Gradient/Actor-Critic. "
        "深度学习包括CNN/RNN/Transformer/GAN.",
    ),
    TestCase(
        11, "mermaid_arch_k8s", "mermaid", "flowchart (architecture)", "mermaid",
        "多subgraph, 共享资源, 跨域连接",
        "画一个Kubernetes部署架构图: "
        "Ingress Controller接收外部流量. "
        "两个Namespace: prod-ns(生产) 包含3个Deployment: API服务/Web前端/后台Worker; "
        "staging-ns(测试) 包含2个Deployment: API服务/Web前端. "
        "所有Deployment连接到共享的Redis缓存和PostgreSQL数据库. "
        "配置通过ConfigMap和Secret注入到各Deployment. "
        "用subgraph区分两个Namespace, 标注各组件角色.",
    ),
    TestCase(
        12, "vega_heatmap", "vega_lite", "heatmap", "vega_lite",
        "2D矩阵热力图 (需验证渲染支持)",
        "用以下数据画一个热力图, 展示某网站一周内不同时段的访问量: "
        "横轴: 周一~周日; 纵轴: 上午/下午/晚间/深夜. "
        "数据(访问量, 单位千次): "
        "周一: 上午85, 下午120, 晚间180, 深夜30; "
        "周二: 上午90, 下午130, 晚间175, 深夜25; "
        "周三: 上午88, 下午125, 晚间190, 深夜35; "
        "周四: 上午92, 下午140, 晚间200, 深夜40; "
        "周五: 上午95, 下午135, 晚间220, 深夜60; "
        "周六: 上午60, 下午90, 晚间250, 深夜90; "
        "周日: 上午55, 下午85, 晚间230, 深夜75. "
        "需要: 颜色深浅表示访问量大小, 有图例.",
    ),
]


# ---------------------------------------------------------------------------
# 上下文组装
# ---------------------------------------------------------------------------


def load_skill_l2() -> str:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    parts = re.split(r"^---\s*$", text, maxsplit=2, flags=re.MULTILINE)
    return parts[2].strip() if len(parts) >= 3 else text.strip()


def load_skill_l3(reference: str) -> str:
    path = SKILL_DIR / "references" / f"{reference}.md"
    if not path.exists():
        logger.warning("L3参考文件不存在: %s", path)
        return ""
    return path.read_text(encoding="utf-8").strip()


def build_system_prompt(tc: TestCase) -> str:
    l2 = load_skill_l2()
    l3 = load_skill_l3(tc.reference)

    tool_desc_map = {
        "mermaid": MERMAID_TOOL_DESC,
        "vega_lite": VEGA_TOOL_DESC,
        "markmap": MARKMAP_TOOL_DESC,
    }
    tools_section = "\n\n".join(tool_desc_map.values())

    engine_hint = {
        "mermaid": "mermaid 语法源码 (如 flowchart TD\\nA-->B)",
        "vega_lite": "Vega-Lite JSON spec (完整JSON对象)",
        "markmap": "Markdown 层级结构 (#/##/列表)",
    }

    return textwrap.dedent(f"""\
        你是图表制作助手. 用户会请你绘制各种图表, 你需要根据以下专业知识生成正确的图表源码.

        # 图表制作知识 (总览)

        {l2}

        # 详细参考文档 ({tc.reference})

        {l3}

        # 渲染工具说明

        {tools_section}

        # 输出要求

        请直接输出{engine_hint.get(tc.engine, "图表源码")}, 不要包含任何解释、说明或 markdown 代码块标记.
        只输出纯代码内容, 确保:
        1. 语法完全正确, 可直接被渲染引擎解析
        2. 标签清晰可读, 避免重叠或截断
        3. 数据完整准确, 图例/坐标轴/标题配置齐全""")


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------


async def generate_chart_code(
    model_id: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    llm = create_llm(model_id, temperature=0)
    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])
    return response.content if isinstance(response.content, str) else str(response.content)


def extract_code(raw: str, engine: str) -> str:
    stripped = raw.strip()
    patterns = [
        (r"```(?:mermaid|vega(?:-lite)?|json|markdown|md)?\s*\n(.*?)```", True),
    ]
    for pat, dotall in patterns:
        flags = re.DOTALL if dotall else 0
        m = re.search(pat, stripped, flags)
        if m:
            return m.group(1).strip()

    if stripped.startswith("```"):
        lines = stripped.split("\n")
        if len(lines) >= 2:
            return "\n".join(lines[1:]).rstrip("`").strip()

    return stripped


async def evaluate_chart_code(
    tc: TestCase,
    user_prompt: str,
    code: str,
) -> dict[str, Any]:
    llm = create_llm(EVALUATION_MODEL, temperature=0)

    eval_prompt = textwrap.dedent(f"""\
        请评估以下 {tc.engine} 图表代码的质量. 你是严格的质量审核员.

        ## 用户请求
        {user_prompt}

        ## 生成的代码
        ```
        {code}
        ```

        ## 评估维度 (每项1-10分)

        1. **数据准确性**: 用户请求中的所有数据是否完整、正确地在代码中呈现? 有无遗漏、篡改或计算错误?
        2. **结构规范性**: 图表类型是否匹配请求? 标签/图例/坐标轴/标题是否配置完整? 数据字段类型是否正确?
        3. **可读性预测**: 基于代码预测渲染后的视觉效果. 是否存在以下可预判的问题:
           - 标签文字重叠或被截断
           - 图表比例失调(过宽/过窄/过高)
           - 信息过于拥挤导致难以阅读
           - 颜色区分度不足
           - 坐标轴刻度不合理

        ## 输出格式 (严格JSON)

        ```json
        {{
          "data_accuracy": <1-10>,
          "structure": <1-10>,
          "readability": <1-10>,
          "verdict": "PASS",
          "fail_reasons": [],
          "notes": "简述评价"
        }}
        ```

        判定规则: 任一维度 < 7 则 verdict 改为 "FAIL", 并在 fail_reasons 数组中列出具体问题.""")

    try:
        response = await llm.ainvoke([
            SystemMessage(content="你是图表代码质量审核员, 严格评估, 输出JSON."),
            HumanMessage(content=eval_prompt),
        ])
        text = response.content if isinstance(response.content, str) else str(response.content)
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        return {"data_accuracy": 0, "structure": 0, "readability": 0, "verdict": "ERROR", "fail_reasons": ["无法解析评估结果"], "notes": text[:200]}
    except Exception as e:
        return {"data_accuracy": 0, "structure": 0, "readability": 0, "verdict": "ERROR", "fail_reasons": [str(e)], "notes": ""}


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


async def render_chart(
    client: httpx.AsyncClient,
    engine: str,
    code: str,
    title: str | None = None,
    scale: int = 3,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"engine": engine, "code": code, "scale": scale}
    if title:
        payload["title"] = title
    resp = await client.post(f"{TOOL_RUNTIME_URL}/render/chart", json=payload, timeout=120)
    return resp.json()


async def check_tool_runtime(client: httpx.AsyncClient) -> bool:
    try:
        resp = await client.get(f"{TOOL_RUNTIME_URL}/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


async def verify_heatmap_support(client: httpx.AsyncClient) -> bool:
    spec = json.dumps({
        "mark": {"type": "rect", "tooltip": True},
        "encoding": {
            "x": {"field": "day", "type": "ordinal"},
            "y": {"field": "time", "type": "ordinal"},
            "color": {"field": "val", "type": "quantitative"},
        },
        "data": {"values": [{"day": "A", "time": "M", "val": 10}, {"day": "B", "time": "N", "val": 20}]},
    })
    result = await render_chart(client, "vega_lite", spec)
    return result.get("success", False)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    tc: TestCase
    raw_output: str = ""
    extracted_code: str = ""
    render_result: dict[str, Any] = field(default_factory=dict)
    eval_result: dict[str, Any] = field(default_factory=dict)
    status: str = "PENDING"
    error: str = ""


async def run_case(
    client: httpx.AsyncClient,
    tc: TestCase,
    model_id: str,
    evaluate: bool,
    output_base: Path,
) -> CaseResult:
    result = CaseResult(tc=tc)
    case_dir = output_base / f"{tc.idx:02d}_{tc.name}"
    case_dir.mkdir(parents=True, exist_ok=True)

    logger.info("━" * 60)
    logger.info("用例 %02d: %s (%s)", tc.idx, tc.name, tc.difficulty)
    logger.info("━" * 60)

    # 1. 生成代码
    try:
        system_prompt = build_system_prompt(tc)
        logger.info("[1/4] 调用 LLM 生成代码...")
        t0 = time.time()
        raw = await generate_chart_code(model_id, system_prompt, tc.prompt)
        elapsed = time.time() - t0
        logger.info("      LLM 生成完成 (%.1fs, %d字符)", elapsed, len(raw))
        result.raw_output = raw
        (case_dir / "raw_output.txt").write_text(raw, encoding="utf-8")
    except Exception as e:
        result.status = "GENERATE_FAIL"
        result.error = str(e)
        logger.error("      LLM 生成失败: %s", e)
        (case_dir / "error.txt").write_text(str(e), encoding="utf-8")
        return result

    # 2. 提取代码
    result.extracted_code = extract_code(raw, tc.engine)
    (case_dir / "extracted_code.txt").write_text(result.extracted_code, encoding="utf-8")
    logger.info("[2/4] 代码提取完成 (%d字符)", len(result.extracted_code))

    # 3. 渲染
    try:
        logger.info("[3/4] 渲染 PNG...")
        render_resp = await render_chart(client, tc.engine, result.extracted_code, title=tc.chart_type)
        result.render_result = render_resp
        if render_resp.get("success"):
            png_b64 = render_resp.get("content_b64", "")
            if png_b64:
                png_path = case_dir / "rendered.png"
                png_path.write_bytes(base64.b64decode(png_b64))
                logger.info("      渲染成功 (%d bytes)", render_resp.get("size_bytes", 0))
            else:
                result.status = "RENDER_FAIL"
                result.error = "渲染成功但无图片数据"
                logger.error("      %s", result.error)
        else:
            result.status = "RENDER_FAIL"
            result.error = render_resp.get("error", "未知渲染错误")
            logger.error("      渲染失败: %s", result.error)
            (case_dir / "render_error.txt").write_text(result.error, encoding="utf-8")
    except Exception as e:
        result.status = "RENDER_FAIL"
        result.error = str(e)
        logger.error("      渲染异常: %s", e)

    # 4. Doubao 评估
    if evaluate:
        try:
            logger.info("[4/4] Doubao 代码评审...")
            result.eval_result = await evaluate_chart_code(tc, tc.prompt, result.extracted_code)
            (case_dir / "eval.json").write_text(
                json.dumps(result.eval_result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            verdict = result.eval_result.get("verdict", "?")
            scores = [
                result.eval_result.get("data_accuracy", 0),
                result.eval_result.get("structure", 0),
                result.eval_result.get("readability", 0),
            ]
            logger.info("      评审: %s (数据%d/结构%d/可读%d)", verdict, *scores)
        except Exception as e:
            logger.error("      评审失败: %s", e)
            result.eval_result = {"verdict": "ERROR", "fail_reasons": [str(e)]}

    # 综合状态
    if result.status == "PENDING":
        if result.render_result.get("success"):
            if evaluate:
                verdict = result.eval_result.get("verdict", "")
                if verdict == "PASS":
                    result.status = "PASS"
                elif verdict == "FAIL":
                    result.status = "FLAG"
                else:
                    result.status = "EVAL_ERROR"
            else:
                result.status = "PASS"
        else:
            result.status = "RENDER_FAIL"

    (case_dir / "status.txt").write_text(result.status, encoding="utf-8")
    logger.info("      → %s", result.status)
    return result


def generate_summary(results: list[CaseResult], output_base: Path) -> None:
    lines = ["# chart_maker Skill 验证报告", ""]
    lines.append("| # | 用例 | 引擎 | 状态 | 数据 | 结构 | 可读 | 说明 |")
    lines.append("|---|------|------|------|------|------|------|------|")

    pass_count = flag_count = fail_count = 0

    for r in results:
        tc = r.tc
        status_emoji = {"PASS": "✅", "FLAG": "⚠️", "RENDER_FAIL": "❌", "GENERATE_FAIL": "💥"}.get(r.status, "❓")

        ev = r.eval_result
        da = ev.get("data_accuracy", "-")
        st = ev.get("structure", "-")
        rd = ev.get("readability", "-")

        note = ""
        if r.status == "RENDER_FAIL":
            note = r.error[:60]
        elif r.status == "FLAG":
            reasons = ev.get("fail_reasons", [])
            note = "; ".join(reasons[:2]) if reasons else ev.get("notes", "")[:60]
        elif r.status == "PASS":
            note = ev.get("notes", "")[:60]

        lines.append(f"| {tc.idx} | {tc.name} | {tc.engine} | {status_emoji} {r.status} | {da} | {st} | {rd} | {note} |")

        if r.status == "PASS":
            pass_count += 1
        elif r.status == "FLAG":
            flag_count += 1
        else:
            fail_count += 1

    lines.append("")
    lines.append(f"**总计**: {pass_count} PASS / {flag_count} FLAG / {fail_count} FAIL (共{len(results)}个)")
    lines.append("")

    action_items = [r for r in results if r.status in ("FLAG", "RENDER_FAIL")]
    if action_items:
        lines.append("## 待打磨清单")
        lines.append("")
        for r in action_items:
            tc = r.tc
            lines.append(f"### 用例 {tc.idx}: {tc.name} ({tc.engine})")
            lines.append(f"- 难度: {tc.difficulty}")
            if r.status == "RENDER_FAIL":
                lines.append(f"- 渲染错误: {r.error}")
                lines.append("- 行动: 修正 references 中导致渲染失败的语法/示例")
            elif r.status == "FLAG":
                reasons = r.eval_result.get("fail_reasons", [])
                for reason in reasons:
                    lines.append(f"- {reason}")
                lines.append("- 行动: 补充 L3 references 中缺失的指导")
            lines.append("")

    (output_base / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    logger.info("汇总报告: %s", output_base / "summary.md")


async def main() -> None:
    parser = argparse.ArgumentParser(description="chart_maker skill 端到端验证")
    parser.add_argument("--cases", type=str, default="", help="指定用例编号(逗号分隔), 如 1,5,10")
    parser.add_argument("--model", type=str, default=GENERATION_MODEL_DEFAULT, help="生成模型ID")
    parser.add_argument("--no-evaluate", action="store_true", help="跳过 Doubao 评估")
    parser.add_argument("--output", type=str, default=str(OUTPUT_DIR), help="输出目录")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    output_base = Path(args.output)
    output_base.mkdir(parents=True, exist_ok=True)

    # 选择测试用例
    cases = TEST_CASES
    if args.cases:
        idxs = {int(x.strip()) for x in args.cases.split(",") if x.strip()}
        cases = [tc for tc in TEST_CASES if tc.idx in idxs]

    async with httpx.AsyncClient() as client:
        # 前置检查
        logger.info("前置检查: tool-runtime 健康...")
        if not await check_tool_runtime(client):
            logger.error("tool-runtime 不可用 (%s), 请先启动容器", TOOL_RUNTIME_URL)
            sys.exit(1)
        logger.info("  ✅ tool-runtime 正常")

        # heatmap 支持验证
        if any(tc.idx == 12 for tc in cases):
            logger.info("前置检查: heatmap 渲染支持...")
            if await verify_heatmap_support(client):
                logger.info("  ✅ heatmap 可用")
            else:
                logger.warning("  ⚠️ heatmap 不支持, 跳过用例12")
                cases = [tc for tc in cases if tc.idx != 12]

        logger.info("\n开始验证: %d 个用例, 模型=%s, 评估=%s\n",
                     len(cases), args.model, "禁用" if args.no_evaluate else "启用")

        results: list[CaseResult] = []
        for tc in cases:
            result = await run_case(client, tc, args.model, not args.no_evaluate, output_base)
            results.append(result)

        generate_summary(results, output_base)

        pass_n = sum(1 for r in results if r.status == "PASS")
        flag_n = sum(1 for r in results if r.status == "FLAG")
        fail_n = sum(1 for r in results if r.status in ("RENDER_FAIL", "GENERATE_FAIL"))

        logger.info("\n" + "=" * 60)
        logger.info("验证完成: %d PASS / %d FLAG / %d FAIL", pass_n, flag_n, fail_n)
        logger.info("报告: %s/summary.md", output_base)
        logger.info("PNG目录: %s/", output_base)
        logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
