"""腾讯地图直连 API 客户端 - httpx 轻量封装.

提供:
- async 函数: 供 unified_geo_client 直接调用
- SK 签名认证 (MD5)
- QPS 限流 (Semaphore, 留余量)
- ExpertCache 缓存

所有 API 通过环境变量 TENCENT_MAPS_KEY / TENCENT_MAPS_SK 认证.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

import httpx

from src.tools.shared.cache import ExpertCache, get_expert_cache

logger = logging.getLogger(__name__)

_BASE_URL = "https://apis.map.qq.com"
_QPS_LIMIT = 4

_semaphore: asyncio.Semaphore | None = None


def _get_retry_params() -> dict[str, Any]:
    """从统一配置获取重试参数."""
    from src.config.retry_config import get_http_retry_params

    return get_http_retry_params()


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_QPS_LIMIT)
    return _semaphore


def _get_key() -> str:
    from src.config.credentials_registry import get_credential

    return get_credential("tencent_maps_key")


def _get_sk() -> str:
    from src.config.credentials_registry import get_credential

    return get_credential("tencent_maps_sk")


def _sign(path: str, params: dict[str, str], sk: str) -> str:
    """腾讯地图 SK 签名: path?sorted_query+sk → MD5 → 大写.

    签名步骤:
    1. 对参数按 key 字典序排序
    2. 拼接 path? + sorted key=value pairs
    3. 末尾追加 SK
    4. MD5 后转大写
    """
    sorted_items = sorted(params.items())
    query = "&".join(f"{k}={v}" for k, v in sorted_items)
    raw = f"{path}?{query}{sk}"
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest().upper()


async def _tencent_get(
    path: str,
    params: dict[str, Any],
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """腾讯 API 通用 GET 请求, 含 SK 签名/QPS 限流/简单重试.

    仅 HTTP 429/5xx 触发重试, 业务错误(status!=0)不重试.
    """
    key = _get_key()
    if not key:
        return {"error": "TENCENT_MAPS_KEY 环境变量未设置"}

    sk = _get_sk()

    str_params: dict[str, str] = {k: str(v) for k, v in params.items()}
    str_params["key"] = key

    if sk:
        str_params["sig"] = _sign(path, str_params, sk)

    sem = _get_semaphore()
    last_error: Exception | None = None

    retry = _get_retry_params()
    max_retries = retry["max_retries"]
    retryable_status = retry["retryable_status"]
    base_delay = retry["base_delay"]
    rate_limit_delay = retry["rate_limit_delay"]

    for attempt in range(1, max_retries + 1):
        async with sem:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(f"{_BASE_URL}{path}", params=str_params)
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code
                if status not in retryable_status or attempt == max_retries:
                    logger.error("腾讯API失败(%s, status=%s): %s", path, status, e)
                    break
                delay = rate_limit_delay if status == 429 else base_delay * attempt
                logger.warning("腾讯API重试(%s, status=%s, %ss后)", path, status, delay)
                await asyncio.sleep(delay)
            except Exception as e:
                last_error = e
                logger.error("腾讯API异常(%s): %s", path, e)
                break

    return {"error": f"腾讯API调用失败: {last_error!s}"}


def _check_response(data: dict[str, Any]) -> dict[str, Any]:
    """检查腾讯 API 响应状态, status=0 视为成功."""
    status = data.get("status")
    if status == 0:
        return data
    return {
        "error": (f"腾讯API返回错误: status={status}, msg={data.get('message', '')}")
    }


# ═══════════════════════════════════════════════════════════════
# 底层 API 调用函数 (供 unified_geo_client 调用)
# ═══════════════════════════════════════════════════════════════


async def call_geocoder(address: str) -> dict[str, Any]:
    """地理编码: 地址→坐标.

    Args:
        address: 文字地址, 如 "北京市海淀区上地十街10号"

    Returns:
        成功: {"address", "lat", "lng", "formatted_address", "source": "tencent"}
        失败: {"error": str}
    """
    cache = get_expert_cache()
    cache_key = ExpertCache.make_key("tencent_geocoder", address=address)
    cached = await cache.get_geo(cache_key)
    if cached is not None:
        return json.loads(cached)

    raw = await _tencent_get("/ws/geocoder/v1/", {"address": address})
    result = _check_response(raw)

    if "error" not in result:
        r = result.get("result", {})
        loc = r.get("location", {})
        formatted = {
            "address": address,
            "lat": loc.get("lat"),
            "lng": loc.get("lng"),
            "formatted_address": r.get("formatted_address", ""),
            "source": "tencent",
        }
        await cache.set_geo(cache_key, json.dumps(formatted, ensure_ascii=False))
        return formatted
    return result


async def call_reverse_geocoder(lat: float, lng: float) -> dict[str, Any]:
    """逆地理编码: 坐标→地址.

    Args:
        lat: 纬度
        lng: 经度

    Returns:
        成功: {"formatted_address", "address_component", "source": "tencent"}
        失败: {"error": str}
    """
    location = f"{lat},{lng}"
    cache = get_expert_cache()
    cache_key = ExpertCache.make_key("tencent_reverse", location=location)
    cached = await cache.get_geo(cache_key)
    if cached is not None:
        return json.loads(cached)

    raw = await _tencent_get("/ws/geocoder/v1/", {"location": location})
    result = _check_response(raw)

    if "error" not in result:
        r = result.get("result", {})
        formatted = {
            "formatted_address": r.get("formatted_address", ""),
            "address_component": r.get("address_component", {}),
            "source": "tencent",
        }
        await cache.set_geo(cache_key, json.dumps(formatted, ensure_ascii=False))
        return formatted
    return result


async def call_district(
    keyword: str,
    get_sub: bool = True,
) -> dict[str, Any]:
    """行政区划查询.

    Args:
        keyword: 行政区划名称, 如 "苏州","北京"
        get_sub: 是否获取下级行政区划

    Returns:
        成功: {"districts": [...], "source": "tencent"}
        失败: {"error": str}
    """
    params: dict[str, Any] = {"keyword": keyword}
    if get_sub:
        params["get_sub"] = "true"

    cache = get_expert_cache()
    cache_key = ExpertCache.make_key(
        "tencent_district", keyword=keyword, get_sub=get_sub
    )
    cached = await cache.get_geo(cache_key)
    if cached is not None:
        return json.loads(cached)

    raw = await _tencent_get("/ws/district/v1/get", params)
    result = _check_response(raw)

    if "error" not in result:
        districts = []
        for d in result.get("result", []):
            entry: dict[str, Any] = {
                "id": d.get("id", ""),
                "name": d.get("name", ""),
                "fullname": d.get("fullname", ""),
            }
            loc = d.get("location", {})
            if loc:
                entry["lat"] = loc.get("lat")
                entry["lng"] = loc.get("lng")
            cidx = d.get("cidx")
            if cidx:
                entry["cidx"] = cidx
            districts.append(entry)
        formatted = {"districts": districts, "source": "tencent"}
        await cache.set_geo(cache_key, json.dumps(formatted, ensure_ascii=False))
        return formatted
    return result


async def call_place_search(
    keyword: str,
    *,
    region: str = "",
    lat: float | None = None,
    lng: float | None = None,
    radius: int = 1000,
    page_size: int = 20,
) -> dict[str, Any]:
    """POI 搜索.

    Args:
        keyword: 搜索关键词, 如 "餐厅","博物馆"
        region: 城市/区域名 (与 lat/lng 二选一)
        lat: 中心点纬度 (周边搜索)
        lng: 中心点经度 (周边搜索)
        radius: 搜索半径(米), 仅周边搜索时有效
        page_size: 每页条数 (最大20)

    Returns:
        成功: {"places": [...], "source": "tencent"}
        失败: {"error": str}
    """
    if lat is not None and lng is not None:
        boundary = f"nearby({lat},{lng},{radius})"
    elif region:
        boundary = f"region({region},0)"
    else:
        boundary = "region(全国,0)"

    params: dict[str, Any] = {
        "keyword": keyword,
        "boundary": boundary,
        "page_size": min(page_size, 20),
    }

    cache = get_expert_cache()
    cache_key = ExpertCache.make_key(
        "tencent_place", keyword=keyword, boundary=boundary
    )
    cached = await cache.get_geo(cache_key)
    if cached is not None:
        return json.loads(cached)

    raw = await _tencent_get("/ws/place/v1/search", params)
    result = _check_response(raw)

    if "error" not in result:
        places = []
        for p in result.get("data", []):
            loc = p.get("location", {})
            places.append({
                "name": p.get("title", ""),
                "address": p.get("address", ""),
                "lat": loc.get("lat"),
                "lng": loc.get("lng"),
                "distance": p.get("_distance"),
                "category": p.get("category", ""),
                "id": p.get("id", ""),
            })
        formatted = {"places": places, "source": "tencent"}
        await cache.set_geo(cache_key, json.dumps(formatted, ensure_ascii=False))
        return formatted
    return result


async def call_driving(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
) -> dict[str, Any]:
    """驾车路线规划.

    Returns:
        成功: {"routes": [{distance, duration, toll, steps}], "mode", "source"}
        失败: {"error": str}
    """
    params: dict[str, Any] = {
        "from": f"{origin_lat},{origin_lng}",
        "to": f"{dest_lat},{dest_lng}",
    }

    raw = await _tencent_get("/ws/direction/v1/driving/", params, timeout=15.0)
    result = _check_response(raw)

    if "error" not in result:
        routes = []
        for route in result.get("result", {}).get("routes", []):
            steps = []
            for step in route.get("steps", []):
                steps.append({
                    "instruction": step.get("instruction", ""),
                    "distance": step.get("distance"),
                    "duration": step.get("duration"),
                })
            routes.append({
                "distance": route.get("distance"),
                "duration": route.get("duration"),
                "toll": route.get("toll"),
                "steps": steps[:10],
            })
        return {"routes": routes, "mode": "driving", "source": "tencent"}
    return result


async def call_transit(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    city: str,
) -> dict[str, Any]:
    """公交路线规划 (需要出发城市).

    Args:
        city: 出发城市, 如 "北京"

    Returns:
        成功: {"routes": [{distance, duration, steps}], "mode", "source"}
        失败: {"error": str}
    """
    params: dict[str, Any] = {
        "from": f"{origin_lat},{origin_lng}",
        "to": f"{dest_lat},{dest_lng}",
        "city": city,
    }

    raw = await _tencent_get("/ws/direction/v1/transit/", params, timeout=15.0)
    result = _check_response(raw)

    if "error" not in result:
        routes = []
        for route in result.get("result", {}).get("routes", []):
            steps = []
            for step in route.get("steps", []):
                steps.append({
                    "instruction": step.get("instruction", ""),
                    "distance": step.get("distance"),
                    "duration": step.get("duration"),
                    "mode": step.get("mode", ""),
                })
            routes.append({
                "distance": route.get("distance"),
                "duration": route.get("duration"),
                "steps": steps[:10],
            })
        return {"routes": routes, "mode": "transit", "source": "tencent"}
    return result


async def call_walking(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
) -> dict[str, Any]:
    """步行路线规划.

    Returns:
        成功: {"routes": [{distance, duration, steps}], "mode", "source"}
        失败: {"error": str}
    """
    params: dict[str, Any] = {
        "from": f"{origin_lat},{origin_lng}",
        "to": f"{dest_lat},{dest_lng}",
    }

    raw = await _tencent_get("/ws/direction/v1/walking/", params)
    result = _check_response(raw)

    if "error" not in result:
        routes = []
        for route in result.get("result", {}).get("routes", []):
            steps = []
            for step in route.get("steps", []):
                steps.append({
                    "instruction": step.get("instruction", ""),
                    "distance": step.get("distance"),
                    "duration": step.get("duration"),
                })
            routes.append({
                "distance": route.get("distance"),
                "duration": route.get("duration"),
                "steps": steps[:10],
            })
        return {"routes": routes, "mode": "walking", "source": "tencent"}
    return result
