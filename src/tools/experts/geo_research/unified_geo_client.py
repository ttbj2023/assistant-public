"""统一地图客户端 - 腾讯主 + 百度 fallback.

fallback 仅在 HTTP 层面错误 (429/timeout/5xx) 时触发,
业务错误 (status!=0) 不切换数据源.

district_search 由腾讯独占, traffic 由百度独占, 不在此模块统一.
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.experts.geo_research import baidu_maps_client as baidu
from src.tools.experts.geo_research import tencent_maps_client as tencent

logger = logging.getLogger(__name__)


def _is_retryable_error(result: dict[str, Any]) -> bool:
    """判断结果是否为可 fallback 的 HTTP 层面错误.

    业务错误 (包含 "status=") 不 fallback,
    仅 HTTP 错误 (429/timeout/5xx/连接失败) 才切换.
    """
    err = result.get("error", "")
    if not err:
        return False
    return "status=" not in err


async def geocode(address: str, city: str = "") -> dict[str, Any]:
    """地理编码: 地址→坐标. 腾讯主, 百度备."""
    result = await tencent.call_geocoder(address)
    if "error" not in result:
        return result

    if _is_retryable_error(result):
        logger.warning("腾讯geocode失败, 切换百度: %s", result["error"])
        baidu_result = await baidu.call_geocode(address, city=city)
        if "error" not in baidu_result:
            baidu_result["source"] = "baidu"
            return baidu_result

    return result


async def reverse_geocode(lat: float, lng: float) -> dict[str, Any]:
    """逆地理编码: 坐标→地址. 腾讯主, 百度备."""
    result = await tencent.call_reverse_geocoder(lat, lng)
    if "error" not in result:
        return result

    if _is_retryable_error(result):
        logger.warning("腾讯reverse_geocode失败, 切换百度: %s", result["error"])
        baidu_result = await baidu.call_reverse_geocode(lat, lng)
        if "error" not in baidu_result:
            baidu_result["source"] = "baidu"
            return baidu_result

    return result


async def district(keyword: str, get_sub: bool = True) -> dict[str, Any]:
    """行政区划查询. 腾讯独占, 无 fallback."""
    return await tencent.call_district(keyword, get_sub=get_sub)


async def place_search(
    keyword: str,
    *,
    region: str = "",
    lat: float | None = None,
    lng: float | None = None,
    radius: int = 1000,
) -> dict[str, Any]:
    """POI 搜索. 腾讯主, 百度备."""
    result = await tencent.call_place_search(
        keyword, region=region, lat=lat, lng=lng, radius=radius
    )
    if "error" not in result:
        return result

    if _is_retryable_error(result):
        logger.warning("腾讯place_search失败, 切换百度: %s", result["error"])
        baidu_result = await baidu.call_place_search(
            keyword, region=region, lat=lat, lng=lng, radius=radius
        )
        if "error" not in baidu_result:
            baidu_result["source"] = "baidu"
            return baidu_result

    return result


async def driving(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
) -> dict[str, Any]:
    """驾车路线规划. 腾讯主, 百度备."""
    result = await tencent.call_driving(origin_lat, origin_lng, dest_lat, dest_lng)
    if "error" not in result:
        return result

    if _is_retryable_error(result):
        logger.warning("腾讯driving失败, 切换百度: %s", result["error"])
        origin = f"{origin_lat},{origin_lng}"
        destination = f"{dest_lat},{dest_lng}"
        baidu_result = await baidu.call_directions(origin, destination, mode="driving")
        if "error" not in baidu_result:
            baidu_result["source"] = "baidu"
            return baidu_result

    return result


async def transit(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    city: str,
) -> dict[str, Any]:
    """公交路线规划. 腾讯主, 百度备."""
    result = await tencent.call_transit(
        origin_lat, origin_lng, dest_lat, dest_lng, city
    )
    if "error" not in result:
        return result

    if _is_retryable_error(result):
        logger.warning("腾讯transit失败, 切换百度: %s", result["error"])
        origin = f"{origin_lat},{origin_lng}"
        destination = f"{dest_lat},{dest_lng}"
        baidu_result = await baidu.call_directions(origin, destination, mode="transit")
        if "error" not in baidu_result:
            baidu_result["source"] = "baidu"
            return baidu_result

    return result


async def walking(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
) -> dict[str, Any]:
    """步行路线规划. 腾讯主, 百度备."""
    result = await tencent.call_walking(origin_lat, origin_lng, dest_lat, dest_lng)
    if "error" not in result:
        return result

    if _is_retryable_error(result):
        logger.warning("腾讯walking失败, 切换百度: %s", result["error"])
        origin = f"{origin_lat},{origin_lng}"
        destination = f"{dest_lat},{dest_lng}"
        baidu_result = await baidu.call_directions(origin, destination, mode="walking")
        if "error" not in baidu_result:
            baidu_result["source"] = "baidu"
            return baidu_result

    return result
