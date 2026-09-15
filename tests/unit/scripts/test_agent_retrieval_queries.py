"""agent_retrieval 基准数据完整性测试."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.benchmarks.agent_retrieval.queries import GROUP_NAMES, QUERIES

_SNAPSHOT = (
    Path(__file__).parents[3]
    / "scripts/benchmarks/agent_retrieval/catalog_snapshot.json"
)


class TestQuerySetIntegrity:
    """query 集与 catalog 快照的一致性约束."""

    def test_query_总数与唯一性(self) -> None:
        queries = [q for q, _, _ in QUERIES]
        assert len(QUERIES) >= 60
        assert len(set(queries)) == len(queries), "query 重复"

    def test_金标全部在_catalog_快照内(self) -> None:
        catalog = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
        for query, gold, _scene in QUERIES:
            assert gold, f"空金标: {query}"
            for g in gold:
                assert g in catalog, f"金标 {g} 不在快照: {query}"

    def test_生产实测原句占比(self) -> None:
        real = [s for _, _, s in QUERIES if s.startswith("real")]
        assert len(real) >= 20, f"生产实测原句仅 {len(real)} 条, 应 >=20"

    def test_金标不混用组名与成员名(self) -> None:
        member_names = {
            "schedule_message_wechat",
            "schedule_message_email",
            "list_scheduled_messages",
            "cancel_scheduled_message",
            "query_stock_price",
            "create_price_alert",
            "list_price_alerts",
            "cancel_price_alert",
        }
        for query, gold, _ in QUERIES:
            assert not (set(gold) & member_names), (
                f"金标用了成员名 (应为组名): {query} -> {gold}"
            )

    def test_组名金标合法(self) -> None:
        for query, gold, _ in QUERIES:
            for g in gold:
                if g.endswith("_group"):
                    assert g in GROUP_NAMES, f"未知组名: {g}"

    def test_重合虚构工具已移除(self) -> None:
        """职责唯一性: 可被真实工具完全取代的虚构工具不得存在于目录."""
        catalog = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
        removed = {"map_navigation", "code_executor", "image_generator", "email_sender"}
        assert not (removed & set(catalog)), "重合虚构工具残留"

    def test_新动作与数据工具在快照内(self) -> None:
        catalog = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
        expected = {
            "flight_booking",
            "hotel_booking",
            "ride_hailing",
            "hospital_appointment",
            "recipe_db",
            "legal_reference",
        }
        assert expected <= set(catalog), f"缺失: {expected - set(catalog)}"

    def test_web_search已移除仅保留web_research(self) -> None:
        """快搜失真条目已删 (生产主 agent 不暴露独立 web_search,
        doubao_search 仅是 web_research 专家内部实现), 目录只留 web_research."""
        catalog = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
        assert "web_search" not in catalog
        assert "深度研究" in catalog["web_research"]["description"]

    def test_组在候选中是单条目而非展开成员(self) -> None:
        """生产语义: LLM 降噪看到的是组条目 (组名+summary), 成员展开发生在过滤之后."""
        import sys

        sys.path.insert(0, ".")
        from scripts.benchmarks.agent_retrieval.runner import (
            _build_candidates,
            _load_search_tool,
        )

        tool = _load_search_tool()
        cands = _build_candidates(tool, "股票 行情 市盈率 股价监控 价格提醒")
        names = [c["name"] for c in cands]
        assert "stock_watch_group" in names, f"组未被召回: {names}"
        group_entry = next(c for c in cands if c["name"] == "stock_watch_group")
        # 组条目呈现组 summary, 不展开成员 (成员仅在 _members 字段, 不进 LLM 渲染)
        # 双侧精确化: 实时股价 (stock_watch) vs 历史行情 (finance_data)
        assert "实时股价" in group_entry["description"]
        member_names = {"query_stock_price", "create_price_alert"}
        assert not (member_names & set(names)), "组成员被展开进候选"
