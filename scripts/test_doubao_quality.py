#!/usr/bin/env python3
"""豆包 Seed 2.0 Pro reasoning_effort 质量对比测试.

保存每个用例在不同 reasoning_effort 下的完整回复, 用于人工对比质量.
固定 temp=0.3, top_p=0.95, max_tokens=16384.

用法: python scripts/test_doubao_quality.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
MODEL = "doubao-seed-2-0-pro-260215"
API_KEY = os.getenv("ARK_API_KEY", "")

if not API_KEY:
    print("错误: ARK_API_KEY 未设置")
    sys.exit(1)

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

EFFORTS = ["minimal", "medium", "high"]

SYSTEM_PROMPT = "你是一个个人助手, 回答要友好、准确、有条理。用中文回答。"


def call_and_save(client: OpenAI, prompt: str, effort: str) -> dict:
    start = time.time()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        top_p=0.95,
        max_tokens=16384,
        extra_body={"reasoning_effort": effort},
    )
    elapsed = time.time() - start
    choice = response.choices[0]
    usage = response.usage
    think_tokens = 0
    if usage and usage.completion_tokens_details:
        think_tokens = getattr(usage.completion_tokens_details, "reasoning_tokens", 0) or 0

    return {
        "elapsed_s": round(elapsed, 2),
        "content": choice.message.content or "",
        "reasoning_content": getattr(choice.message, "reasoning_content", None) or "",
        "input_tokens": usage.prompt_tokens if usage else 0,
        "output_tokens": usage.completion_tokens if usage else 0,
        "reasoning_tokens": think_tokens,
        "finish_reason": choice.finish_reason,
    }


def main():
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    results = {}

    for effort in EFFORTS:
        print(f"\n{'=' * 60}")
        print(f"reasoning_effort = {effort}")
        print(f"{'=' * 60}")
        results[effort] = {}

        for case in TEST_PROMPTS:
            name = case["name"]
            print(f"  [{name}] ...", end="", flush=True)
            r = call_and_save(client, case["prompt"], effort)
            results[effort][name] = r
            print(
                f" {r['elapsed_s']}s | "
                f"in={r['input_tokens']} out={r['output_tokens']} "
                f"think={r['reasoning_tokens']} | {r['finish_reason']}"
            )
            time.sleep(1)

    # ── 输出到文件 ──────────────────────────────────────────────────────
    output_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "doubao_quality_comparison.json")

    # 也生成可读的 markdown
    md_path = os.path.join(output_dir, "doubao_quality_comparison.md")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 生成 markdown 对比文档
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# 豆包 Seed 2.0 Pro reasoning_effort 质量对比\n\n")
        f.write(f"模型: {MODEL} | temp=0.3 | top_p=0.95 | max_tokens=16384\n\n")
        f.write("---\n\n")

        for case in TEST_PROMPTS:
            name = case["name"]
            f.write(f"## {name}\n\n")
            f.write(f"**用户**: {case['prompt']}\n\n")

            # 指标对比表
            f.write("| 指标 |")
            for effort in EFFORTS:
                f.write(f" R={effort} |")
            f.write("\n|---|")
            for _ in EFFORTS:
                f.write("---|")
            f.write("\n")

            r_rows = [
                ("耗时", lambda r: f"{r['elapsed_s']}s"),
                ("输入Tokens", lambda r: str(r["input_tokens"])),
                ("输出Tokens", lambda r: str(r["output_tokens"])),
                ("思维链Tokens", lambda r: str(r["reasoning_tokens"])),
                ("完成原因", lambda r: r["finish_reason"]),
            ]
            for row_name, row_fn in r_rows:
                f.write(f"| {row_name} |")
                for effort in EFFORTS:
                    f.write(f" {row_fn(results[effort][name])} |")
                f.write("\n")
            f.write("\n")

            # 逐个回复
            for effort in EFFORTS:
                r = results[effort][name]
                f.write(f"### R={effort} 的回复\n\n")
                f.write(f">>>\n{r['content']}\n<<<\n\n")
                if r["reasoning_content"]:
                    f.write(f"<details>\n<summary>思维链 ({r['reasoning_tokens']} tokens)</summary>\n\n")
                    f.write(f"{r['reasoning_content']}\n\n")
                    f.write("</details>\n\n")
                f.write("---\n\n")

    print("\n✅ 结果已保存:")
    print(f"   JSON: {output_path}")
    print(f"   Markdown: {md_path}")


if __name__ == "__main__":
    main()
