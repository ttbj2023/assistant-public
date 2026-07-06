"""tencent_maps_client 单元测试 - SK 签名 + API 调用.

测试范围:
1. _sign - SK 签名 (MD5 正确性, 参数排序无关性)
2. _check_response - 响应状态检查
3. call_geocoder / call_reverse_geocoder / call_district - API 调用 (mock)
4. call_driving / call_transit / call_walking - 路线规划 (mock)
5. call_place_search - POI 搜索 (mock)
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.experts.geo_research.tencent_maps_client import (
    _check_response,
    _sign,
    call_district,
    call_driving,
    call_geocoder,
    call_place_search,
    call_reverse_geocoder,
    call_transit,
    call_walking,
)

# =============================================================================
# 工具 fixtures
# =============================================================================


@pytest.fixture
def mock_cache():
    """Mock ExpertCache 避免测试间缓存污染."""
    cache = AsyncMock()
    cache.get_geo = AsyncMock(return_value=None)
    cache.set_geo = AsyncMock()
    with patch(
        "src.tools.experts.geo_research.tencent_maps_client.get_expert_cache",
        return_value=cache,
    ):
        yield cache


@pytest.fixture
def mock_tencent_get():
    """Mock _tencent_get 返回指定数据."""
    with patch(
        "src.tools.experts.geo_research.tencent_maps_client._tencent_get"
    ) as mock:
        mock.return_value = {"status": 0, "result": {}}
        yield mock


# =============================================================================
# 1. _sign 测试
# =============================================================================


class TestSign:
    """SK 签名测试"""

    def test_should_return_uppercase_md5_hex(self):
        sig = _sign(
            "/ws/geocoder/v1/",
            {"address": "test", "key": "mykey"},
            "mysk",
        )
        assert len(sig) == 32
        assert sig == sig.upper()

    def test_should_match_manual_calculation(self):
        path = "/ws/geocoder/v1/"
        params = {"address": "北京", "key": "mykey"}
        sk = "mysk"

        sorted_items = sorted(params.items())
        query = "&".join(f"{k}={v}" for k, v in sorted_items)
        raw = f"{path}?{query}{sk}"
        expected = hashlib.md5(raw.encode()).hexdigest().upper()

        assert _sign(path, params, sk) == expected

    def test_should_be_independent_of_param_order(self):
        params1 = {"address": "test", "key": "mykey"}
        params2 = {"key": "mykey", "address": "test"}

        assert _sign("/ws/test/", params1, "sk") == _sign("/ws/test/", params2, "sk")

    def test_should_change_with_different_sk(self):
        params = {"key": "mykey", "address": "test"}
        assert _sign("/ws/test/", params, "sk1") != _sign("/ws/test/", params, "sk2")


# =============================================================================
# 2. _check_response 测试
# =============================================================================


class TestCheckResponse:
    """响应状态检查测试"""

    def test_should_pass_when_status_zero(self):
        data = {"status": 0, "result": {"location": {"lat": 39.9, "lng": 116.4}}}
        assert _check_response(data) == data

    def test_should_return_error_when_status_nonzero(self):
        data = {"status": 121, "message": "配额超限"}
        result = _check_response(data)
        assert "error" in result
        assert "status=121" in result["error"]


# =============================================================================
# 3. API 调用测试
# =============================================================================


class TestCallGeocoder:
    """地理编码测试"""

    @pytest.mark.asyncio
    async def test_should_return_formatted_result(self, mock_cache, mock_tencent_get):
        mock_tencent_get.return_value = {
            "status": 0,
            "result": {
                "location": {"lat": 39.914, "lng": 116.403},
                "formatted_address": "北京市海淀区",
            },
        }

        result = await call_geocoder("北京海淀")

        assert result["lat"] == 39.914
        assert result["lng"] == 116.403
        assert result["formatted_address"] == "北京市海淀区"
        assert result["source"] == "tencent"
        mock_cache.set_geo.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_return_error_on_api_failure(
        self, mock_cache, mock_tencent_get
    ):
        mock_tencent_get.return_value = {"error": "腾讯API调用失败: timeout"}

        result = await call_geocoder("北京")

        assert "error" in result


class TestCallReverseGeocoder:
    """逆地理编码测试"""

    @pytest.mark.asyncio
    async def test_should_return_formatted_result(self, mock_cache, mock_tencent_get):
        mock_tencent_get.return_value = {
            "status": 0,
            "result": {
                "formatted_address": "北京市海淀区",
                "address_component": {"city": "北京市"},
            },
        }

        result = await call_reverse_geocoder(39.914, 116.403)

        assert result["formatted_address"] == "北京市海淀区"
        assert result["source"] == "tencent"


class TestCallDistrict:
    """行政区划查询测试"""

    @pytest.mark.asyncio
    async def test_should_return_districts_list(self, mock_cache, mock_tencent_get):
        mock_tencent_get.return_value = {
            "status": 0,
            "result": [
                {
                    "id": "3205",
                    "name": "苏州",
                    "fullname": "苏州市",
                    "location": {"lat": 31.299, "lng": 120.585},
                    "cidx": [320501, 320502],
                }
            ],
        }

        result = await call_district("苏州")

        assert len(result["districts"]) == 1
        assert result["districts"][0]["fullname"] == "苏州市"
        assert result["districts"][0]["lat"] == 31.299
        assert result["districts"][0]["cidx"] == [320501, 320502]
        assert result["source"] == "tencent"


class TestCallPlaceSearch:
    """POI 搜索测试"""

    @pytest.mark.asyncio
    async def test_should_return_places_with_region(self, mock_cache, mock_tencent_get):
        mock_tencent_get.return_value = {
            "status": 0,
            "data": [
                {
                    "id": "1",
                    "title": "故宫博物院",
                    "address": "景山前街4号",
                    "location": {"lat": 39.916, "lng": 116.397},
                    "_distance": 500,
                    "category": "博物馆",
                }
            ],
        }

        result = await call_place_search("博物馆", region="北京")

        assert len(result["places"]) == 1
        assert result["places"][0]["name"] == "故宫博物院"
        assert result["places"][0]["distance"] == 500

    @pytest.mark.asyncio
    async def test_should_use_nearby_boundary_with_lat_lng(
        self, mock_cache, mock_tencent_get
    ):
        mock_tencent_get.return_value = {"status": 0, "data": []}

        await call_place_search("咖啡", lat=39.914, lng=116.403, radius=2000)

        call_args = mock_tencent_get.call_args
        params = call_args[0][1]
        assert "nearby(39.914,116.403,2000)" in params["boundary"]


class TestCallDriving:
    """驾车路线规划测试"""

    @pytest.mark.asyncio
    async def test_should_return_route_with_toll(self, mock_cache, mock_tencent_get):
        mock_tencent_get.return_value = {
            "status": 0,
            "result": {
                "routes": [
                    {
                        "distance": 1308723,
                        "duration": 88208,
                        "toll": 595,
                        "steps": [
                            {
                                "instruction": "向东行驶",
                                "distance": 500,
                                "duration": 60,
                            }
                        ],
                    }
                ]
            },
        }

        result = await call_driving(39.914, 116.403, 31.230, 121.473)

        assert result["routes"][0]["distance"] == 1308723
        assert result["routes"][0]["toll"] == 595
        assert result["mode"] == "driving"
        assert result["source"] == "tencent"


class TestCallTransit:
    """公交路线规划测试"""

    @pytest.mark.asyncio
    async def test_should_require_city_param(self, mock_cache, mock_tencent_get):
        mock_tencent_get.return_value = {
            "status": 0,
            "result": {
                "routes": [
                    {
                        "distance": 25000,
                        "duration": 6300,
                        "steps": [],
                    }
                ]
            },
        }

        result = await call_transit(39.895, 116.322, 40.050, 116.608, "北京")

        assert result["mode"] == "transit"
        call_args = mock_tencent_get.call_args
        params = call_args[0][1]
        assert params["city"] == "北京"


class TestCallWalking:
    """步行路线规划测试"""

    @pytest.mark.asyncio
    async def test_should_return_walking_route(self, mock_cache, mock_tencent_get):
        mock_tencent_get.return_value = {
            "status": 0,
            "result": {
                "routes": [
                    {
                        "distance": 5800,
                        "duration": 5360,
                        "steps": [],
                    }
                ]
            },
        }

        result = await call_walking(39.999, 116.273, 39.968, 116.323)

        assert result["routes"][0]["distance"] == 5800
        assert result["mode"] == "walking"
