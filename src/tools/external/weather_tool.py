"""天气查询工具 - 主Agent可直接调用的轻量天气工具.

完全独立的工具实现, 直接使用百度地图天气 API, 不依赖 geo_research 模块.
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar, override

from pydantic import BaseModel, ConfigDict, Field

from src.tools.shared.baidu_http import baidu_get, check_response
from src.tools.shared.base_external_tool import BaseExternalTool
from src.tools.shared.cache import ExpertCache, get_expert_cache

logger = logging.getLogger(__name__)


class WeatherQueryInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    location: str = Field(
        description="城市名或地区名, 如 '北京','上海浦东', 或 '经度,纬度'"
    )


class WeatherQueryTool(BaseExternalTool):
    """天气查询工具 - 供主Agent直接调用, 无需经过geo_research专家."""

    name: str = "weather_query"
    summary: str = "天气查询, 获取实时天气和未来预报(温度/风力/空气质量)"
    search_keywords: ClassVar[list[str]] = [
        "天气",
        "温度",
        "下雨",
        "风力",
        "空气质量",
        "预报",
    ]
    description: str = (
        "天气查询工具, 查询指定城市的实时天气和未来多日天气预报.\n"
        "包含温度/湿度/风力/空气质量/降水概率等详细信息.\n"
        "响应快速, 适合简单的天气查询需求.\n"
        '示例: {"location": "北京"} 或 {"location": "上海浦东"}'
    )
    args_schema: type[BaseModel] = WeatherQueryInput
    timeout: float = 10.0

    @override
    async def is_available(self) -> bool:
        from src.config.credentials_registry import has_credential

        return has_credential("baidu_maps_ak")

    @override
    async def _arun(self, location: str) -> str:
        result = await _call_weather(location)

        if "error" in result:
            return json.dumps(result, ensure_ascii=False)

        now = result.get("now", {})
        loc = result.get("location", {})
        forecasts = result.get("forecasts", [])

        lines = [
            f"{'的'.join(filter(None, [loc.get('province', ''), loc.get('city', ''), loc.get('name', '')]))}天气:",
            "",
            f"当前: {now.get('text', '')} {now.get('temp', '?')}°C "
            f"(体感{now.get('feels_like', '?')}°C, 湿度{now.get('rh', '?')}%)",
            f"风: {now.get('wind_dir', '')} {now.get('wind_class', '')}",
            f"能见度: {now.get('vis', '?')}m | AQI: {now.get('aqi', '?')} | PM2.5: {now.get('pm25', '?')}",
        ]

        if forecasts:
            lines.append("")
            lines.append("预报:")
            for fc in forecasts[:3]:
                lines.append(
                    f"  {fc.get('date', '')}: {fc.get('text_day', '')}→{fc.get('text_night', '')} "
                    f"{fc.get('low', '?')}~{fc.get('high', '?')}°C"
                )

        return "\n".join(lines)


async def _call_weather(location: str) -> dict[str, Any]:
    """百度天气查询 - 直接调用百度 API, 含缓存."""
    params: dict[str, Any] = {"data_type": "all"}

    if "," in location:
        params["location"] = location
    else:
        params["district"] = location

    cache = get_expert_cache()
    cache_key = ExpertCache.make_key("baidu_weather", location=location)
    cached = await cache.get_geo(cache_key)
    if cached is not None:
        return json.loads(cached)

    raw = await baidu_get("/weather/v1/", params)
    result = check_response(raw)

    if "error" not in result:
        now = result.get("result", {}).get("now", {})
        forecasts = result.get("result", {}).get("forecasts", [])
        formatted = {
            "location": result.get("result", {}).get("location", {}),
            "now": now,
            "forecasts": forecasts[:3],
        }
        await cache.set_geo(cache_key, json.dumps(formatted, ensure_ascii=False))
        return formatted
    return result
