"""统一认证数据模型

整合所有认证相关的数据模型,包括用户模型,API密钥模型和认证上下文模型.
与现有配置系统保持兼容,使用Pydantic进行严格的数据验证和序列化.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.validation import IDValidator


class UserStatus(StrEnum):
    """用户状态枚举"""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING = "pending"


class ApiKeyInfo(BaseModel):
    """API密钥信息模型"""

    api_key: str = Field(
        description="API密钥,格式: sk-project-{user_hash}-{thread_hash}-{random_suffix}",
    )
    thread_id: str = Field(description="线程ID,用于数据隔离")
    description: str = Field(default="", description="API密钥用途描述")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="创建时间",
    )
    last_used_at: datetime | None = Field(default=None, description="最后使用时间")
    usage_count: int = Field(default=0, ge=0, description="使用次数统计")
    expires_at: datetime | None = Field(default=None, description="过期时间")
    is_active: bool = Field(default=True, description="是否启用")
    key_name: str | None = Field(default=None, description="API密钥名称")

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("api_key")
    @classmethod
    def validate_api_key_format(cls, v: str) -> str:
        """验证API密钥格式"""
        if not v.startswith("sk-project-"):
            raise ValueError('API密钥必须以 "sk-project-" 开头')

        parts = v.split("-")
        if len(parts) < 4:
            raise ValueError("API密钥格式无效,应包含至少4个部分")

        return v

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, v: str) -> str:
        """验证线程ID格式"""
        return IDValidator.validate_thread_id(v)


class StaticUser(BaseModel):
    """静态用户模型"""

    model_config = ConfigDict(validate_assignment=True, str_strip_whitespace=True)

    user_id: str = Field(description="用户唯一标识符")
    display_name: str = Field(description="用户显示名称")
    email: str | None = Field(default=None, description="用户邮箱地址")
    timezone: str = Field(
        default="Asia/Shanghai",
        description="用户时区, IANA时区标识符",
    )
    status: UserStatus = Field(default=UserStatus.ACTIVE, description="用户状态")
    threads: list[ApiKeyInfo] = Field(
        default_factory=list,
        description="用户线程和API密钥列表",
    )

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, v: str) -> str:
        """验证用户ID格式"""
        return IDValidator.validate_user_id(v)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: str) -> str:
        """验证显示名称"""
        if not v or not v.strip():
            raise ValueError("显示名称不能为空")

        if len(v.strip()) > 100:
            raise ValueError("显示名称长度不能超过100个字符")

        return v.strip()

    def get_api_key_info(self, api_key: str) -> ApiKeyInfo | None:
        """根据API密钥获取对应的API密钥信息"""
        for thread_info in self.threads:
            if thread_info.api_key == api_key:
                return thread_info
        return None

    def get_thread_ids(self) -> list[str]:
        """获取用户的所有线程ID列表"""
        return [thread.thread_id for thread in self.threads]

    def is_active(self) -> bool:
        """检查用户是否处于活跃状态"""
        return self.status == UserStatus.ACTIVE

    def update_usage(self, api_key: str) -> bool:
        """更新API密钥使用统计"""
        for thread_info in self.threads:
            if thread_info.api_key == api_key:
                thread_info.last_used_at = datetime.now(UTC)
                thread_info.usage_count += 1
                return True
        return False

    def is_api_key_expired(self, api_key: str) -> bool:
        """检查API密钥是否过期"""
        for thread_info in self.threads:
            if thread_info.api_key == api_key:
                if not thread_info.is_active:
                    return True
                return bool(
                    thread_info.expires_at
                    and datetime.now(UTC) > thread_info.expires_at,
                )
        return True  # API密钥不存在也视为过期

    def add_api_key(self, api_key_info: ApiKeyInfo) -> bool:
        """添加API密钥"""
        # 检查线程ID是否已存在
        for existing_thread in self.threads:
            if existing_thread.thread_id == api_key_info.thread_id:
                # 更新现有的API密钥
                existing_thread.api_key = api_key_info.api_key
                existing_thread.description = api_key_info.description
                existing_thread.expires_at = api_key_info.expires_at
                existing_thread.is_active = api_key_info.is_active
                existing_thread.key_name = api_key_info.key_name
                return True

        # 检查API密钥是否已存在
        for existing_thread in self.threads:
            if existing_thread.api_key == api_key_info.api_key:
                return False  # API密钥已存在

        # 添加新的API密钥
        self.threads.append(api_key_info)
        return True

    def deactivate_api_key(self, api_key: str) -> bool:
        """停用API密钥"""
        for thread_info in self.threads:
            if thread_info.api_key == api_key:
                thread_info.is_active = False
                return True
        return False


class StaticUserConfig(BaseModel):
    """静态用户配置模型"""

    users: dict[str, StaticUser] = Field(
        default_factory=dict,
        description="用户字典,key为user_id",
    )

    def get_user_by_api_key(self, api_key: str) -> tuple[StaticUser, ApiKeyInfo] | None:
        """根据API密钥查找对应用户和密钥信息"""
        for user in self.users.values():
            api_key_info = user.get_api_key_info(api_key)
            if api_key_info:
                return user, api_key_info
        return None

    def get_user_by_user_id(self, user_id: str) -> StaticUser | None:
        """根据用户ID查找用户"""
        return self.users.get(user_id)

    def get_all_api_keys(self) -> dict[str, tuple[str, str]]:
        """获取所有API密钥映射 {api_key: (user_id, thread_id)}"""
        api_key_map = {}
        for user_id, user in self.users.items():
            for thread_info in user.threads:
                api_key_map[thread_info.api_key] = (user_id, thread_info.thread_id)
        return api_key_map

    def validate_api_key(self, api_key: str) -> tuple[StaticUser, ApiKeyInfo] | None:
        """验证API密钥并返回用户和密钥信息"""
        result = self.get_user_by_api_key(api_key)
        if result:
            user, api_key_info = result

            # 检查用户状态
            if not user.is_active():
                return None

            # 检查API密钥是否过期
            if user.is_api_key_expired(api_key):
                return None

            # 更新使用统计
            user.update_usage(api_key)

            return user, api_key_info

        return None


class AuthUser(BaseModel):
    """认证用户信息模型"""

    user_id: str = Field(description="用户ID")
    thread_id: str = Field(description="线程ID")
    display_name: str = Field(description="用户显示名称")
    permissions: list[str] = Field(default_factory=list, description="用户权限列表")
    api_key: str = Field(description="API密钥")

    def has_permission(self, permission: str) -> bool:
        """检查用户是否有指定权限"""
        return permission in self.permissions


class AuthContext(BaseModel):
    """认证上下文模型"""

    user: AuthUser = Field(description="认证用户信息")
    request_id: str | None = Field(default=None, description="请求ID")

    def get_user_id(self) -> str:
        """获取用户ID"""
        return self.user.user_id

    def get_thread_id(self) -> str:
        """获取线程ID"""
        return self.user.thread_id


class AuthRequest(BaseModel):
    """认证请求模型"""

    api_key: str = Field(description="API密钥")
    user_id: str | None = Field(default=None, description="用户ID")
    thread_id: str | None = Field(default=None, description="线程ID")
    permissions: list[str] = Field(default_factory=list, description="请求权限列表")


class AuthResponse(BaseModel):
    """认证响应模型"""

    success: bool = Field(description="认证是否成功")
    user: AuthUser | None = Field(default=None, description="用户信息")
    error_message: str | None = Field(default=None, description="错误信息")
    error_code: str | None = Field(default=None, description="错误代码")


# 导出所有模型
__all__ = [
    "ApiKeyInfo",
    "AuthContext",
    "AuthRequest",
    "AuthResponse",
    "AuthUser",
    "StaticUser",
    "StaticUserConfig",
    "UserStatus",
]
