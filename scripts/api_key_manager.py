#!/usr/bin/env python3
"""
统一API密钥管理工具 - 基于新的认证模块重构

轻量级静态用户认证体系的API密钥管理工具.
与现有命令行接口保持完全兼容,支持用户管理、API密钥生成、验证和配置管理.

注: 静态用户的写操作(创建/撤销API密钥、序列化保存配置)由本脚本承担,
src/auth/static_user_manager.py 仅保留读/验证路径.

更新日志:
- 2025-12-16: 修复与当前静态用户管理系统实现的不一致问题
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiofiles
import yaml


# 计算项目根目录（增强版本，支持多种环境变量）
def get_project_root() -> Path:
    """获取项目根目录路径，支持多种方式指定路径."""

    # 1. 优先使用PROJECT_ROOT环境变量（由e2e测试设置）
    if "PROJECT_ROOT" in os.environ:
        root_path = Path(os.environ["PROJECT_ROOT"]).resolve()
        if (root_path / "scripts" / "api_key_manager.py").exists():
            return root_path
        print(f"⚠️ PROJECT_ROOT环境变量指向的路径不正确: {root_path}")

    # 2. 使用脚本位置计算
    script_root = Path(__file__).parent.parent.resolve()
    if (script_root / "scripts" / "api_key_manager.py").exists():
        return script_root

    # 3. 使用当前工作目录
    cwd_root = Path.cwd()
    if (cwd_root / "scripts" / "api_key_manager.py").exists():
        return cwd_root

    # 4. 最后尝试当前目录的父目录
    parent_root = Path.cwd().parent
    if (parent_root / "scripts" / "api_key_manager.py").exists():
        return parent_root

    # 如果都失败了，返回脚本位置作为默认值
    print("⚠️ 无法自动确定项目根目录，使用脚本位置作为默认值")
    return script_root


# 获取项目根目录
project_root = get_project_root()

# 添加项目根目录到Python路径
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from src.auth import AuthManager, StaticUserManager, UserStatus
    from src.auth.models import ApiKeyInfo, StaticUser
    from src.core.validation.unified_sanitizer import UnifiedSanitizer
except ImportError as e:
    print(f"❌ 无法导入认证模块: {e}")
    print(f"项目根目录: {project_root}")
    print(f"当前工作目录: {os.getcwd()}")
    print(f"Python路径: {sys.path[:3]}...")
    print("请确保在项目根目录下运行此脚本")
    sys.exit(1)

if TYPE_CHECKING:
    from src.auth.models import StaticUserConfig


# =============================================================================
# 写操作(从 src/auth/static_user_manager.py 迁入, 仅本 CLI 使用)
# =============================================================================


def _validate_identifier(identifier: str, identifier_type: str = "标识符") -> None:
    """验证标识符安全性."""
    if not identifier:
        raise ValueError(f"{identifier_type}不能为空")

    if len(identifier) > 200:
        raise ValueError(f"{identifier_type}长度不能超过200个字符")

    try:
        UnifiedSanitizer.quick_security_check(identifier)
    except Exception as e:
        raise ValueError(f"{identifier_type}包含不安全内容: {e}") from e


def _hash_identifier(identifier: str) -> str:
    """哈希化标识符, 用于生成API Key."""
    safe_id = re.sub(r"[^\w\-_.@]", "_", identifier)
    if safe_id and safe_id[0].isdigit():
        safe_id = f"id_{safe_id}"
    safe_id = safe_id[:100] or "default"
    return hashlib.sha256(safe_id.encode("utf-8")).hexdigest()[:12]


def generate_api_key(user_id: str, thread_id: str, length: int = 12) -> str:
    """为指定用户和线程生成API密钥(不写入配置)."""
    _validate_identifier(user_id, "用户ID")
    _validate_identifier(thread_id, "线程ID")
    user_hash = _hash_identifier(user_id)
    thread_hash = _hash_identifier(thread_id)
    random_suffix = secrets.token_urlsafe(length)
    return f"sk-project-{user_hash}-{thread_hash}-{random_suffix}"


def create_default_user(
    user_id: str,
    display_name: str,
    email: str | None = None,
    status: UserStatus = UserStatus.ACTIVE,
) -> dict[str, Any]:
    """创建默认用户配置(原 UserConfigHelper.create_default_user)."""
    return {
        "user_id": user_id,
        "display_name": display_name,
        "email": email,
        "status": status.value,
        "threads": [],
    }


def _serialize_config(config: StaticUserConfig) -> dict[str, Any]:
    """序列化配置为可YAML化的字典格式."""
    config_data: dict[str, Any] = {"users": {}}

    for user_id, user in config.users.items():
        user_data: dict[str, Any] = {
            "user_id": user.user_id,
            "display_name": user.display_name,
            "email": user.email,
            "status": user.status.value if hasattr(user.status, "value") else str(user.status),
            "threads": [],
        }

        for thread in user.threads:
            thread_data = {
                "api_key": thread.api_key,
                "thread_id": thread.thread_id,
                "description": thread.description,
                "created_at": thread.created_at.isoformat() if thread.created_at else None,
                "last_used_at": thread.last_used_at.isoformat() if thread.last_used_at else None,
                "usage_count": thread.usage_count,
                "expires_at": thread.expires_at.isoformat() if thread.expires_at else None,
                "is_active": thread.is_active,
                "key_name": thread.key_name,
            }
            user_data["threads"].append(thread_data)

        config_data["users"][user_id] = user_data

    return config_data


async def _save_config(manager: StaticUserManager) -> None:
    """异步保存配置到文件."""
    if manager._config is None:
        raise ValueError("配置未加载")

    await asyncio.to_thread(manager._config_path.parent.mkdir, parents=True, exist_ok=True)
    config_data = _serialize_config(manager._config)
    async with aiofiles.open(manager._config_path, "w", encoding="utf-8") as f:
        yaml_content = yaml.dump(
            config_data,
            default_flow_style=False,
            allow_unicode=True,
            indent=2,
            sort_keys=False,
        )
        await f.write(yaml_content)


async def create_api_key(
    manager: StaticUserManager,
    user_id: str,
    thread_id: str,
    description: str = "",
    key_name: str | None = None,
    expires_in_days: int | None = None,
) -> str:
    """创建新的API密钥并添加到配置."""
    if not manager.is_enabled():
        raise ValueError("静态用户管理未启用")
    if manager._config is None:
        raise ValueError("配置未加载")

    user = manager._config.get_user_by_user_id(user_id)
    if not user:
        raise ValueError(f"用户不存在: {user_id}")

    api_key = generate_api_key(user_id, thread_id)

    expires_at = None
    if expires_in_days:
        expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)

    api_key_info = ApiKeyInfo(
        api_key=api_key,
        thread_id=thread_id,
        description=description,
        key_name=key_name,
        expires_at=expires_at,
        created_at=datetime.now(UTC),
        last_used_at=None,
        usage_count=0,
        is_active=True,
    )

    if user.add_api_key(api_key_info):
        await _save_config(manager)
        print(f"创建API密钥成功: {api_key[:20]}... -> {user_id}:{thread_id}")
        return api_key
    raise ValueError(f"线程已存在或API密钥已存在: {thread_id}")


async def revoke_api_key(manager: StaticUserManager, api_key: str) -> bool:
    """撤销API密钥."""
    if not manager.is_enabled() or manager._config is None:
        return False

    result = manager._config.get_user_by_api_key(api_key)
    if result:
        user, _ = result
        if user.deactivate_api_key(api_key):
            await _save_config(manager)
            print(f"API密钥已撤销: {api_key[:20]}...")
            return True

    print(f"API密钥不存在: {api_key}")
    return False


def get_user_api_keys(manager: StaticUserManager, user_id: str) -> list[ApiKeyInfo]:
    """获取用户的所有API密钥."""
    if not manager.is_enabled() or manager._config is None:
        return []
    user = manager._config.get_user_by_user_id(user_id)
    if user:
        return user.threads
    return []


# =============================================================================
# CLI
# =============================================================================


class APIKeyManagerCLI:
    """API密钥管理CLI."""

    def __init__(self) -> None:
        """初始化CLI."""
        self.user_manager = StaticUserManager()
        self.auth_manager = AuthManager(self.user_manager)

    # 向后兼容的API密钥创建方法
    async def create_static_api_key(
        self, user_id: str, thread_id: str, description: str
    ) -> str:
        """创建静态API密钥 (向后兼容方法)."""
        try:
            # 检查用户是否存在
            user_info = self.user_manager.get_user_info(user_id)
            if not user_info:
                print(f"❌ 用户 '{user_id}' 不存在")
                return ""

            # 检查线程是否已存在
            existing_threads = get_user_api_keys(self.user_manager, user_id)
            for thread in existing_threads:
                if thread.thread_id == thread_id:
                    print(f"❌ 用户 '{user_id}' 的线程 '{thread_id}' 已存在")
                    print(f"   现有API密钥: {thread.api_key}")
                    return ""

            # 创建新API密钥
            api_key = await create_api_key(
                manager=self.user_manager,
                user_id=user_id,
                thread_id=thread_id,
                description=description,
            )

            print("✅ 成功创建API密钥:")
            print(f"   用户: {user_id}")
            print(f"   线程: {thread_id}")
            print(f"   描述: {description}")
            print(f"   API密钥: {api_key}")

            return api_key

        except Exception as e:
            print(f"❌ 创建API密钥失败: {e}")
            return ""

    def list_static_users(self) -> None:
        """列出所有静态用户 (向后兼容方法)."""
        try:
            manager = self.user_manager
            users_dict = manager.get_all_users()

            if not users_dict:
                print("📭 暂无静态用户")
                return

            users = list(users_dict.values())
            print(f"👥 静态用户列表 (共 {len(users)} 个用户):")
            print("-" * 60)

            for user in users:
                user_id = user.user_id
                display_name = getattr(user, "display_name", "N/A")
                status = getattr(user, "status", "unknown")
                thread_count = len(getattr(user, "threads", []))

                print(f"👤 {user_id}")
                print(f"   显示名: {display_name}")
                print(f"   状态: {status}")
                print(f"   线程数: {thread_count}")

                if hasattr(user, "threads") and user.threads:
                    print("   线程详情:")
                    for thread in user.threads:
                        thread_id = thread.thread_id
                        api_key = thread.api_key
                        description = getattr(thread, "description", "N/A")
                        usage_count = getattr(thread, "usage_count", 0)

                        print(f"     🔗 {thread_id}: {api_key[:30]}...")
                        print(f"        描述: {description}")
                        print(f"        使用次数: {usage_count}")

                print()

        except Exception as e:
            print(f"❌ 列出用户失败: {e}")

    def validate_api_key(self, api_key: str) -> bool:
        """验证API密钥有效性 (向后兼容方法)."""
        try:
            manager = self.user_manager

            # 验证静态用户API密钥
            result = manager.validate_api_key(api_key)

            if result:
                user, api_key_info = result
                user_id = user.user_id
                thread_id = api_key_info.thread_id

                print("✅ API密钥有效")
                print(f"   用户: {user_id}")
                print(f"   线程: {thread_id}")
                print(f"   用户显示名: {user.display_name}")
                print(f"   API密钥描述: {api_key_info.description}")
                print(f"   使用次数: {api_key_info.usage_count}")
                return True
            else:
                print("❌ API密钥无效或已禁用")
                return False

        except Exception as e:
            print(f"❌ 验证API密钥失败: {e}")
            return False

    def show_user_info(self, user_id: str) -> None:
        """显示用户详细信息 (向后兼容方法)."""
        try:
            users = self.user_manager.get_all_users()

            if user_id not in users:
                print(f"❌ 用户 '{user_id}' 不存在")
                return

            user_info = users[user_id]

            print(f"👤 用户信息: {user_id}")
            print("-" * 40)
            print(f"显示名: {getattr(user_info, 'display_name', 'N/A')}")
            print(f"邮箱: {getattr(user_info, 'email', 'N/A')}")
            print(f"状态: {getattr(user_info, 'status', 'N/A')}")
            print(f"创建时间: {getattr(user_info, 'created_at', 'N/A')}")
            print(f"更新时间: {getattr(user_info, 'updated_at', 'N/A')}")

            if hasattr(user_info, "threads") and user_info.threads:
                print(f"\n🔗 线程列表 (共 {len(user_info.threads)} 个):")
                for thread in user_info.threads:
                    print(f"  - {thread.thread_id}: {thread.api_key}")
                    print(f"    描述: {getattr(thread, 'description', 'N/A')}")
                    print(f"    使用次数: {getattr(thread, 'usage_count', 0)}")
            else:
                print("\n🔗 暂无线程")

        except Exception as e:
            print(f"❌ 获取用户信息失败: {e}")

    def show_statistics(self) -> None:
        """显示统计信息 (向后兼容方法)."""
        try:
            users_dict = self.user_manager.get_all_users()
            users = list(users_dict.values())

            total_users = len(users)
            active_users = len([
                u for u in users if getattr(u, "status", "inactive") == "active"
            ])
            total_threads = sum(len(getattr(u, "threads", [])) for u in users)
            total_api_keys = total_threads  # 每个线程对应一个API密钥

            print("📊 静态用户统计:")
            print("-" * 30)
            print(f"总用户数: {total_users}")
            print(f"活跃用户数: {active_users}")
            print(f"总线程数: {total_threads}")
            print(f"总API密钥数: {total_api_keys}")

        except Exception as e:
            print(f"❌ 获取统计信息失败: {e}")

    # 新增功能 - 用户管理
    async def create_user(
        self,
        user_id: str,
        display_name: str,
        email: str | None = None,
        status: str = "active",
    ) -> bool:
        """创建新用户."""
        try:
            # 检查用户是否已存在
            if self.user_manager.get_user_info(user_id):
                print(f"❌ 用户 '{user_id}' 已存在")
                return False

            # 验证状态
            try:
                user_status = UserStatus(status)
            except ValueError:
                print(f"❌ 无效的用户状态: {status}")
                print(f"   有效状态: {[s.value for s in UserStatus]}")
                return False

            # 创建用户
            user_data = create_default_user(
                user_id=user_id,
                display_name=display_name,
                email=email,
                status=user_status,
            )

            user = StaticUser.model_validate(user_data)

            # 通过配置对象添加用户
            config = self.user_manager._config
            if config is not None and user.user_id not in config.users:
                config.users[user.user_id] = user
                await _save_config(self.user_manager)
                print(f"✅ 用户创建成功: {user_id}")
                print(f"   显示名称: {display_name}")
                print(f"   邮箱: {email or '未设置'}")
                print(f"   状态: {status}")
                return True
            else:
                print("❌ 用户创建失败")
                return False

        except Exception as e:
            print(f"❌ 创建用户时发生错误: {e}")
            return False

    async def revoke_api_key(self, api_key: str) -> bool:
        """撤销API密钥."""
        try:
            # 先验证API密钥是否存在
            result = self.user_manager.validate_api_key(api_key)
            if not result:
                print("❌ API密钥不存在或已无效")
                return False

            user, api_key_info = result
            print("⚠️ 确认撤销API密钥?")
            print(f"   用户: {user.user_id}")
            print(f"   线程: {api_key_info.thread_id}")
            print(f"   API密钥: {api_key[:20]}...")

            if input("   输入 'yes' 确认撤销: ").lower() != "yes":
                print("❌ 撤销操作已取消")
                return False

            if await revoke_api_key(self.user_manager, api_key):
                print("✅ API密钥撤销成功")
                return True
            else:
                print("❌ API密钥撤销失败")
                return False

        except Exception as e:
            print(f"❌ 撤销API密钥时发生错误: {e}")
            return False

    def backup_config(self) -> str | None:
        """备份配置文件."""
        import shutil
        from pathlib import Path

        config_path = Path(self.user_manager._config_path)
        if not config_path.exists():
            print("❌ 配置文件不存在")
            return None
        backup_path = config_path.with_suffix(".yaml.bak")
        try:
            shutil.copy2(config_path, backup_path)
            print(f"✅ 配置文件备份成功: {backup_path}")
            return str(backup_path)
        except Exception as e:
            print(f"❌ 备份配置文件失败: {e}")
            return None


def main() -> None:
    """主函数."""
    parser = argparse.ArgumentParser(
        description="统一API密钥管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 创建新线程的API密钥
  python scripts/api_key_manager.py create alice work "工作线程"

  # 创建新用户
  python scripts/api_key_manager.py create-user alice --display-name "Alice Smith"

  # 列出所有用户和API密钥
  python scripts/api_key_manager.py list

  # 验证API密钥
  python scripts/api_key_manager.py validate sk-project-alice-work-abc123

  # 查看用户信息
  python scripts/api_key_manager.py show alice

  # 撤销API密钥
  python scripts/api_key_manager.py revoke sk-project-alice-work-abc123

  # 查看统计信息
  python scripts/api_key_manager.py stats

  # 备份配置文件
  python scripts/api_key_manager.py backup
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # 创建API密钥 (简化语法)
    create_parser = subparsers.add_parser("create", help="创建新的API密钥")
    create_parser.add_argument("user_id", help="用户ID")
    create_parser.add_argument("thread_id", help="线程ID")
    create_parser.add_argument("description", help="线程描述")

    # 创建用户
    user_parser = subparsers.add_parser("create-user", help="创建新用户")
    user_parser.add_argument("user_id", help="用户ID")
    user_parser.add_argument("--display-name", required=True, help="显示名称")
    user_parser.add_argument("--email", help="邮箱地址")
    user_parser.add_argument(
        "--status",
        choices=["active", "inactive", "suspended", "pending"],
        default="active",
        help="用户状态",
    )

    # 其他命令
    subparsers.add_parser("list", help="列出所有用户和API密钥")

    validate_parser = subparsers.add_parser("validate", help="验证API密钥")
    validate_parser.add_argument("api_key", help="要验证的API密钥")

    show_parser = subparsers.add_parser("show", help="显示用户详细信息")
    show_parser.add_argument("user_id", help="用户ID")

    revoke_parser = subparsers.add_parser("revoke", help="撤销API密钥")
    revoke_parser.add_argument("api_key", help="API密钥")

    subparsers.add_parser("stats", help="显示统计信息")
    subparsers.add_parser("backup", help="备份配置文件")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 创建CLI实例
    cli = APIKeyManagerCLI()

    # 执行相应命令
    try:
        if args.command == "create":
            asyncio.run(
                cli.create_static_api_key(
                    args.user_id, args.thread_id, args.description
                )
            )

        elif args.command == "create-user":
            asyncio.run(
                cli.create_user(
                    args.user_id, args.display_name, args.email, args.status
                )
            )

        elif args.command == "list":
            cli.list_static_users()

        elif args.command == "validate":
            cli.validate_api_key(args.api_key)

        elif args.command == "show":
            cli.show_user_info(args.user_id)

        elif args.command == "revoke":
            asyncio.run(cli.revoke_api_key(args.api_key))

        elif args.command == "stats":
            cli.show_statistics()

        elif args.command == "backup":
            cli.backup_config()

    except KeyboardInterrupt:
        print("\n\n❌ 操作已取消")
    except Exception as e:
        print(f"\n❌ 执行命令时发生错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
