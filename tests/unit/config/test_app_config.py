"""根配置 schema 测试."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config.app_config import AppConfig


def test_app_config_rejects_unknown_top_level_field() -> None:
    with pytest.raises(ValidationError):
        AppConfig(unknown={})


def test_app_config_rejects_deprecated_file_secret() -> None:
    with pytest.raises(ValidationError):
        AppConfig(api={"file_signing_secret": "secret"})
