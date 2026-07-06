"""健康数据提取服务 - 协调数据提取和存储.

职责:
1. 接收 UnifiedHealthExtractor 的提取结果
2. 通过 AsyncHealthDAO 将数据持久化到用户隔离的 health_data.db
3. 返回友好的中文确认信息
4. 支持审计场景: 快照读取 + CUD操作

不再负责:
- 数据类型检测 (由 UnifiedHealthExtractor 一次完成)
- LLM 调用 (由 UnifiedHealthExtractor 负责)
- 营养推断 (系统不再推断任何营养数据)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, ClassVar

from src.storage.dao.async_database_manager import create_async_health_data_db_manager
from src.storage.dao.async_health_dao import AsyncHealthDAO

logger = logging.getLogger(__name__)

_DATA_TYPE_LABELS: dict[str, str] = {
    "weight_record": "体重记录",
    "meal_record": "饮食记录",
    "workout_record": "运动记录",
    "shopping_list": "购物清单",
    "food_product": "食品包装目录",
    "medical_report": "体检报告",
}


class HealthDataExtractionService:
    """健康数据提取服务 - 只负责存储."""

    def __init__(self, user_id: str, thread_id: str, *, agent_id: str) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_id = agent_id
        self._dao: AsyncHealthDAO | None = None
        self.logger = logging.getLogger(f"{__name__}.HealthDataExtractionService")

    async def _get_dao(self) -> AsyncHealthDAO:
        """获取 AsyncHealthDAO 实例(延迟初始化)."""
        if self._dao is None:
            db_manager = await create_async_health_data_db_manager(
                self.user_id,
                self.thread_id,
                agent_id=self.agent_id,
            )
            self._dao = AsyncHealthDAO(db_manager.session_factory)
        return self._dao

    async def store_extraction(
        self,
        data_type: str,
        data: Any,
        round_number: int | None = None,
    ) -> dict[str, Any]:
        """存储单条提取结果.

        Args:
            data_type: 数据类型
            data: 提取的结构化数据
            round_number: 来源对话轮次

        Returns:
            存储结果

        """
        try:
            data = self._preprocess(data_type, data)
            dao = await self._get_dao()
            store_result = await self._store_extracted_data(
                dao,
                data_type,
                data,
                round_number,
            )
            self.logger.info("数据存储成功: type=%s", data_type)
            return {
                "success": True,
                "data_type": data_type,
                "data_type_label": _DATA_TYPE_LABELS.get(data_type, data_type),
                "storage_result": store_result,
                "message": f"已记录{_DATA_TYPE_LABELS.get(data_type, data_type)}",
            }
        except Exception as e:
            self.logger.error("数据存储失败: %s", e)
            return {
                "success": False,
                "error": str(e),
                "data_type": data_type,
            }

    def _preprocess(self, data_type: str, data: Any) -> Any:
        """预处理数据 - food_product 的 kJ→kcal 机械转换."""
        if data_type == "food_product":
            nutrition = data.get("nutrition_per_100g")
            if isinstance(nutrition, dict):
                energy_kj = nutrition.get("energy_kj")
                calories = nutrition.get("calories")
                if energy_kj and not calories:
                    nutrition["calories"] = round(energy_kj / 4.184, 1)
                elif calories and not energy_kj:
                    nutrition["energy_kj"] = round(calories * 4.184, 1)
        return data

    async def _store_extracted_data(
        self,
        dao: AsyncHealthDAO,
        data_type: str,
        data: Any,
        round_number: int | None = None,
    ) -> dict[str, Any]:
        """将提取的数据存储到数据库."""
        now = datetime.now()

        if data_type == "weight_record":
            record_data = {
                "weight_kg": data["weight"],
                "body_fat_pct": data.get("body_fat_percentage"),
                "muscle_mass_kg": data.get("muscle_mass"),
                "recorded_at": (
                    datetime.fromisoformat(data["timestamp"])
                    if data.get("timestamp")
                    else None
                ),
                "round_number": round_number,
                "source": "conversation_extraction",
            }
            weight_record = await dao.create_weight_record(record_data)
            return {"id": weight_record.id, "weight": weight_record.weight_kg}

        if data_type == "workout_record":
            record_data = {
                "workout_type": data["workout_type"],
                "duration": data.get("duration_minutes", data.get("duration")),
                "distance": data.get("distance_km", data.get("distance")),
                "calories": data.get("calories"),
                "start_time": (
                    datetime.fromisoformat(data["start_time"])
                    if data.get("start_time")
                    else now
                ),
                "round_number": round_number,
                "source": "conversation_extraction",
            }
            workout = await dao.create_workout_record(record_data)
            return {"id": workout.id, "workout_type": workout.workout_type}

        if data_type == "meal_record":
            record_data = {
                "meal_type": data.get("meal_type"),
                "meal_date": (
                    datetime.strptime(data["meal_date"], "%Y-%m-%d").date()
                    if "meal_date" in data
                    else now.date()
                ),
                "meal_time": data.get("meal_time"),
                "items": list(data.get("items", [])),
                "round_number": round_number,
                "notes": data.get("notes"),
                "source": "conversation_extraction",
            }
            meal = await dao.create_meal_record(record_data)
            return {"id": meal.id, "meal_type": meal.meal_type}

        if data_type == "shopping_list":
            # shopping_list.data 是 dict, 包含 items 数组
            items = data.get("items", [])
            if not isinstance(items, list):
                items = [items]
            results = []
            for item in items:
                record_data = {
                    "name": item.get("name", "未知商品"),
                    "quantity": item.get("quantity"),
                    "purchase_date": now,
                    "round_number": round_number,
                    "source": "conversation_extraction",
                }
                item_record = await dao.create_shopping_item(record_data)
                results.append({"id": item_record.id, "name": item_record.name})
            return {"items": results}

        if data_type == "food_product":
            record_data = {
                "product_id": data["product_id"],
                "name": data["name"],
                "brand": data.get("brand"),
                "weight_per_unit": data.get("weight_per_unit"),
                "ingredients": data.get("ingredients"),
                "nutrition_per_100g": data.get("nutrition_per_100g", {}),
                "allergens": data.get("allergens"),
                "round_number": round_number,
                "source": "conversation_extraction",
            }
            product = await dao.create_food_product(record_data)
            return {"id": product.id, "name": product.name}

        if data_type == "medical_report":
            report_data = {
                "report_date": (
                    datetime.fromisoformat(data["report_date"])
                    if data.get("report_date")
                    else now
                ),
                "report_data": data.get("report_data", {}),
                "report_type": data.get("report_type", "routine"),
                "round_number": round_number,
                "source": "conversation_extraction",
            }
            report = await dao.save_medical_report(report_data)
            return {"id": report.id}

        return {"warning": f"未知的数据类型: {data_type}"}

    # ========== 审计支持方法 ==========

    _AUDIT_DATA_TYPES: ClassVar[dict[str, tuple[str, int]]] = {
        "weight_record": ("_weight_record_ops", 50),
        "meal_record": ("_meal_record_ops", 80),
        "workout_record": ("_workout_record_ops", 50),
        "shopping_list": ("_shopping_item_ops", 80),
        "food_product": ("_food_product_ops", 50),
        "medical_report": ("_medical_report_ops", 30),
    }

    async def get_extraction_snapshot(self, min_round: int) -> str:
        """加载提取数据的快照, 按类型分组格式化, 供审计使用.

        Args:
            min_round: 最小轮次, 低于此值的记录会被过滤

        Returns:
            格式化的数据快照字符串, 无数据返回空字符串

        """
        try:
            dao = await self._get_dao()
            sections: list[str] = []

            for data_type, (ops_attr, limit) in self._AUDIT_DATA_TYPES.items():
                ops = getattr(dao, ops_attr)
                records = await ops.find_by_filters(
                    {"source": "conversation_extraction"},
                    limit=limit,
                )
                filtered = self._filter_by_round(records, min_round)
                if filtered:
                    formatted = self._format_snapshot_section(data_type, filtered)
                    if formatted:
                        sections.append(formatted)

            return "\n\n".join(sections) if sections else ""
        except Exception as e:
            self.logger.warning("加载审计快照失败: %s", e)
            return ""

    async def execute_audit_operation(
        self,
        action: str,
        data_type: str,
        record_id: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行单条审计操作 (create/update/delete).

        Args:
            action: 操作类型 (create/update/delete)
            data_type: 数据类型
            record_id: 目标记录ID (update/delete必需)
            data: 操作数据 (create/update必需)

        Returns:
            操作结果

        """
        try:
            if action == "create":
                if not data:
                    return {"success": False, "error": "create 操作缺少 data"}
                return await self.store_extraction(
                    data_type=data_type,
                    data=data,
                    round_number=None,
                )

            dao = await self._get_dao()
            ops_attr = self._AUDIT_DATA_TYPES.get(data_type, (None, None))[0]
            if not ops_attr:
                return {"success": False, "error": f"未知数据类型: {data_type}"}
            ops = getattr(dao, ops_attr)

            if action == "delete":
                if not record_id:
                    return {"success": False, "error": "delete 操作缺少 record_id"}
                success = await ops.delete_by_id(record_id)
                if success:
                    self.logger.info("审计删除: %s#%d", data_type, record_id)
                    return {"success": True, "action": "delete", "record_id": record_id}
                return {
                    "success": False,
                    "error": f"记录不存在: {data_type}#{record_id}",
                }

            if action == "update":
                if not record_id:
                    return {"success": False, "error": "update 操作缺少 record_id"}
                if not data:
                    return {"success": False, "error": "update 操作缺少 data"}
                clean_data = self._sanitize_update_data(data)
                result = await ops.update(record_id, clean_data)
                if result:
                    self.logger.info("审计更新: %s#%d", data_type, record_id)
                    return {"success": True, "action": "update", "record_id": record_id}
                return {
                    "success": False,
                    "error": f"记录不存在: {data_type}#{record_id}",
                }

            return {"success": False, "error": f"未知操作类型: {action}"}
        except Exception as e:
            self.logger.warning("审计操作异常 (%s %s): %s", action, data_type, e)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _filter_by_round(records: list[Any], min_round: int) -> list[Any]:
        """按 round_number 过滤记录, 无 round_number 字段的保留."""
        result = []
        for r in records:
            rn = getattr(r, "round_number", None)
            if rn is None or rn >= min_round:
                result.append(r)
        return result

    @staticmethod
    def _sanitize_update_data(data: dict[str, Any]) -> dict[str, Any]:
        """清理更新数据, 移除不允许修改的元数据字段."""
        forbidden = {"id", "created_at", "updated_at", "source"}
        return {k: v for k, v in data.items() if k not in forbidden}

    @staticmethod
    def _format_snapshot_section(data_type: str, records: list[Any]) -> str:
        """格式化单个数据类型的快照段落."""
        if not records:
            return ""
        sorted_records = sorted(records, key=lambda x: x.id)
        formatter = _SNAPSHOT_FORMATTERS.get(data_type)
        if not formatter:
            return ""
        lines = [f"共 {len(sorted_records)} 条"]
        for rec in sorted_records:
            line = formatter(rec)
            if line:
                lines.append(line)
        return f"## {data_type}\n" + "\n".join(lines)


