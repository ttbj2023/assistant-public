#!/usr/bin/env python3
"""基础配置类测试.

遵循项目单元测试设计规范：
- Mock外部依赖，保留内部业务逻辑
- 验证BaseConfig基类的通用功能
- 完全隔离，毫秒级执行
- 使用统一测试管理系统

测试重点：
- BaseConfig基类的工厂方法
- 配置对象的创建、验证和转换
- 向后兼容性功能
"""

from __future__ import annotations

import warnings
from typing import ClassVar
from unittest.mock import patch

import pytest
from pydantic import field_validator

from src.config.base_config import (
    BaseConfig,
)


# 创建测试用的配置类
class SampleConfig(BaseConfig):
    """测试用的配置类"""

    _module_name = "test"
    _default_config: ClassVar[dict[str, object]] = {
        "host": "localhost",
        "port": 8080,
        "debug": False,
    }

    host: str = "localhost"
    port: int = 8080
    debug: bool = False


class SampleConfigWithDefaults(BaseConfig):
    """测试用的带默认值配置类"""

    _module_name = "test_defaults"
    _default_config: ClassVar[dict[str, object]] = {
        "name": "test",
        "count": 5,
        "enabled": True,
    }

    name: str = "test"
    count: int = 5
    enabled: bool = True


class SampleConfigEmpty(BaseConfig):
    """测试用的空配置类"""

    _module_name = "test_empty"
    # 没有默认配置

    # 没有定义字段，测试动态字段处理
    pass


