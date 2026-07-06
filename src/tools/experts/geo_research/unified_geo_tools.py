"""geo_research 专家 Agent 内部工具集 - 对 LLM 暴露的统一工具.

8 个工具, 隐藏腾讯/百度实现细节:
- geocode / reverse_geocode: 地理编码
- district_search: 行政区划
- place_search: POI 搜索
- driving_directions / transit_directions / walking_directions: 路线规划
- traffic: 实时路况 (百度独占)

设计约定:
- 参数统一使用 lat/lng 分离的 float, 避免坐标格式混淆
- 路线工具拆为 3 个独立工具, LLM 不需要传 mode 参数
- 工具名自解释, 不加 geo_ 前缀 (geo_research 是顶层专家工具名)
"""

from __future__ import annotations

import json
import logging
from typing import Any, override

from pydantic import BaseModel, ConfigDict, Field

from src.tools.experts.geo_research import baidu_maps_client as baidu
from src.tools.experts.geo_research import unified_geo_client as unified
from src.tools.shared.base_external_tool import BaseExternalTool

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 输入 Schema
# ═══════════════════════════════════════════════════════════════


class _GeocodeInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    address: str = Field(description="文字地址, 如 '北京市海淀区上地十街10号'")
    city: str = Field(default="", description="城市名, 用于消除歧义")


class _ReverseGeocodeInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    lat: float = Field(description="纬度")
    lng: float = Field(description="经度")


class _DistrictInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    keyword: str = Field(description="行政区划名称, 如 '苏州','北京'")
    get_sub: bool = Field(default=True, description="是否获取下级行政区划")


class _PlaceSearchInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    keyword: str = Field(description="搜索关键词, 如 '餐厅','博物馆'")
    region: str = Field(default="", description="城市/区域名 (与 lat/lng 二选一)")
    lat: float | None = Field(default=None, description="中心点纬度 (周边搜索)")
    lng: float | None = Field(default=None, description="中心点经度 (周边搜索)")
    radius: int = Field(default=1000, description="搜索半径(米), 仅周边搜索时有效")


class _RouteInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    origin_lat: float = Field(description="起点纬度")
    origin_lng: float = Field(description="起点经度")
    dest_lat: float = Field(description="终点纬度")
    dest_lng: float = Field(description="终点经度")


class _TransitInput(_RouteInput):
    city: str = Field(description="出发城市, 如 '北京'")


class _TrafficInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    lat: float = Field(description="中心点纬度")
    lng: float = Field(description="中心点经度")
    radius: int = Field(default=200, description="查询半径(米), 1-1000")


# ═══════════════════════════════════════════════════════════════
# 工具实现
# ═══════════════════════════════════════════════════════════════


def _has_any_key() -> bool:
    from src.config.credentials_registry import has_credential

    return has_credential("tencent_maps_key") or has_credential("baidu_maps_ak")


def _has_tencent_key() -> bool:
    from src.config.credentials_registry import has_credential

    return has_credential("tencent_maps_key")


def _has_baidu_key() -> bool:
    from src.config.credentials_registry import has_credential

    return has_credential("baidu_maps_ak")


class GeocodeTool(BaseExternalTool):
    name: str = "geocode"
    summary: str = "地址转坐标(地理编码)"
    description: str = (
        '将文字地址转换为经纬度坐标.\n示例: {"address": "北京市海淀区上地十街10号"}'
    )
    args_schema: type[BaseModel] = _GeocodeInput
    timeout: float = 10.0

    @override
    async def is_available(self) -> bool:
        return _has_any_key()

    @override
    async def _arun(self, address: str, city: str = "") -> str:
        result = await unified.geocode(address, city=city)
        return json.dumps(result, ensure_ascii=False)


class ReverseGeocodeTool(BaseExternalTool):
    name: str = "reverse_geocode"
    summary: str = "坐标转地址(逆地理编码)"
    description: str = (
        '将经纬度坐标转换为文字地址.\n示例: {"lat": 39.914, "lng": 116.403}'
    )
    args_schema: type[BaseModel] = _ReverseGeocodeInput
    timeout: float = 10.0

    @override
    async def is_available(self) -> bool:
        return _has_any_key()

    @override
    async def _arun(self, lat: float, lng: float) -> str:
        result = await unified.reverse_geocode(lat, lng)
        return json.dumps(result, ensure_ascii=False)


class DistrictSearchTool(BaseExternalTool):
    name: str = "district_search"
    summary: str = "行政区划查询(省/市/区县列表)"
    description: str = (
        "查询行政区划信息, 返回指定区域的下级区县列表.\n"
        "用于需要精确行政区划数据的场景 (Gemini 容易遗漏或混淆).\n"
        '示例: {"keyword": "苏州"} → 姑苏区/虎丘区/吴中区/相城区/吴江区/'
        "常熟市/张家港市/昆山市/太仓市"
    )
    args_schema: type[BaseModel] = _DistrictInput
    timeout: float = 10.0

    @override
    async def is_available(self) -> bool:
        return _has_tencent_key()

    @override
    async def _arun(self, keyword: str, get_sub: bool = True) -> str:
        result = await unified.district(keyword, get_sub=get_sub)
        return json.dumps(result, ensure_ascii=False)


