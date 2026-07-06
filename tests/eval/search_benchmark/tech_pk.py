"""豆包 vs 智谱 技术搜索 PK (智谱传统强项主场).

10 条技术 query (官方文档/具体库/报错/前沿框架/DB/CLI/算法/DevOps/架构/小众深度),
每条结果打信源类型标签, 量化两家的数据源覆盖范围.

用法: python tests/eval/search_benchmark/tech_pk.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_benchmark import search_doubao, search_zhipu

RES = Path(__file__).parent / "results"

# 信源类型分类规则
OFFICIAL = (
    "python.org", "fastapi.tiangolo.com", "docs.pydantic.dev", "doc.rust-lang.org",
    "postgresql.org", "docs.docker.com", "grpc.io", "langchain.com",
    "langchain-ai.github.io", "react.dev", "vuejs.org", "go.dev", "rust-lang.org",
    "nodejs.org", "docker.com", "kubernetes.io", "redis.io", "nginx.org",
)
EN_AUTHORITY = (
    "stackoverflow.com", "github.com", "medium.com", "dev.to", "realpython.com",
    "geeksforgeeks.org", "mozilla.org", "w3schools.com", "tutorialspoint.com",
    "digitalocean.com", "baeldung.com", "towardsdatascience.com", "stackoverflow",
)
CN_COMMUNITY = (
    "juejin.cn", "csdn.net", "zhihu.com", "cnblogs.com", "segmentfault.com",
    "51cto.com", "jianshu.com", "oschina.net", "iteye.com", "php.cn",
)
CN_VENDOR = (
    "tencent.com", "cloud.tencent.com", "aliyun.com", "help.aliyun.com",
    "huaweicloud.com", "baidu.com", "cloud.baidu.com",
)


def classify(url: str, domain: str) -> str:
    d = domain.lower()
    u = url.lower()
    if any(o in d or o in u for o in OFFICIAL):
        return "官方文档"
    if any(e in d or e in u for e in EN_AUTHORITY):
        return "英文权威"
    if any(c in d for c in CN_COMMUNITY):
        return "中文社区"
    if any(v in d for v in CN_VENDOR):
        return "厂商云"
    if domain == "" or domain == "?":
        return "无URL"
    return "其他"


TECH_QUERIES = [
    {"id": "t01_official", "query": "FastAPI Depends 依赖注入 用法 官方文档", "note": "英文官方文档命中"},
    {"id": "t02_lib", "query": "Pydantic v2 BaseModel model_config 配置", "note": "具体库版本"},
    {"id": "t03_debug", "query": "Python ModuleNotFoundError No module named 解决方法", "note": "报错排查"},
    {"id": "t04_framework", "query": "LangGraph StateGraph 状态图 工作流 编排", "note": "前沿框架"},
    {"id": "t05_db", "query": "PostgreSQL 索引优化 EXPLAIN ANALYZE 慢查询", "note": "DB深度"},
    {"id": "t06_cli", "query": "uv pip compile requirements lock 锁文件", "note": "CLI工具"},
    {"id": "t07_algo", "query": "红黑树 插入 平衡 旋转 算法实现", "note": "算法/CS"},
    {"id": "t08_devops", "query": "Docker multi-stage build 多阶段构建 减小镜像", "note": "DevOps"},
    {"id": "t09_api", "query": "gRPC REST 性能对比 protobuf 序列化", "note": "架构对比"},
    {"id": "t10_niche", "query": "Rust 所有权 借用检查器 生命周期 lifetime", "note": "小众深度"},
]


def tag_results(results: list) -> list:
    for it in results:
        it["source_type"] = classify(it.get("url", ""), it.get("domain", ""))
    return results


async def main() -> None:
    out: list[str] = ["# 豆包 vs 智谱 技术搜索 PK\n"]
    # 信源类型累计
    agg = {"doubao": {}, "zhipu": {}}
    no_url = {"doubao": 0, "zhipu": 0}

    for q in TECH_QUERIES:
        out.append(f"\n## [{q['id']}] {q['note']}\n**Q:** {q['query']}\n")
        rec: dict = {"query": q["query"]}
        for name, fn in (("doubao", search_doubao), ("zhipu", search_zhipu)):
            try:
                r = await fn(q["query"])
            except Exception as e:
                r = {"error": str(e), "results": []}
            r["results"] = tag_results(r.get("results", []))
            rec[name] = r
            for it in r["results"]:
                st = it["source_type"]
                agg[name][st] = agg[name].get(st, 0) + 1
                if st == "无URL":
                    no_url[name] += 1

        out.append("| # | doubao | 源类型 | zhipu | 源类型 |")
        out.append("|---|--------|--------|-------|--------|")
        d_res = rec["doubao"].get("results", [])
        z_res = rec["zhipu"].get("results", [])
        if "error" in rec["doubao"]:
            out.append(f"| - | ❌{rec['doubao']['error'][:30]} | | | |")
        if "error" in rec["zhipu"]:
            out.append(f"| - | | | ❌{rec['zhipu']['error'][:30]} | |")
        for i in range(max(len(d_res), len(z_res))):
            d = d_res[i] if i < len(d_res) else None
            z = z_res[i] if i < len(z_res) else None
            dt = f"{(d.get('domain') or '?')}·{d.get('source_type')}" if d else ""
            zt = f"{(z.get('domain') or '?')}·{z.get('source_type')}" if z else ""
            out.append(f"| {i + 1} | {dt} | | {zt} |")
        out.append("")
        (RES / f"{q['id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2))

    # 汇总信源覆盖
    out.append("\n## 信源类型覆盖汇总\n")
    out.append("| 源类型 | doubao | zhipu |")
    out.append("|--------|--------|-------|")
    keys = sorted(set(agg["doubao"]) | set(agg["zhipu"]))
    for k in keys:
        out.append(f"| {k} | {agg['doubao'].get(k, 0)} | {agg['zhipu'].get(k, 0)} |")
    out.append(f"| **无URL占比** | {no_url['doubao']} | {no_url['zhipu']} |")

    report = Path(__file__).parent / "tech_pk.md"
    report.write_text("\n".join(out))
    # 同时打印汇总到 stdout
    print("\n=== 信源类型覆盖汇总 ===")
    print(f"{'源类型':<10} {'doubao':>8} {'zhipu':>8}")
    for k in keys:
        print(f"{k:<10} {agg['doubao'].get(k, 0):>8} {agg['zhipu'].get(k, 0):>8}")
    print(f"{'无URL':<10} {no_url['doubao']:>8} {no_url['zhipu']:>8}")
    print(f"\n报告: {report}")


if __name__ == "__main__":
    asyncio.run(main())
