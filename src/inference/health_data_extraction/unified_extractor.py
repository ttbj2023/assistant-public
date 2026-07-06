"""统一健康数据提取器 - 单次 LLM 调用完成检测+分类+转录.

通过项目标准 LLM 调用体系 (model_loader.create_llm) 调用模型.
只需在 config.yaml 中修改 model 即可切换模型.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, override

import yaml

from src.inference.llm.response_utils import content_to_text

logger = logging.getLogger(__name__)

_PROMPTS_PATH = Path(__file__).parent / "prompts" / "health_extraction.yaml"

_VALID_DATA_TYPES = {
    "meal_record",
    "food_product",
    "shopping_list",
    "weight_record",
    "workout_record",
    "medical_report",
}


def _load_prompt_template() -> str:
    """从 YAML 加载统一提取 prompt 模板."""
    if not _PROMPTS_PATH.exists():
        raise FileNotFoundError(f"Prompt 配置文件不存在: {_PROMPTS_PATH}")
    with Path(_PROMPTS_PATH).open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    template = config.get("unified_extraction", "")
    if not template:
        raise ValueError("YAML 中未找到 unified_extraction 模板")
    return template


def _get_model_config() -> dict[str, Any]:
    """从配置系统获取模型配置."""
    try:
        from src.config.inference_config import get_config

        inference = get_config()
        cfg = inference.health_data_extraction
        return {
            "model": cfg.model,
            "model_params": cfg.model_params,
            "timeout": cfg.timeout,
        }
    except Exception as e:
        logger.warning("健康数据提取配置获取失败, 使用兜底默认配置: %s", e)
        # 兜底值对齐 HealthDataExtractionConfig Field default
        return {
            "model": "ark-agent-plan:doubao-seed-2.0-mini",
            "model_params": {},
            "timeout": 60.0,
        }


class ExtractionResult:
    """单条提取结果."""

    def __init__(self, data_type: str, data: Any) -> None:
        self.data_type = data_type
        self.data = data

    @override
    def __repr__(self) -> str:
        return f"ExtractionResult(type={self.data_type})"


class UnifiedHealthExtractor:
    """统一健康数据提取器.

    通过项目标准 LLM 调用体系 (model_loader.create_llm) 调用模型,
    自动适配所有 provider.
    """

    def __init__(self) -> None:
        self.prompt_template = _load_prompt_template()
        config = _get_model_config()
        self.model_id = config["model"]
        self.model_params: dict[str, Any] = config.get("model_params", {})
        self.timeout = config["timeout"]
        logger.info(
            f"健康数据提取器初始化: model={self.model_id}, params={self.model_params}",
        )

    def is_available(self) -> bool:
        """检查提取器是否可用."""
        return bool(self.model_id)

    async def extract(
        self,
        user_message: str,
        current_date: str | None = None,
    ) -> list[ExtractionResult]:
        """从用户消息中提取所有健康数据.

        Args:
            user_message: 用户消息 (可能含图片描述文本)
            current_date: 当前日期字符串 (YYYY-MM-DD), None 则自动获取

        """
        if not self.is_available():
            logger.warning("健康数据提取器不可用, 跳过")
            return []

        if not current_date:
            current_date = datetime.now().strftime("%Y-%m-%d")

        prompt = self.prompt_template.replace("{user_message}", user_message).replace(
            "{current_date}",
            current_date,
        )

        try:
            raw_json = await self._call_llm(prompt)
            results = self._parse_response(raw_json)
            logger.info(f"健康数据提取完成: {len(results)} 条结果")
            return results
        except Exception as e:
            logger.error("健康数据提取失败: %s", e)
            return []

    async def _call_llm(self, prompt: str) -> dict[str, Any]:
        """通过项目标准 LLM 调用体系调用模型, 返回 JSON."""
        from langchain_core.messages import HumanMessage

        from src.inference.llm.model_loader import invoke_with_fallback
        from src.inference.usage import usage_source

        with usage_source("health_extraction"):
            response = await invoke_with_fallback(
                [HumanMessage(content=prompt)],
                self.model_id,
                self.model_params,
                fallback_kind="text",
                usage_tag="health_extraction",
            )

        content = response.content
        text_content = content_to_text(content)

        if not text_content:
            raise ValueError("LLM 返回为空")

        return json.loads(text_content)

    def _parse_response(self, raw: dict[str, Any]) -> list[ExtractionResult]:
        """解析 LLM 返回的 JSON 为 ExtractionResult 列表."""
        extractions = raw.get("extractions", [])
        if not isinstance(extractions, list):
            logger.warning("extractions 不是数组, 跳过")
            return []

        results: list[ExtractionResult] = []
        for item in extractions:
            if not isinstance(item, dict):
                continue
            data_type = item.get("data_type", "")
            data = item.get("data")
            if data_type not in _VALID_DATA_TYPES or data is None:
                logger.debug("跳过无效提取: type=%s", data_type)
                continue
            results.append(ExtractionResult(data_type=data_type, data=data))

        return results
