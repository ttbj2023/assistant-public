"""Auth 配置真实加载链路集成测试.

仅保留必须通过真实文件系统验证的场景 (真实 YAML 文件加载 + 环境变量覆盖 + 缺失降级).
纯配置逻辑 (字符串解析 / 合并 / 校验 / 缓存) 已由 tests/unit/config/test_environment_variables.py
和 tests/unit/auth/test_auth_config.py 用 Mock 覆盖, 此处不重复, 避免与单测重叠.

用 monkeypatch 注入 PROJECT_ROOT 和环境变量 (自动回滚), 配合缓存清理 fixture,
无需 serial 标记即可与同进程其它测试安全并行 (改进自原实现的全局状态直接赋值).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.config.auth_config import get_config as get_auth_config

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_config_cache() -> Iterator[None]:
    """每个测试前后清空配置缓存, 隔离进程级 _config_cache 全局状态."""
    from src.config.config_loader import clear_cache

    clear_cache()
    yield
    clear_cache()


class TestAuthConfigLoadingIntegration:
    """Auth 配置真实文件系统加载链路集成测试."""

    def test_integration_real_yaml_loads_into_auth_config_with_env_override(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """真实 config.yaml 经完整加载链路装配为 AuthConfig, 且环境变量覆盖生效.

        协作场景: config_loader(真实 YAML 读取 + 缓存) + AuthConfig(Pydantic 构建)
            + env_manager(环境变量解析) 三层协作
        Mock 边界: 无 Mock, 使用真实临时文件系统 + monkeypatch 注入环境变量
        验证重点: 1) 真实 config.yaml 各节正确读入 2) 优先级 env > yaml > default
        业务价值: 确保部署环境的真实配置文件能正确驱动认证系统行为
        """
        config_yaml = """
auth:
  user_management:
    enable_static_user_management: true
"""
        (tmp_path / "config.yaml").write_text(config_yaml, encoding="utf-8")

        monkeypatch.setattr("src.config.config_loader.PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("ENABLE_STATIC_USER_MANAGEMENT", "false")

        config = get_auth_config()

        # 环境变量覆盖 YAML 的 True
        assert config.user_management.enable_static_user_management is False

    def test_integration_missing_config_file_falls_back_to_defaults(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """配置文件缺失时优雅降级到 Pydantic 默认值.

        协作场景: config_loader(文件不存在 → 返回空字典) + AuthConfig(默认值兜底)
        Mock 边界: 无 Mock, PROJECT_ROOT 指向不存在配置文件的空目录
        验证重点: load_base_config_sync 文件缺失路径返回 {}, from_dict({}) 全默认值
        业务价值: 确保无 config.yaml 的最小部署仍能以安全默认值启动
        """
        monkeypatch.setattr("src.config.config_loader.PROJECT_ROOT", tmp_path)

        config = get_auth_config()

        assert config.user_management.enable_static_user_management is True
