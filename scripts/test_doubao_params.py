#!/usr/bin/env python3
"""豆包 Seed 2.0 Pro 主对话模型参数对比测试.

使用真实用户对话数据 (alice/bob) 提炼的测试用例, 测试不同参数组合在个人助手
主对话场景下的延迟、Token 消耗、输出长度.

用法: python scripts/test_doubao_params.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

# ── 配置 ──────────────────────────────────────────────────────────────────
BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
MODEL = "doubao-seed-2-0-pro-260215"
API_KEY = os.getenv("ARK_API_KEY", "")

if not API_KEY:
    print("错误: ARK_API_KEY 未设置, 请检查 .env 文件")
    sys.exit(1)

# ── 真实用户对话提炼的测试用例 ────────────────────────────────────────────
TEST_PROMPTS = [
    {
        "name": "TODO记录(短)",
        "prompt": "记录一个小待办，研究并实现rime跨平台本地云同步",
    },
    {
        "name": "生活规划(中)",
        "prompt": (
            "现在行程确认了。接下来该帮我想想有什么出门要带齐全的东西。"
            "先说目的，我这次去，是拜访朋友小住几天。"
            "帮他去调整一下电脑设备，然后聊一聊项目。剩下的时间自由"
        ),
    },
    {
        "name": "知识介绍(中)",
        "prompt": "DaVinci Resolve 我完全不熟悉，先给我介绍一下",
    },
    {
        "name": "工具设计(长)",
        "prompt": (
            "我遇到了一些算法设计方面的问题。我当前不是在给一个agent设计工具吗，"
            "因为通过langchain注入的工具会注入完整的描述参数等信息，"
            "然后我就通过中间件设计了动态发现。原理是agent自己通过一个搜索工具去查询，"
            "命中查询之后的对应工具就会被注入下一轮对话。"
            "但是我在搜索算法这里遇到了问题。棘手的点在于，"
            "llm的查询，有随机性，优根据用户语音，多语言输入，"
            "还会使用同义词和目的查询，导致经常一次性命中一大片工具，"
            "动态注入的效果大打折扣。"
        ),
    },
    {
        "name": "Agent SOP设计(长)",
        "prompt": (
            "我在设计subagent（也就是专家工具）的时候遇到了问题。"
            "我不知道该问如何给一个geo的agent制定sop，制定行为边界。"
            "当前是简单提示词+高思考预算+不限制llm调用轮次，让模型自由发挥。"
            "这样已经验证了llm的实力足够完成任务，"
            "但是任务的消耗时间以及稳定性肯定不是很令人满意。"
            "给我一点思路，关于：一个geo agent拿到了一个请求、"
            "以及一份由google Gemini的lite模型生成的查了地图数据的回复之后。"
        ),
    },
    {
        "name": "行业分析(长)",
        "prompt": (
            "我在想，为什么目前，都是本来应该专注做模型，提升模型能力的公司，"
            "在做agent工程，在做提示词工程？除了像DeepSeek这种规模比较小的。"
            "而且从商业上来看，是正确的，你看阿里基础模型做的不错，"
            "接入模型的app稀烂，没人用，豆包功能完善易用，市场占有极高。"
            "本来优化提示词，研究工程把AI落地应该是各行各业的专家自己的事情，"
            "但是现在怎么好像OpenAI anthropic这种公司要大包大揽的样子？"
        ),
    },
]

# ── 参数组合: temperature / top_p / max_tokens ────────────────────────────
# reasoning_effort 均不传 (使用 API 默认深度思考行为)
PARAM_COMBOS = [
    {
        "label": "A: 官方默认(temp=1.0, top_p=0.5, max=4K)",
        "params": {"temperature": 1.0, "top_p": 0.5, "max_tokens": 4096},
    },
    {
        "label": "B: 项目默认(temp=0.3, top_p=0.95, max=4K)",
        "params": {"temperature": 0.3, "top_p": 0.95, "max_tokens": 4096},
    },
    {
        "label": "C: 官方默认(temp=1.0, top_p=0.5, max=16K)",
        "params": {"temperature": 1.0, "top_p": 0.5, "max_tokens": 16384},
    },
    {
        "label": "D: 项目默认(temp=0.3, top_p=0.95, max=16K)",
        "params": {"temperature": 0.3, "top_p": 0.95, "max_tokens": 16384},
    },
]

# ── reasoning_effort 对比 (固定 temp=0.3, top_p=0.95, max_tokens=16384) ──
REASONING_COMBOS = [
    {
        "label": "E: R=minimal",
        "params": {
            "temperature": 0.3,
            "top_p": 0.95,
            "max_tokens": 16384,
            "reasoning_effort": "minimal",
        },
    },
    {
        "label": "F: R=low",
        "params": {
            "temperature": 0.3,
            "top_p": 0.95,
            "max_tokens": 16384,
            "reasoning_effort": "low",
        },
    },
    {
        "label": "G: R=medium",
        "params": {
            "temperature": 0.3,
            "top_p": 0.95,
            "max_tokens": 16384,
            "reasoning_effort": "medium",
        },
    },
    {
        "label": "H: R=high",
        "params": {
            "temperature": 0.3,
            "top_p": 0.95,
            "max_tokens": 16384,
            "reasoning_effort": "high",
        },
    },
]


def call_doubao(client: OpenAI, prompt: str, params: dict) -> dict:
    """调用豆包 API 并返回结果指标."""
    start = time.time()

    request_params = dict(params)
    # reasoning_effort 通过 extra_body 传入 (火山方舟 OpenAI 兼容接口)
    reasoning = request_params.pop("reasoning_effort", None)
    extra_body = {}
    if reasoning:
        extra_body["reasoning_effort"] = reasoning

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "你是一个个人助手, 回答要友好、准确、有条理。用中文回答。",
            },
            {"role": "user", "content": prompt},
        ],
        **request_params,
        **({"extra_body": extra_body} if extra_body else {}),
    )

    elapsed = time.time() - start
    choice = response.choices[0]
    content = choice.message.content or ""
    reasoning_content = getattr(choice.message, "reasoning_content", None)

    usage = response.usage
    think_tokens = 0
    if usage and usage.completion_tokens_details:
        think_tokens = getattr(
            usage.completion_tokens_details, "reasoning_tokens", 0
        ) or 0

    return {
        "elapsed_s": round(elapsed, 2),
        "content_chars": len(content),
        "input_tokens": usage.prompt_tokens if usage else 0,
        "output_tokens": usage.completion_tokens if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
        "reasoning_tokens": think_tokens,
        "reasoning_chars": len(reasoning_content) if reasoning_content else 0,
        "finish_reason": choice.finish_reason,
    }


def run_suite(client: OpenAI, combos: list[dict], label_prefix: str):
    """运行一组参数组合测试."""
    all_results = {}

    for combo in combos:
        print(f"\n┌─ {combo['label']}")
        print(f"│  参数: {json.dumps(combo['params'], ensure_ascii=False)}")
        results = []

        for case in TEST_PROMPTS:
            tag = case["name"]
            print(f"│  [{tag}] ...", end="", flush=True)
            try:
                r = call_doubao(client, case["prompt"], combo["params"])
                r["case"] = tag
                results.append(r)
                print(
                    f" {r['elapsed_s']:>5.1f}s | "
                    f"in={r['input_tokens']:>5} out={r['output_tokens']:>5} "
                    f"think={r['reasoning_tokens']:>5} | "
                    f"{r['content_chars']:>4}字 | {r['finish_reason']}"
                )
            except Exception as e:
                print(f" 错误: {e}")
                results.append({"case": tag, "error": str(e)})
            time.sleep(1)

        all_results[combo["label"]] = results

        # 汇总
        ok = [r for r in results if "error" not in r]
        if ok:
            avg_t = sum(r["elapsed_s"] for r in ok) / len(ok)
            tot_in = sum(r["input_tokens"] for r in ok)
            tot_out = sum(r["output_tokens"] for r in ok)
            tot_thk = sum(r["reasoning_tokens"] for r in ok)
            avg_c = sum(r["content_chars"] for r in ok) / len(ok)
            finish = [r["finish_reason"] for r in ok]
            print(
                f"│  ── 平均{avg_t:.1f}s | "
                f"total: {tot_in}+{tot_out}(+think:{tot_thk}) | "
                f"avg {avg_c:.0f}字 | finish: {finish}"
            )
        else:
            print("│  ── 全部失败!")
        print("└─")

    return all_results


def print_comparison_table(all_results: dict):
    """打印最终横向对比表."""
    print("\n" + "=" * 80)
    print("📊 横向对比表")
    print("=" * 80)

    header = (
        f"{'组合':<42} {'平均耗时':>8} {'总Tokens':>10} "
        f"{'思维链':>8} {'平均字数':>8} {'完成状态':>12}"
    )
    print(header)
    print("-" * len(header))

    for label, results in all_results.items():
        ok = [r for r in results if "error" not in r]
        if not ok:
            print(f"{label:<42} {'失败':>8}")
            continue
        avg_time = sum(r["elapsed_s"] for r in ok) / len(ok)
        total = sum(r["total_tokens"] for r in ok)
        think = sum(r["reasoning_tokens"] for r in ok)
        avg_chars = sum(r["content_chars"] for r in ok) / len(ok)
        finish = ok[0]["finish_reason"]
        truncated = sum(1 for r in ok if r["finish_reason"] == "length")
        note = f"{finish}" + (f" ({truncated}截断)" if truncated else "")
        print(
            f"{label:<42} {avg_time:>7.1f}s {total:>10} "
            f"{think:>8} {avg_chars:>7.0f}字 {note:>12}"
        )

    print("=" * 80)

    # 每个 case 的详细对比
    print("\n📋 各用例详情 (每个用例在全部组合下的字数)")
    print("-" * 80)
    case_names = [c["name"] for c in TEST_PROMPTS]

    # 表头
    print(f"{'用例':<20}", end="")
    for label in all_results:
        short = label.split(":")[0] if ":" in label else label[:8]
        print(f" {short:>8}", end="")
    print()
    print("-" * 80)

    for case_name in case_names:
        print(f"{case_name:<20}", end="")
        for label, results in all_results.items():
            r = next((r for r in results if r.get("case") == case_name), None)
            if r and "error" not in r:
                print(f" {r['content_chars']:>8}", end="")
            else:
                print(f" {'ERR':>8}", end="")
        print()
    print("-" * 80)


def main():
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    print("=" * 80)
    print("豆包 Seed 2.0 Pro 参数对比测试 (真实用户对话用例)")
    print(f"模型: {MODEL}")
    print(f"用例: {len(TEST_PROMPTS)} 个")
    for c in TEST_PROMPTS:
        print(f"  - {c['name']}: {c['prompt'][:50]}...")
    print("=" * 80)

    # Part 1: temperature / top_p / max_tokens
    print("\n📌 Part 1: temperature / top_p / max_tokens")
    print("   (reasoning_effort 均不传, API 默认开启深度思考)\n")

    results_p1 = run_suite(client, PARAM_COMBOS, "P1")

    # Part 2: reasoning_effort
    print("\n\n📌 Part 2: reasoning_effort 对比")
    print("   (固定 temp=0.3, top_p=0.95, max_tokens=16384)\n")

    results_p2 = run_suite(client, REASONING_COMBOS, "P2")

    # 合并出表
    all_results = {**results_p1, **results_p2}
    print_comparison_table(all_results)


if __name__ == "__main__":
    main()