class PlaceSearchTool(BaseExternalTool):
    name: str = "place_search"
    summary: str = "POI搜索(餐厅/酒店/景点等)"
    description: str = (
        "搜索POI (餐厅/酒店/景点/博物馆等).\n"
        "支持按城市搜索或按坐标周边搜索.\n"
        '示例: {"keyword": "博物馆", "region": "北京"} 或 '
        '{"keyword": "咖啡", "lat": 39.914, "lng": 116.403}'
    )
    args_schema: type[BaseModel] = _PlaceSearchInput
    timeout: float = 10.0

    @override
    async def is_available(self) -> bool:
        return _has_any_key()

    @override
    async def _arun(
        self,
        keyword: str,
        region: str = "",
        lat: float | None = None,
        lng: float | None = None,
        radius: int = 1000,
    ) -> str:
        result = await unified.place_search(
            keyword, region=region, lat=lat, lng=lng, radius=radius
        )
        return json.dumps(result, ensure_ascii=False)


class DrivingDirectionsTool(BaseExternalTool):
    name: str = "driving_directions"
    summary: str = "驾车路线规划(距离/时间/过路费)"
    description: str = (
        "规划从起点到终点的驾车路线, 返回距离/行驶时间/过路费.\n"
        '示例: {"origin_lat": 39.914, "origin_lng": 116.403, '
        '"dest_lat": 31.230, "dest_lng": 121.473}'
    )
    args_schema: type[BaseModel] = _RouteInput
    timeout: float = 15.0

    @override
    async def is_available(self) -> bool:
        return _has_any_key()

    @override
    async def _arun(
        self,
        origin_lat: float,
        origin_lng: float,
        dest_lat: float,
        dest_lng: float,
    ) -> str:
        result = await unified.driving(origin_lat, origin_lng, dest_lat, dest_lng)
        return json.dumps(result, ensure_ascii=False)


class TransitDirectionsTool(BaseExternalTool):
    name: str = "transit_directions"
    summary: str = "公交路线规划(距离/时间/换乘方案)"
    description: str = (
        "规划从起点到终点的公共交通路线, 返回距离/时间/换乘方案.\n"
        "需要指定出发城市.\n"
        '示例: {"origin_lat": 39.895, "origin_lng": 116.322, '
        '"dest_lat": 40.050, "dest_lng": 116.608, "city": "北京"}'
    )
    args_schema: type[BaseModel] = _TransitInput
    timeout: float = 15.0

    @override
    async def is_available(self) -> bool:
        return _has_any_key()

    @override
    async def _arun(
        self,
        origin_lat: float,
        origin_lng: float,
        dest_lat: float,
        dest_lng: float,
        city: str = "",
    ) -> str:
        result: dict[str, Any] = await unified.transit(
            origin_lat, origin_lng, dest_lat, dest_lng, city
        )
        return json.dumps(result, ensure_ascii=False)


class WalkingDirectionsTool(BaseExternalTool):
    name: str = "walking_directions"
    summary: str = "步行路线规划(距离/时间)"
    description: str = (
        "规划从起点到终点的步行路线, 返回距离/步行时间.\n"
        '示例: {"origin_lat": 39.999, "origin_lng": 116.273, '
        '"dest_lat": 39.968, "dest_lng": 116.323}'
    )
    args_schema: type[BaseModel] = _RouteInput
    timeout: float = 10.0

    @override
    async def is_available(self) -> bool:
        return _has_any_key()

    @override
    async def _arun(
        self,
        origin_lat: float,
        origin_lng: float,
        dest_lat: float,
        dest_lng: float,
    ) -> str:
        result = await unified.walking(origin_lat, origin_lng, dest_lat, dest_lng)
        return json.dumps(result, ensure_ascii=False)


class TrafficTool(BaseExternalTool):
    name: str = "traffic"
    summary: str = "查询周边实时路况"
    description: str = (
        "查询指定位置周边的实时路况, 返回拥堵状况和速度.\n"
        '示例: {"lat": 39.914, "lng": 116.403, "radius": 500}'
    )
    args_schema: type[BaseModel] = _TrafficInput
    timeout: float = 10.0

    @override
    async def is_available(self) -> bool:
        return _has_baidu_key()

    @override
    async def _arun(self, lat: float, lng: float, radius: int = 200) -> str:
        result = await baidu.call_traffic_around(lat, lng, radius=radius)
        return json.dumps(result, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════════════


def create_geo_sub_tools() -> list[BaseExternalTool]:
    """创建全部地理子工具实例 (供 geo_research deep 模式 Agent 使用)."""
    return [
        GeocodeTool(),
        ReverseGeocodeTool(),
        DistrictSearchTool(),
        PlaceSearchTool(),
        DrivingDirectionsTool(),
        TransitDirectionsTool(),
        WalkingDirectionsTool(),
        TrafficTool(),
    ]
