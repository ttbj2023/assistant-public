"""配置基类 - 模块配置对象管理.

配置体系 v2 明确区分应用配置,运行时环境和凭据:
- 应用配置: config.yaml + Pydantic 默认值
- 运行时环境: runtime_env.py 的显式白名单
- 凭据/密钥: credentials_registry.py / provider_registry.py

BaseConfig 只负责应用配置对象的构造和校验, 不再执行通用环境变量合并.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any, ClassVar, Self

from pydantic import BaseModel, field_validator

_logger = logging.getLogger(__name__)


class BaseConfig(BaseModel):
    """统一配置基类

    提供标准化的配置对象管理功能:
    - 与 config.yaml + Pydantic 默认值架构集成
    - 配置验证和类型安全
    - 工厂方法支持
    - 向后兼容性保证
    """

    class Config:
        """Pydantic配置"""

        extra = "forbid"  # 未声明字段应尽早暴露, 避免配置漂移
        validate_assignment = True  # 赋值时验证
        frozen = False  # 允许修改
        use_enum_values = True  # 使用枚举值
        str_strip_whitespace = True  # 去除字符串空格

    # 模块名称(子类必须重写)
    _module_name: ClassVar[str] = ""

    # 默认配置字典(子类可以重写)
    _default_config: ClassVar[dict[str, Any]] = {}

    @classmethod
    def get_module_name(cls) -> str:
        """获取模块名称"""
        if not cls._module_name:
            # 从类名推断模块名(如 APIConfig -> api)
            return cls.__name__.replace("Config", "").lower()
        return cls._module_name

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        """获取默认配置字典"""
        return cls._default_config.copy()

    @classmethod
    def from_dict(cls, config_dict: dict[str, Any]) -> Self:
        """从字典创建配置对象

        Args:
            config_dict: 配置字典

        Returns:
            配置对象实例

        """
        # 合并默认配置和提供的配置
        merged_config = cls.get_default_config()
        if config_dict is not None:
            merged_config.update(config_dict)

        return cls(**merged_config)

    @classmethod
    def from_module_config(cls) -> Self:
        """从 config.yaml 模块块创建配置对象.

        获取配置顺序: config.yaml > Pydantic 默认值. 运行时环境变量只允许
        显式白名单字段, 由对应模块自行调用 runtime_env.py 合并.
        子类可override此方法实现特殊合并逻辑(如ToolsConfig的增量合并).

        Returns:
            配置对象实例

        """
        from .config_loader import get_module_config_sync

        module_name = cls.get_module_name()
        yaml_config = get_module_config_sync(module_name) or {}
        return cls.from_dict(yaml_config)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典(向后兼容)

        Returns:
            配置字典

        """
        return self.model_dump()

    def get(self, key: str, default: Any = None) -> Any:
        """字典式访问(向后兼容)

        Args:
            key: 配置键
            default: 默认值

        Returns:
            配置值

        """
        return getattr(self, key, default)

    def update(self, **kwargs: Any) -> Self:
        """更新配置字段

        Args:
            **kwargs: 要更新的字段

        Returns:
            更新后的配置对象

        """
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                warnings.warn(
                    f"配置字段 '{key}' 不存在,将被忽略", UserWarning, stacklevel=2
                )
        return self

    @field_validator("*", mode="before")
    @classmethod
    def strip_strings(cls, v: Any) -> Any:
        """去除字符串空格"""
        if isinstance(v, str):
            return v.strip()
        return v