def _fmt_weight(w: Any) -> str:
    parts = [f"{w.weight_kg}kg"]
    if w.body_fat_pct:
        parts.append(f"体脂:{w.body_fat_pct}%")
    if w.muscle_mass_kg:
        parts.append(f"肌肉:{w.muscle_mass_kg}kg")
    if w.recorded_at:
        parts.append(w.recorded_at.strftime("%Y-%m-%d %H:%M"))
    if w.round_number:
        parts.append(f"R{w.round_number}")
    return f"[ID:{w.id}] " + ", ".join(parts)


def _fmt_meal(m: Any) -> str:
    items_str = _format_meal_items(m.items)
    time_str = f" {m.meal_time}" if m.meal_time else ""
    notes_str = f" ({m.notes})" if m.notes else ""
    rn = f" R{m.round_number}" if m.round_number else ""
    return (
        f"[ID:{m.id}] {m.meal_date} {m.meal_type or '未知餐型'}{time_str}{rn}: "
        f"{items_str}{notes_str}"
    )


def _fmt_workout(w: Any) -> str:
    parts = [w.workout_type, f"{w.duration}分钟"]
    if w.distance:
        parts.append(f"{w.distance}km")
    parts.append(w.start_time.strftime("%Y-%m-%d %H:%M"))
    if w.round_number:
        parts.append(f"R{w.round_number}")
    if w.notes:
        parts.append(f"备注:{w.notes}")
    return f"[ID:{w.id}] " + ", ".join(parts)


