"""unified_geo_client 单元测试 - fallback 逻辑.

测试范围:
1. _is_retryable_error - HTTP 错误 vs 业务错误判断
2. geocode - 腾讯成功/腾讯HTTP失败切换百度/业务错误不切换
3. district - 腾讯独占无 fallback
4. driving/transit/walking - 坐标格式适配
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.tools.experts.geo_research import unified_geo_client as unified

# =============================================================================
# 1. _is_retryable_error 测试
# =============================================================================


class TestIsRetryableError:
    """fallback 触发条件测试"""

    def test_should_return_false_for_no_error(self):
        assert unified._is_retryable_error({"lat": 39.9, "lng": 116.4}) is False

    def test_should_return_false_for_empty_error(self):
        assert unified._is_retryable_error({"error": ""}) is False

    def test_should_return_true_for_http_error(self):
        assert (
            unified._is_retryable_error({"error": "腾讯API调用失败: timeout"}) is True
        )

    def test_should_return_true_for_429(self):
        assert unified._is_retryable_error({"error": "腾讯API调用失败: 429"}) is True

    def test_should_return_false_for_business_error(self):
        assert (
            unified._is_retryable_error({"error": "腾讯API返回错误: status=121"})
            is False
        )

    def test_should_return_false_for_business_error_zero(self):
        assert unified._is_retryable_error({"error": "status=310 参数错误"}) is False


# =============================================================================
# 2. geocode fallback 测试
# =============================================================================


class TestGeocodeFallback:
    """地理编码 fallback 测试"""

    @pytest.mark.asyncio
    async def test_should_return_tencent_on_success(self):
        tencent_result = {"lat": 39.9, "lng": 116.4, "source": "tencent"}

        with patch.object(
            unified.tencent, "call_geocoder", new_callable=AsyncMock
        ) as mock_t:
            mock_t.return_value = tencent_result

            result = await unified.geocode("北京")

            assert result == tencent_result
            assert result["source"] == "tencent"

    @pytest.mark.asyncio
    async def test_should_fallback_to_baidu_on_http_error(self):
        tencent_error = {"error": "腾讯API调用失败: timeout"}
        baidu_result = {"lat": 39.9, "lng": 116.4, "precise": 1}

        with (
            patch.object(
                unified.tencent, "call_geocoder", new_callable=AsyncMock
            ) as mock_t,
            patch.object(
                unified.baidu, "call_geocode", new_callable=AsyncMock
            ) as mock_b,
        ):
            mock_t.return_value = tencent_error
            mock_b.return_value = baidu_result

            result = await unified.geocode("北京")

            assert result["lat"] == 39.9
            assert result["source"] == "baidu"
            mock_b.assert_called_once_with("北京", city="")

    @pytest.mark.asyncio
    async def test_should_not_fallback_on_business_error(self):
        tencent_error = {"error": "腾讯API返回错误: status=121, msg=配额超限"}

        with (
            patch.object(
                unified.tencent, "call_geocoder", new_callable=AsyncMock
            ) as mock_t,
            patch.object(
                unified.baidu, "call_geocode", new_callable=AsyncMock
            ) as mock_b,
        ):
            mock_t.return_value = tencent_error

            result = await unified.geocode("北京")

            assert "error" in result
            assert "status=121" in result["error"]
            mock_b.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_return_tencent_error_when_baidu_also_fails(self):
        tencent_error = {"error": "腾讯API调用失败: 429"}
        baidu_error = {"error": "百度API调用失败: timeout"}

        with (
            patch.object(
                unified.tencent, "call_geocoder", new_callable=AsyncMock
            ) as mock_t,
            patch.object(
                unified.baidu, "call_geocode", new_callable=AsyncMock
            ) as mock_b,
        ):
            mock_t.return_value = tencent_error
            mock_b.return_value = baidu_error

            result = await unified.geocode("北京")

            assert "error" in result


# =============================================================================
# 3. district (腾讯独占) 测试
# =============================================================================


class TestDistrictNoFallback:
    """行政区划查询无 fallback 测试"""

    @pytest.mark.asyncio
    async def test_should_only_call_tencent(self):
        tencent_result = {"districts": [{"name": "姑苏区"}], "source": "tencent"}

        with (
            patch.object(
                unified.tencent, "call_district", new_callable=AsyncMock
            ) as mock_t,
            patch.object(
                unified.baidu, "call_geocode", new_callable=AsyncMock
            ) as mock_b,
        ):
            mock_t.return_value = tencent_result

            result = await unified.district("苏州")

            assert result == tencent_result
            mock_b.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_return_error_directly_on_failure(self):
        error = {"error": "腾讯API返回错误: status=121"}

        with patch.object(
            unified.tencent, "call_district", new_callable=AsyncMock
        ) as mock_t:
            mock_t.return_value = error

            result = await unified.district("苏州")

            assert "error" in result


# =============================================================================
# 4. 路线规划 fallback 测试
# =============================================================================


class TestDrivingFallback:
    """驾车路线规划 fallback 测试"""

    @pytest.mark.asyncio
    async def test_should_fallback_to_baidu_with_string_coords(self):
        tencent_error = {"error": "腾讯API调用失败: 502"}
        baidu_result = {
            "routes": [{"distance": 1308000, "duration": 88000}],
            "mode": "driving",
        }

        with (
            patch.object(
                unified.tencent, "call_driving", new_callable=AsyncMock
            ) as mock_t,
            patch.object(
                unified.baidu, "call_directions", new_callable=AsyncMock
            ) as mock_b,
        ):
            mock_t.return_value = tencent_error
            mock_b.return_value = baidu_result

            result = await unified.driving(39.914, 116.403, 31.230, 121.473)

            assert result["source"] == "baidu"
            mock_b.assert_called_once_with(
                "39.914,116.403", "31.23,121.473", mode="driving"
            )


class TestTransitFallback:
    """公交路线规划 fallback 测试"""

    @pytest.mark.asyncio
    async def test_should_pass_city_param_to_baidu(self):
        tencent_error = {"error": "腾讯API调用失败: timeout"}
        baidu_result = {"routes": [], "mode": "transit"}

        with (
            patch.object(
                unified.tencent, "call_transit", new_callable=AsyncMock
            ) as mock_t,
            patch.object(
                unified.baidu, "call_directions", new_callable=AsyncMock
            ) as mock_b,
        ):
            mock_t.return_value = tencent_error
            mock_b.return_value = baidu_result

            result = await unified.transit(39.9, 116.4, 40.0, 116.6, "北京")

            assert result["source"] == "baidu"
            mock_b.assert_called_once_with("39.9,116.4", "40.0,116.6", mode="transit")


class TestWalkingFallback:
    """步行路线规划 fallback 测试"""

    @pytest.mark.asyncio
    async def test_should_fallback_on_http_error(self):
        tencent_error = {"error": "腾讯API调用失败: 503"}
        baidu_result = {
            "routes": [{"distance": 5800, "duration": 5360}],
            "mode": "walking",
        }

        with (
            patch.object(
                unified.tencent, "call_walking", new_callable=AsyncMock
            ) as mock_t,
            patch.object(
                unified.baidu, "call_directions", new_callable=AsyncMock
            ) as mock_b,
        ):
            mock_t.return_value = tencent_error
            mock_b.return_value = baidu_result

            result = await unified.walking(39.999, 116.273, 39.968, 116.323)

            assert result["source"] == "baidu"
            mock_b.assert_called_once_with(
                "39.999,116.273", "39.968,116.323", mode="walking"
            )


# =============================================================================
# 5. place_search fallback 测试
# =============================================================================


class TestPlaceSearchFallback:
    """POI 搜索 fallback 测试"""

    @pytest.mark.asyncio
    async def test_should_fallback_to_baidu_on_http_error(self):
        tencent_error = {"error": "腾讯API调用失败: 429"}
        baidu_result = {"places": [{"name": "故宫博物院"}]}

        with (
            patch.object(
                unified.tencent, "call_place_search", new_callable=AsyncMock
            ) as mock_t,
            patch.object(
                unified.baidu, "call_place_search", new_callable=AsyncMock
            ) as mock_b,
        ):
            mock_t.return_value = tencent_error
            mock_b.return_value = baidu_result

            result = await unified.place_search("博物馆", region="北京")

            assert result["source"] == "baidu"
