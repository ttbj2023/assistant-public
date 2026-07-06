"""配置加载器单元测试.

覆盖 config_loader.py 的核心逻辑:
- load_base_config_sync (YAML加载, 文件不存在回退)
- get_module_config_sync (模块配置获取和缓存)
- clear_cache (缓存清理)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from src.config.config_loader import (
    clear_cache,
    get_module_config_sync,
    load_base_config_sync,
)


class TestLoadBaseConfigSync:
    """基础配置加载测试."""

    def teardown_method(self) -> None:
        clear_cache()

    def test_config_file_exists(self) -> None:
        config = load_base_config_sync()
        assert isinstance(config, dict)

    def test_config_file_not_exists(self, tmp_path: Path) -> None:
        with patch("src.config.config_loader.PROJECT_ROOT", tmp_path):
            config = load_base_config_sync()
            assert config == {}

    def test_config_file_empty(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")
        with patch("src.config.config_loader.PROJECT_ROOT", tmp_path):
            config = load_base_config_sync()
            assert config == {}

    def test_config_file_with_content(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        test_config = {"storage": {"db_type": "sqlite"}, "api": {"port": 8000}}
        config_file.write_text(yaml.dump(test_config))

        with patch("src.config.config_loader.PROJECT_ROOT", tmp_path):
            config = load_base_config_sync()
            assert config["storage"]["db_type"] == "sqlite"
            assert config["api"]["port"] == 8000

    def test_config_in_config_subdir(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"
        test_config = {"tools": {"internal_tools": {"create_todo": {}}}}
        config_file.write_text(yaml.dump(test_config))

        with patch("src.config.config_loader.PROJECT_ROOT", tmp_path):
            config = load_base_config_sync()
            assert "create_todo" in config["tools"]["internal_tools"]

    def test_root_config_takes_priority(self, tmp_path: Path) -> None:
        root_config = tmp_path / "config.yaml"
        root_config.write_text(yaml.dump({"priority": "root"}))

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        subdir_config = config_dir / "config.yaml"
        subdir_config.write_text(yaml.dump({"priority": "subdir"}))

        with patch("src.config.config_loader.PROJECT_ROOT", tmp_path):
            config = load_base_config_sync()
            assert config["priority"] == "root"


class TestGetModuleConfigSync:
    """模块配置获取测试."""

    def teardown_method(self) -> None:
        clear_cache()

    def test_returns_module_config(self) -> None:
        config = get_module_config_sync("storage")
        assert isinstance(config, dict)

    def test_missing_module_returns_empty(self) -> None:
        config = get_module_config_sync("nonexistent_module_xyz")
        assert config == {}

    def test_caching(self) -> None:
        config1 = get_module_config_sync("storage")
        config2 = get_module_config_sync("storage")
        assert config1 is config2


class TestClearCache:
    """缓存清理测试."""

    def test_clear_allows_reload(self) -> None:
        get_module_config_sync("storage")
        clear_cache()
        config = get_module_config_sync("storage")
        assert isinstance(config, dict)


class TestCachedModuleCoverage:
    """_cached 模块覆盖校验.

    防止新增配置模块采用 _cached 缓存却忘记在 _CACHED_MODULE_NAMES 注册,
    导致 reset_config_cache 漏清理而污染其他测试.
    """

    def test_all_cached_modules_registered(self) -> None:
        """所有使用 _cached 的配置模块必须注册到 _CACHED_MODULE_NAMES."""
        import importlib

        from src.config.config_loader import _CACHED_MODULE_NAMES

        config_dir = Path(__file__).resolve().parents[3] / "src" / "config"
        actual: set[str] = set()
        for py_file in config_dir.glob("*_config.py"):
            mod = importlib.import_module(f"src.config.{py_file.stem}")
            if hasattr(mod, "_cached"):
                actual.add(py_file.stem)

        registered = set(_CACHED_MODULE_NAMES)
        assert actual == registered, (
            f"_cached 模块与注册列表不一致: "
            f"未注册={actual - registered}, 多余注册={registered - actual}"
        )
