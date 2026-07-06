"""百度地图直连API客户端 - httpx轻量封装.

仅提供 async 函数形态, 供 unified_geo_client (主备切换编排) 和
unified_geo_tools (供 geo_research deep 模式 Agent 调用) 使用.

通用 HTTP 基础设施 (QPS 限流,重试,认证) 已抽到 src.tools.shared.baidu_http,
weather_tool 也直接使用该共享层, 不再寄生在本模块内.

所有API通过环境变量 BAIDU_MAPS_AK 认证, 免费额度 QPS=3.
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.shared.baidu_http import baidu_get, check_response

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 底层API调用函数 (供 unified_geo_client / unified_geo_tools 共享)
# ═══════════════════════════════════════════════════════════════


async def call_geocode(address: str, city: str = "") -> dict[str, Any]:
    """地理编码: 地址→坐标."""
    params: dict[str, Any] = {"address": address}
    if city:
        params["city"] = city

    raw = await baidu_get("/geocoding/v3/", params)
    result = check_response(raw)

    if "error" not in result:
        loc = result.get("result", {}).get("location", {})
        return {
            "address": address,
            "lat": loc.get("lat"),
            "lng": loc.get("lng"),
            "precise": result.get("result", {}).get("precise"),
            "confidence": result.get("result", {}).get("confidence"),
        }
    return result


async def call_reverse_geocode(lat: float, lng: float) -> dict[str, Any]:
    """逆地理编码: 坐标→地址."""
    params = {"location": f"{lat},{lng}"}

    raw = await baidu_get("/reverse_geocoding/v3/", params)
    result = check_response(raw)

    if "error" not in result:
        r = result.get("result", {})
        return {
            "formatted_address": r.get("formatted_address", ""),
            "address_component": r.get("addressComponent", {}),
            "business": r.get("business", ""),
        }
    return result


async def call_place_search(
    query: str,
    *,
    region: str = "",
    lat: float | None = None,
    lng: float | None = None,
    radius: int = 5000,
    page_size: int = 10,
) -> dict[str, Any]:
    """POI搜索."""
    params: dict[str, Any] = {
        "query": query,
        "page_size": page_size,
    }

    if lat is not None and lng is not None:
        params["location"] = f"{lat},{lng}"
        params["radius"] = radius
    elif region:
        params["region"] = region
        params["city_limit"] = "true"

    raw = await baidu_get("/place/v2/search", params)
    result = check_response(raw)

    if "error" not in result:
        places = []
        for p in result.get("results", []):
            places.append({
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "lat": p.get("location", {}).get("lat"),
                "lng": p.get("location", {}).get("lng"),
                "telephone": p.get("telephone", ""),
                "rating": p.get("detail_info", {}).get("overall_rating", ""),
                "distance": p.get("detail_info", {}).get("distance", ""),
                "uid": p.get("uid", ""),
            })
        return {"places": places}
    return result


async def call_directions(
    origin: str,
    destination: str,
    mode: str = "driving",
) -> dict[str, Any]:
    """路线规划.

    origin/destination: "纬度,经度" 格式
    mode: driving/transit/walking/riding
    """
    path = f"/directionlite/v1/{mode}"
    params: dict[str, Any] = {
        "origin": origin,
        "destination": destination,
    }

    raw = await baidu_get(path, params, timeout=15.0)
    result = check_response(raw)

    if "error" not in result:
        routes = []
        for route in result.get("result", {}).get("routes", []):
            r: dict[str, Any] = {
                "distance": route.get("distance"),
                "duration": route.get("duration"),
            }
            if mode == "driving":
                r["toll"] = route.get("toll")
            steps = []
            for step in route.get("steps", []):
                steps.append({
                    "instruction": step.get("instruction", ""),
                    "road_name": step.get("road_name", ""),
                    "distance": step.get("distance"),
                    "duration": step.get("duration"),
                })
            r["steps"] = steps[:10]
            routes.append(r)
        return {"routes": routes, "mode": mode}
    return result


async def call_traffic_around(
    lat: float,
    lng: float,
    radius: int = 200,
) -> dict[str, Any]:
    """周边实时路况."""
    params: dict[str, Any] = {
        "center": f"{lat},{lng}",
        "radius": radius,
        "coord_type_input": "bd09ll",
    }

    raw = await baidu_get("/traffic/v1/around", params)
    result = check_response(raw)

    if "error" not in result:
        roads = []
        for rd in result.get("road_traffic", []):
            sections = []
            for s in rd.get("congestion_sections", []):
                sections.append({
                    "desc": s.get("section_desc", ""),
                    "status": s.get("status"),
                    "speed": s.get("speed"),
                    "trend": s.get("congestion_trend", ""),
                })
            roads.append({
                "road_name": rd.get("road_name", ""),
                "congestion_sections": sections,
            })
        evaluation = result.get("evaluation", {})
        desc = result.get("description", "")
        return {
            "description": desc,
            "evaluation": evaluation,
            "roads": roads,
        }
    return result


__all__ = [
    "call_directions",
    "call_geocode",
    "call_place_search",
    "call_reverse_geocode",
    "call_traffic_around",
]
