"""WeatherQueryTool 单元测试.

测试天气查询工具的核心逻辑, Mock外部API调用.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.external.weather_tool import (
    WeatherQueryInput,
    WeatherQueryTool,
    _call_weather,
)


@pytest.fixture
def weather_tool():
    """创建天气查询工具实例."""
    return WeatherQueryTool()


class TestWeatherQueryToolAvailability:
    @pytest.mark.asyncio
    async def test_is_available_should_return_true_when_key_set(self):
        """API Key存在时应返回True."""
        tool = WeatherQueryTool()
        with patch.dict("os.environ", {"BAIDU_MAPS_AK": "test_api_key"}):
            result = await tool.is_available()
            assert result is True

    @pytest.mark.asyncio
    async def test_is_available_should_return_false_when_key_missing(self):
        """API Key不存在时应返回False."""
        tool = WeatherQueryTool()
        with patch.dict("os.environ", {"BAIDU_MAPS_AK": ""}, clear=False):
            result = await tool.is_available()
            assert result is False


class TestWeatherQueryToolRun:
    @pytest.mark.asyncio
    async def test_arun_should_return_formatted_weather(self, weather_tool):
        """正常天气数据应格式化为可读文本."""
        mock_result = {
            "location": {
                "province": "北京",
                "city": "北京市",
                "name": "海淀区",
            },
            "now": {
                "text": "晴",
                "temp": "25",
                "feels_like": "27",
                "rh": "45",
                "wind_dir": "北",
                "wind_class": "3级",
                "vis": "15000",
                "aqi": "52",
                "pm25": "35",
            },
            "forecasts": [
                {
                    "date": "2026-06-07",
                    "text_day": "晴",
                    "text_night": "多云",
                    "low": "18",
                    "high": "30",
                },
            ],
        }

        with patch(
            "src.tools.external.weather_tool._call_weather",
            return_value=mock_result,
        ):
            result = await weather_tool._arun(location="北京海淀")

        assert "海淀" in result
        assert "25" in result
        assert "晴" in result
        assert "预报" in result
        assert "18" in result

    @pytest.mark.asyncio
    async def test_arun_should_return_error_json_on_failure(self, weather_tool):
        """API错误时应返回错误JSON."""
        error_result = {"error": "API调用失败"}

        with patch(
            "src.tools.external.weather_tool._call_weather",
            return_value=error_result,
        ):
            result = await weather_tool._arun(location="不存在")

        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_arun_should_handle_empty_forecasts(self, weather_tool):
        """无预报数据时应只显示当前天气."""
        mock_result = {
            "location": {
                "province": "北京",
                "city": "北京市",
                "name": "朝阳区",
            },
            "now": {
                "text": "多云",
                "temp": "22",
                "feels_like": "23",
                "rh": "60",
                "wind_dir": "南",
                "wind_class": "2级",
                "vis": "10000",
                "aqi": "68",
                "pm25": "45",
            },
            "forecasts": [],
        }

        with patch(
            "src.tools.external.weather_tool._call_weather",
            return_value=mock_result,
        ):
            result = await weather_tool._arun(location="北京朝阳")

        assert "预报" not in result
        assert "多云" in result


class TestCallWeather:
    @pytest.mark.asyncio
    async def test_call_weather_should_use_coordinates(self):
        """逗号分隔的location应作为坐标传参."""
        with (
            patch(
                "src.tools.external.weather_tool.baidu_get",
                return_value={"status": 0, "result": {"now": {}, "forecasts": []}},
            ),
            patch(
                "src.tools.external.weather_tool.check_response",
                return_value={"error": "no data"},
            ),
            patch(
                "src.tools.external.weather_tool.get_expert_cache",
            ) as mock_cache_fn,
        ):
            mock_cache = AsyncMock()
            mock_cache.get_geo.return_value = None
            mock_cache_fn.return_value = mock_cache

            await _call_weather("116.40,39.90")

        mock_cache.get_geo.assert_called_once()
        call_args = mock_cache_fn.call_args

    @pytest.mark.asyncio
    async def test_call_weather_should_use_district_name(self):
        """城市名应作为district参数传参."""
        with (
            patch(
                "src.tools.external.weather_tool.baidu_get",
                return_value={
                    "status": 0,
                    "result": {
                        "now": {"text": "晴", "temp": "25"},
                        "forecasts": [],
                        "location": {"city": "北京"},
                    },
                },
            ),
            patch(
                "src.tools.external.weather_tool.check_response",
                return_value={
                    "result": {
                        "now": {"text": "晴", "temp": "25"},
                        "forecasts": [],
                        "location": {"city": "北京"},
                    },
                },
            ),
            patch(
                "src.tools.external.weather_tool.get_expert_cache",
            ) as mock_cache_fn,
        ):
            mock_cache = AsyncMock()
            mock_cache.get_geo.return_value = None
            mock_cache_fn.return_value = mock_cache

            result = await _call_weather("北京")

            assert "now" in result
            assert "location" in result

    @pytest.mark.asyncio
    async def test_call_weather_should_return_cached_data(self):
        """缓存命中时应直接返回缓存数据."""
        cached_data = json.dumps(
            {"now": {"text": "晴"}, "location": {"city": "上海"}},
            ensure_ascii=False,
        )

        with patch(
            "src.tools.external.weather_tool.get_expert_cache",
        ) as mock_cache_fn:
            mock_cache = AsyncMock()
            mock_cache.get_geo.return_value = cached_data
            mock_cache_fn.return_value = mock_cache

            result = await _call_weather("上海")

            assert result["now"]["text"] == "晴"
            mock_cache.get_geo.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_weather_should_cache_successful_result(self):
        """成功查询后应缓存结果."""
        formatted_result = {
            "result": {
                "now": {"text": "晴", "temp": "25"},
                "forecasts": [{"date": "2026-06-07"}],
                "location": {"city": "广州"},
            },
        }

        with (
            patch(
                "src.tools.external.weather_tool.baidu_get",
                return_value=formatted_result,
            ),
            patch(
                "src.tools.external.weather_tool.check_response",
                return_value=formatted_result,
            ),
            patch(
                "src.tools.external.weather_tool.get_expert_cache",
            ) as mock_cache_fn,
        ):
            mock_cache = AsyncMock()
            mock_cache.get_geo.return_value = None
            mock_cache_fn.return_value = mock_cache

            result = await _call_weather("广州")

            assert "now" in result
            assert len(result["forecasts"]) <= 3
            mock_cache.set_geo.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_weather_should_not_cache_error(self):
        """错误结果不应缓存."""
        with (
            patch(
                "src.tools.external.weather_tool.baidu_get",
                return_value={"status": 1},
            ),
            patch(
                "src.tools.external.weather_tool.check_response",
                return_value={"error": "查询失败"},
            ),
            patch(
                "src.tools.external.weather_tool.get_expert_cache",
            ) as mock_cache_fn,
        ):
            mock_cache = AsyncMock()
            mock_cache.get_geo.return_value = None
            mock_cache_fn.return_value = mock_cache

            result = await _call_weather("不存在的城市")

            assert "error" in result
            mock_cache.set_geo.assert_not_called()


class TestWeatherQueryInput:
    def test_input_model_should_reject_extra_fields(self):
        """输入模型应拒绝多余字段."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WeatherQueryInput(location="北京", extra_field="value")  # type: ignore[call-arg]

    def test_input_model_should_accept_location(self):
        """输入模型应接受location字段."""
        inp = WeatherQueryInput(location="上海浦东")
        assert inp.location == "上海浦东"
