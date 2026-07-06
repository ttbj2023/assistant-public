"""Token估算工具.

提供文本token数量估算功能,用于记忆管理和成本控制.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from src.core.cache import get_token_cache, is_token_cache_enabled

logger = logging.getLogger(__name__)

_CHINESE_TOKEN_RATIO = 1.5
_ENGLISH_TOKEN_RATIO = 0.25
_CODE_TOKEN_RATIO = 0.3

_chinese_pattern = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff]")

_default_estimator: TokenEstimator | None = None


class TokenEstimator:
    """Token估算器.

    支持基于统一缓存体系的高效token估算.
    """

    def __init__(self, enable_cache: bool = True, model_name: str = "default") -> None:
        self.enable_cache = enable_cache
        self.model_name = model_name
        self._cache = None

        if self.enable_cache:
            try:
                if is_token_cache_enabled():
                    self._cache = get_token_cache()
                    logger.debug("Token估算器缓存已启用: model=%s", model_name)
                else:
                    logger.debug("Token缓存已全局禁用")
            except Exception as e:
                logger.warning("Token缓存初始化失败,降级到无缓存模式: %s", e)
                self.enable_cache = False

    def estimate_tokens_instance(self, text: str) -> int:
        """使用启发式方法估算Token数量(实例方法)."""
        if not text:
            return 0

        if self.enable_cache and self._cache:
            try:
                cached_result = self._cache.get(f"{self.model_name}:{text}")
                if cached_result is not None:
                    return cached_result
            except Exception as e:
                logger.warning("Token缓存获取失败,使用直接计算: %s", e)

        token_count = _calculate_tokens(text)

        if self.enable_cache and self._cache:
            try:
                self._cache.set(f"{self.model_name}:{text}", token_count)
            except Exception as e:
                logger.warning("Token缓存存储失败: %s", e)

        return token_count

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """静态方法包装器,保持向后兼容性."""
        estimator = _get_default_estimator()
        return estimator.estimate_tokens_instance(text)

    def get_cache_stats(self) -> dict[str, Any] | None:
        """获取缓存统计信息."""
        if self.enable_cache and self._cache:
            try:
                return self._cache.get_stats()
            except Exception as e:
                logger.warning("获取Token缓存统计失败: %s", e)
        return None


def _calculate_tokens(text: str) -> int:
    """实际的token计算逻辑."""
    if not text:
        return 0
    base_estimate = max(1, math.ceil(len(text) / 3))
    chinese_ratio = _chinese_pattern.findall(text)
    chinese_chars = len(chinese_ratio)
    if len(text) > 0 and chinese_chars / len(text) > 0.3:
        return max(1, math.ceil(len(text) * _CHINESE_TOKEN_RATIO))
    return base_estimate


def _get_default_estimator() -> TokenEstimator:
    """获取默认的Token估算器实例."""
    global _default_estimator
    if _default_estimator is None:
        _default_estimator = TokenEstimator()
    return _default_estimator


def estimate_tokens(text: str) -> int:
    """估算token数量.

    Args:
        text: 输入文本

    Returns:
        估算的token数量

    """
    return _get_default_estimator().estimate_tokens(text)


__all__ = [
    "TokenEstimator",
    "estimate_tokens",
]
