"""unified_geo_tools 单元测试 - 工具接口和命名.

测试范围:
1. create_geo_sub_tools - 返回 8 个工具
2. 工具 name 和 args_schema 正确性
3. is_available 逻辑 (环境变量检查)
4. _arun 调用 unified_geo_client (mock)
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.experts.geo_research.unified_geo_tools import (
    DistrictSearchTool,
    DrivingDirectionsTool,
    GeocodeTool,
    TrafficTool,
    TransitDirectionsTool,
    WalkingDirectionsTool,
    create_geo_sub_tools,
)

EXPECTED_NAMES = {
    "geocode",
    "reverse_geocode",
    "district_search",
    "place_search",
    "driving_directions",
    "transit_directions",
    "walking_directions",
    "traffic",
}


# =============================================================================
# 1. create_geo_sub_tools 测试
# =============================================================================


class TestCreateGeoSubTools:
    """工具工厂函数测试"""

    def test_should_return_8_tools(self):
        tools = create_geo_sub_tools()
        assert len(tools) == 8

    def test_should_have_all_expected_names(self):
        tools = create_geo_sub_tools()
        names = {t.name for t in tools}
        assert names == EXPECTED_NAMES

    def test_should_not_have_geo_prefix(self):
        tools = create_geo_sub_tools()
        for tool in tools:
            assert not tool.name.startswith("geo_")

    def test_should_not_have_baidu_prefix(self):
        tools = create_geo_sub_tools()
        for tool in tools:
            assert not tool.name.startswith("baidu_")


# =============================================================================
# 2. 工具描述和参数测试
# =============================================================================


class TestToolDescriptions:
    """工具描述和参数 schema 测试"""

    def test_transit_directions_tool_has_city_param(self):
        tool = TransitDirectionsTool()
        assert tool.name == "transit_directions"
        schema = tool.args_schema.model_json_schema()
        assert "city" in schema.get("properties", {})


# =============================================================================
# 3. 坐标参数分离测试
# =============================================================================


class TestCoordinateParams:
    """路线工具坐标参数使用 lat/lng 分离的 float"""

    @pytest.mark.parametrize(
        "tool_cls",
        [
            DrivingDirectionsTool,
            TransitDirectionsTool,
            WalkingDirectionsTool,
        ],
    )
    def test_should_have_separated_lat_lng_params(self, tool_cls):
        tool = tool_cls()
        schema = tool.args_schema.model_json_schema()
        props = schema.get("properties", {})

        assert "origin_lat" in props
        assert "origin_lng" in props
        assert "dest_lat" in props
        assert "dest_lng" in props

    def test_should_not_use_origin_string(self):
        tool = DrivingDirectionsTool()
        schema = tool.args_schema.model_json_schema()
        props = schema.get("properties", {})

        assert "origin" not in props
        assert "destination" not in props


# =============================================================================
# 4. is_available 测试
# =============================================================================


class TestIsAvailable:
    """工具可用性检查测试"""

    @pytest.mark.asyncio
    async def test_geocode_available_with_either_key(self):
        tool = GeocodeTool()
        with patch.dict(os.environ, {"TENCENT_MAPS_KEY": "test", "BAIDU_MAPS_AK": ""}):
            assert await tool.is_available() is True

        with patch.dict(os.environ, {"TENCENT_MAPS_KEY": "", "BAIDU_MAPS_AK": "test"}):
            assert await tool.is_available() is True

    @pytest.mark.asyncio
    async def test_district_search_requires_tencent_key(self):
        tool = DistrictSearchTool()
        with patch.dict(
            os.environ, {"TENCENT_MAPS_KEY": "test", "BAIDU_MAPS_AK": "test"}
        ):
            assert await tool.is_available() is True

        with patch.dict(os.environ, {"TENCENT_MAPS_KEY": "", "BAIDU_MAPS_AK": "test"}):
            assert await tool.is_available() is False

    @pytest.mark.asyncio
    async def test_traffic_requires_baidu_key(self):
        tool = TrafficTool()
        with patch.dict(os.environ, {"TENCENT_MAPS_KEY": "test", "BAIDU_MAPS_AK": ""}):
            assert await tool.is_available() is False

        with patch.dict(os.environ, {"BAIDU_MAPS_AK": "test", "TENCENT_MAPS_KEY": ""}):
            assert await tool.is_available() is True


# =============================================================================
# 5. _arun 调用测试 (mock unified client)
# =============================================================================


class TestArunCalls:
    """工具 _arun 方法调用正确的 unified client 函数"""

    @pytest.mark.asyncio
    async def test_geocode_calls_unified_geocode(self):
        tool = GeocodeTool()

        with patch(
            "src.tools.experts.geo_research.unified_geo_tools.unified.geocode",
            new_callable=AsyncMock,
            return_value={"lat": 39.9, "lng": 116.4},
        ) as mock_fn:
            result = await tool._arun(address="北京")

            mock_fn.assert_called_once_with("北京", city="")
            assert "39.9" in result

    @pytest.mark.asyncio
    async def test_driving_calls_unified_driving(self):
        tool = DrivingDirectionsTool()

        with patch(
            "src.tools.experts.geo_research.unified_geo_tools.unified.driving",
            new_callable=AsyncMock,
            return_value={"routes": [], "source": "tencent"},
        ) as mock_fn:
            await tool._arun(
                origin_lat=39.9,
                origin_lng=116.4,
                dest_lat=31.2,
                dest_lng=121.4,
            )

            mock_fn.assert_called_once_with(39.9, 116.4, 31.2, 121.4)

    @pytest.mark.asyncio
    async def test_traffic_calls_baidu_directly(self):
        tool = TrafficTool()

        with patch(
            "src.tools.experts.geo_research.unified_geo_tools.baidu.call_traffic_around",
            new_callable=AsyncMock,
            return_value={"description": "畅通"},
        ) as mock_fn:
            await tool._arun(lat=39.9, lng=116.4, radius=500)

            mock_fn.assert_called_once_with(39.9, 116.4, radius=500)