class TestBaseConfig:
    """BaseConfig基类测试.

    **测试职责**: 验证BaseConfig基类的核心功能
    **测试范围**: 工厂方法、配置转换、验证
    **Mock策略**: Mock配置加载器和模块配置
    **测试价值**: 确保基类功能正确且可继承
    """

    def test_get_module_name_with_defined_name(self) -> None:
        """测试获取定义的模块名称

        **测试目的**: 验证_get_module_name能正确返回定义的名称
        **业务价值**: 确保模块名称配置正确
        **验证要点**:
        1. 返回定义的模块名
        2. 模块名格式正确
        """
        module_name = SampleConfig.get_module_name()
        assert module_name == "test"

    def test_get_module_name_inferred(self) -> None:
        """测试推断模块名称

        **测试目的**: 验证从类名推断模块名称
        **业务价值**: 确保未定义模块名时能正确推断
        **验证要点**:
        1. 从类名正确推断
        2. Config后缀移除
        3. 大小写转换正确
        """

        # 创建一个没有定义_module_name的配置类
        class InferredConfig(BaseConfig):
            pass

        module_name = InferredConfig.get_module_name()
        assert module_name == "inferred"

    def test_get_default_config(self) -> None:
        """测试获取默认配置

        **测试目的**: 验证get_default_config返回正确默认值
        **业务价值**: 确保默认配置可用
        **验证要点**:
        1. 返回字典格式
        2. 包含所有默认值
        3. 返回副本而非引用
        """
        default_config = SampleConfig.get_default_config()

        assert isinstance(default_config, dict)
        assert default_config["host"] == "localhost"
        assert default_config["port"] == 8080
        assert default_config["debug"] is False

        # 修改返回值不应影响原始默认配置
        default_config["host"] = "modified"
        new_default_config = SampleConfig.get_default_config()
        assert new_default_config["host"] == "localhost"

    def test_from_dict_with_valid_config(self) -> None:
        """测试从字典创建配置对象

        **测试目的**: 验证from_dict方法正确创建配置对象
        **业务价值**: 确保配置对象可以从字典创建
        **验证要点**:
        1. 字典转换正确
        2. 默认值合并
        3. 类型验证
        """
        config_dict = {"host": "example.com", "port": 9000}
        config = SampleConfig.from_dict(config_dict)

        assert config.host == "example.com"  # 覆盖的值
        assert config.port == 9000  # 覆盖的值
        assert config.debug is False  # 默认值保持

    def test_from_dict_with_empty_dict(self) -> None:
        """测试从空字典创建配置对象

        **测试目的**: 验证from_dict处理空字典
        **业务价值**: 确保空字典使用默认配置
        **验证要点**:
        1. 空字典处理
        2. 默认配置应用
        """
        config = SampleConfig.from_dict({})

        assert config.host == "localhost"
        assert config.port == 8080
        assert config.debug is False

    @patch("src.config.config_loader.get_module_config_sync")
    def test_from_module_config(self, mock_get_module_config) -> None:
        """测试从模块配置创建配置对象

        **测试目的**: 验证from_module_config方法
        **业务价值**: 确保配置对象可以从模块配置创建
        **验证要点**:
        1. 调用get_module_config_sync
        2. 传递正确的模块名
        3. 配置合并正确
        """
        mock_get_module_config.return_value = {"host": "module.com", "debug": True}

        config = SampleConfig.from_module_config()

        assert config.host == "module.com"
        assert config.debug is True
        assert config.port == 8080  # 默认值保持
        mock_get_module_config.assert_called_once_with("test")

    def test_to_dict_conversion(self) -> None:
        """测试转换为字典

        **测试目的**: 验证to_dict方法
        **业务价值**: 确保配置对象能转换为字典
        **验证要点**:
        1. 返回字典格式
        2. 包含所有字段
        3. 值类型正确
        """
        config = SampleConfig(host="test.com", port=9000, debug=True)
        config_dict = config.to_dict()

        assert isinstance(config_dict, dict)
        assert config_dict["host"] == "test.com"
        assert config_dict["port"] == 9000
        assert config_dict["debug"] is True

    def test_get_method_dict_style_access(self) -> None:
        """测试字典式访问方法

        **测试目的**: 验证get方法支持字典式访问
        **业务价值**: 提供向后兼容性
        **验证要点**:
        1. 存在的键返回值
        2. 不存在的键返回默认值
        3. 默认值参数工作
        """
        config = SampleConfig(host="test.com", port=9000)

        assert config.get("host") == "test.com"
        assert config.get("port") == 9000
        assert config.get("nonexistent") is None
        assert config.get("nonexistent", "default") == "default"

    def test_update_method(self) -> None:
        """测试更新配置方法

        **测试目的**: 验证update方法
        **业务价值**: 支持配置对象的动态更新
        **验证要点**:
        1. 存在字段更新
        2. 不存在字段警告
        3. 返回更新后的对象
        """
        config = SampleConfig()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            updated_config = config.update(
                host="updated.com", port=9999, nonexistent="value"
            )

            # 验证存在的字段被更新
            assert updated_config.host == "updated.com"
            assert updated_config.port == 9999
            assert updated_config.debug is False

            # 验证不存在的字段产生警告
            assert len(w) == 1
            assert "不存在,将被忽略" in str(w[0].message)

    def test_string_stripping_validator(self) -> None:
        """测试字符串去除空格验证器

        **测试目的**: 验证字符串字段自动去除空格
        **业务价值**: 确保字符串值干净无多余空格
        **验证要点**:
        1. 前导空格去除
        2. 后导空格去除
        3. 中间空格保留
        """
        config = SampleConfig(host="  test.com  ")
        assert config.host == "test.com"

        # 直接用from_dict创建也会触发验证器
        config = SampleConfig.from_dict({"host": "  trimmed.com  "})
        assert config.host == "trimmed.com"


class TestBackwardCompatibility:
    """Pydantic 行为测试类."""

    def test_base_config_config_class(self) -> None:
        """测试BaseConfig的Config类设置

        **测试目的**: 验证Pydantic配置正确设置
        **业务价值**: 确保未知字段尽早暴露, 防止配置漂移
        **验证要点**:
        1. extra="forbid" 拒绝额外字段
        2. validate_assignment=True 赋值时验证
        3. 其他配置项正确
        """
        with pytest.raises(ValueError):
            SampleConfig(extra_field="should_be_rejected")

        # 测试赋值时验证 - 创建一个会导致验证失败的配置
        class SampleConfigWithValidation(BaseConfig):
            _module_name = "test_validation"

            port: int = 8080

            @field_validator("port")
            @classmethod
            def validate_port(cls, v):
                if v <= 0:
                    raise ValueError("Port must be positive")
                return v

        config = SampleConfigWithValidation()
        # 尝试设置无效值应该抛出异常
        with pytest.raises(ValueError, match="Port must be positive"):
            config.port = -1  # 无效端口值


# 测试标记
pytestmark_unit = pytest.mark.unit