def _fmt_shopping(s: Any) -> str:
    qty = f" x{s.quantity}" if s.quantity else ""
    dt = s.purchase_date.strftime("%Y-%m-%d") if s.purchase_date else ""
    rn = f" R{s.round_number}" if s.round_number else ""
    return f"[ID:{s.id}] {s.name}{qty}, {dt}{rn}"


def _fmt_product(p: Any) -> str:
    brand = f" ({p.brand})" if p.brand else ""
    rn = f" R{p.round_number}" if p.round_number else ""
    return (
        f"[ID:{p.id}] {p.name}{brand}{rn}, "
        f"营养: {json.dumps(p.nutrition_per_100g, ensure_ascii=False)}"
    )


def _fmt_report(r: Any) -> str:
    rtype = r.report_type or "未知"
    rdate = r.report_date.strftime("%Y-%m-%d") if r.report_date else ""
    rn = f" R{r.round_number}" if r.round_number else ""
    data_preview = (
        json.dumps(r.report_data, ensure_ascii=False)[:200] if r.report_data else "{}"
    )
    return f"[ID:{r.id}] {rdate} ({rtype}){rn}: {data_preview}"


def _format_meal_items(items: Any) -> str:
    if not items:
        return "无详情"
    try:
        items_list = json.loads(items) if isinstance(items, str) else items
        parts = []
        for it in items_list[:6]:
            name = it.get("name", "?")
            qty = it.get("quantity", "")
            parts.append(f"{name}({qty})" if qty else name)
        result = ", ".join(parts)
        if len(items_list) > 6:
            result += f" 等{len(items_list)}项"
        return result
    except Exception as e:
        logger.debug("膳食项格式化失败, 降级为str截断: %s", e)
        return str(items)[:80]


_SNAPSHOT_FORMATTERS = {
    "weight_record": _fmt_weight,
    "meal_record": _fmt_meal,
    "workout_record": _fmt_workout,
    "shopping_list": _fmt_shopping,
    "food_product": _fmt_product,
    "medical_report": _fmt_report,
}


def get_health_data_extraction_service(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
) -> HealthDataExtractionService:
    """创建健康数据提取服务实例."""
    return HealthDataExtractionService(
        user_id,
        thread_id,
        agent_id=agent_id,
    )
