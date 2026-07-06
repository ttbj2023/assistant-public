"""用户数据路径解析器 - Agent物理隔离版本

存储规范 (三级物理隔离: 用户 -> 线程 -> Agent):
- 基础路径/用户ID/线程ID/agentID/
  - database/: 存放各种数据库文件 (每个agent独立)
  - vector/: 存放向量存储 (每个agent独立)
- 基础路径/用户ID/线程ID/shared/
  - files/images/: 共享附件存储

核心特性:
- 零配置: 完全自动化, 无需任何配置
- 按需创建: 使用时自动创建对应目录
- 单例模式: 全局统一的路径解析器
- 路径缓存: 避免重复计算和文件系统操作
- 测试环境支持: 通过 ENVIRONMENT=testing 自动使用 ./test_data
- Agent物理隔离: 每个agent拥有完全独立的数据库和向量存储
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from pathlib import Path

from src.config import runtime_env
from src.core.validation import IDValidator

logger = logging.getLogger(__name__)


class UserDataPathResolver:
    """用户数据路径解析器 - Agent物理隔离版本

    存储规范 (三级物理隔离):
    基础路径/用户ID/线程ID/agentID/
      database/: todo.db, pinned_memory.db, conversation_history.db
      vector/: 向量存储目录
    基础路径/用户ID/线程ID/shared/
      files/images/: 共享附件
    """

    _instance: UserDataPathResolver | None = None
    _lock = threading.Lock()
    _initialized: bool
    base_path: Path

    def __new__(cls) -> UserDataPathResolver:
        """单例模式实现"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        """初始化路径解析器"""
        if hasattr(self, "_initialized") and self._initialized:
            return

        # 检测测试环境:ENVIRONMENT=testing 或 test
        is_test_mode = runtime_env.is_test_environment()

        if is_test_mode:
            # 测试模式:使用 ./test_data 作为base_path
            # 检查是否是pytest-xdist worker进程
            worker_id = runtime_env.get_pytest_worker_id()
            self.base_path = Path(runtime_env.test_data_dir_name(worker_id))
            self.base_path.mkdir(parents=True, exist_ok=True)
            if worker_id:
                logger.info(
                    f"🧪 测试模式(pytest-xdist Worker {worker_id}) - 使用测试目录: {self.base_path}",
                )
            else:
                logger.info(f"🧪 测试模式 - 使用测试目录: {self.base_path}")
        else:
            # 生产模式:使用环境变量或默认路径
            self.base_path = runtime_env.get_base_data_path()
            # 验证路径有效性
            self._validate_base_path()
            # 确保基础目录存在
            self.base_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"🏗️ 生产模式 - 使用配置的基础路径: {self.base_path}")

        self._initialized = True

    def _validate_base_path(self) -> None:
        """验证基础路径的有效性(增强版)"""
        try:
            # 规范化路径,解析符号链接
            self.base_path = self.base_path.resolve()

            # 检查路径安全性(防止路径遍历攻击)
            self._validate_path_security()

            # 检查路径是否有效
            if self.base_path.exists():
                self._validate_existing_path()
            else:
                self._validate_parent_path()

        except (OSError, PermissionError, ValueError) as e:
            logger.error("❌ 基础路径验证失败: %s", e)
            raise

    def _validate_path_security(self) -> None:
        """验证路径安全性,防止路径遍历攻击"""
        path_parts = self.base_path.parts

        # 检查是否包含危险组件
        dangerous_components = [".."]
        for component in dangerous_components:
            if component in path_parts:
                raise ValueError(f"路径包含危险组件 '{component}': {self.base_path}")

        # 检查是否为绝对路径(推荐)
        if not self.base_path.is_absolute():
            logger.warning(f"⚠️ 建议使用绝对路径,当前为相对路径: {self.base_path}")

    def _validate_existing_path(self) -> None:
        """验证已存在的路径"""
        if not self.base_path.is_dir():
            raise ValueError(f"基础路径存在但不是目录: {self.base_path}")

        # 检查目录权限
        required_permissions = os.R_OK | os.W_OK | os.X_OK
        if not os.access(self.base_path, required_permissions):
            missing_permissions = []
            if not os.access(self.base_path, os.R_OK):
                missing_permissions.append("读")
            if not os.access(self.base_path, os.W_OK):
                missing_permissions.append("写")
            if not os.access(self.base_path, os.X_OK):
                missing_permissions.append("执行")

            raise PermissionError(
                f"基础路径权限不足,缺少: {', '.join(missing_permissions)} 权限: {self.base_path}",
            )

        # 检查磁盘空间
        self._check_disk_space()

        logger.debug(f"✅ 基础路径验证通过: {self.base_path}")

    def _validate_parent_path(self) -> None:
        """验证父目录,准备创建新目录"""
        parent_dir = self.base_path.parent

        # 递归检查父目录链
        if not parent_dir.exists():
            raise FileNotFoundError(f"父目录不存在: {parent_dir}")

        # 检查父目录权限
        if not os.access(parent_dir, os.W_OK | os.X_OK):
            raise PermissionError(f"无法在父目录中创建基础路径,缺少权限: {parent_dir}")

        # 检查父目录的磁盘空间
        self._check_disk_space(parent_dir)

        logger.debug("✅ 父目录验证通过: %s", parent_dir)

    def _check_disk_space(self, path: Path | None = None) -> None:
        """检查磁盘空间"""
        try:
            check_path = path or self.base_path
            _total, _used, free = shutil.disk_usage(check_path)

            # 转换为GB
            free_gb = free / (1024**3)

            # 警告阈值:1GB
            warning_threshold_gb = 1.0

            if free_gb < warning_threshold_gb:
                logger.warning(
                    f"⚠️ 磁盘空间不足: {free_gb:.2f}GB 可用 (建议至少 {warning_threshold_gb}GB)",
                )
            else:
                logger.debug(f"✅ 磁盘空间充足: {free_gb:.2f}GB 可用")

        except (OSError, AttributeError) as e:
            logger.debug("无法检查磁盘空间: %s", e)

    def get_thread_base_path(self, user_id: str, thread_id: str) -> Path:
        """获取线程基础路径

        Args:
            user_id: 用户ID
            thread_id: 线程ID

        Returns:
            线程基础目录Path对象

        """
        # 验证ID并获取安全的用户ID和线程ID
        safe_user_id, safe_thread_id = self._validate_ids(user_id, thread_id)

        # 新的存储规范: base_path/user_id/thread_id/
        thread_path = self.base_path / safe_user_id / safe_thread_id

        # 确保目录存在
        self._ensure_directory(thread_path, "线程基础目录")

        self._log_debug_path(safe_user_id, safe_thread_id, "线程基础", thread_path)
        return thread_path

    def get_agent_base_path(self, user_id: str, thread_id: str, agent_id: str) -> Path:
        """获取Agent基础路径 (三级物理隔离: 用户/线程/Agent).

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID

        Returns:
            Agent基础目录Path对象

        """
        safe_user_id, safe_thread_id, safe_agent_id = self._validate_ids_with_agent(
            user_id,
            thread_id,
            agent_id,
        )

        agent_path = self.base_path / safe_user_id / safe_thread_id / safe_agent_id

        self._ensure_directory(agent_path, "Agent基础目录")

        logger.debug(
            "🔧 创建Agent基础路径: %s/%s/%s -> %s",
            safe_user_id,
            safe_thread_id,
            safe_agent_id,
            agent_path,
        )
        return agent_path

    def get_database_path(
        self,
        user_id: str,
        thread_id: str,
        db_name: str,
        *,
        agent_id: str,
    ) -> str:
        """获取数据库文件路径 (Agent物理隔离).

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            db_name: 数据库名称 (如 'todo', 'pinned_memory', 'conversation_history')
            agent_id: Agent ID

        Returns:
            数据库文件路径字符串

        """
        if not db_name.strip():
            raise ValueError("db_name必须是非空字符串")

        safe_user_id, safe_thread_id, safe_agent_id = self._validate_ids_with_agent(
            user_id,
            thread_id,
            agent_id,
        )

        safe_db_name = IDValidator.validate_user_id(db_name.replace(":", "_"))

        agent_path = self.get_agent_base_path(
            safe_user_id,
            safe_thread_id,
            safe_agent_id,
        )
        database_dir = agent_path / "database"

        self._ensure_directory(database_dir, "数据库目录")

        db_path = database_dir / f"{safe_db_name}.db"
        logger.debug(
            "🔧 创建数据库路径: %s/%s/%s/%s -> %s",
            safe_user_id,
            safe_thread_id,
            safe_agent_id,
            safe_db_name,
            db_path,
        )
        return str(db_path)

    def get_vector_path(self, user_id: str, thread_id: str, *, agent_id: str) -> Path:
        """获取向量存储目录路径 (Agent物理隔离).

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID

        Returns:
            向量存储目录Path对象

        """
        safe_user_id, safe_thread_id, safe_agent_id = self._validate_ids_with_agent(
            user_id,
            thread_id,
            agent_id,
        )

        agent_path = self.get_agent_base_path(
            safe_user_id,
            safe_thread_id,
            safe_agent_id,
        )
        vector_dir = agent_path / "vector"

        self._ensure_directory(vector_dir, "向量存储目录")

        logger.debug(
            "🔧 创建向量存储路径: %s/%s/%s -> %s",
            safe_user_id,
            safe_thread_id,
            safe_agent_id,
            vector_dir,
        )
        return vector_dir

    def get_user_database_path(self, user_id: str, db_name: str) -> str:
        """获取用户级数据库文件路径.

        用于存储用户级共享数据 (如文件去重索引), 不随线程或Agent隔离.

        Args:
            user_id: 用户ID
            db_name: 数据库名称

        Returns:
            数据库文件路径字符串

        """
        if not db_name.strip():
            raise ValueError("db_name必须是非空字符串")

        safe_user_id = IDValidator.validate_user_id(user_id)
        safe_db_name = IDValidator.validate_user_id(db_name.replace(":", "_"))

        user_path = self.base_path / safe_user_id / "database"
        self._ensure_directory(user_path, "用户级数据库目录")

        db_path = user_path / f"{safe_db_name}.db"
        logger.debug(
            "🔧 创建用户级数据库路径: %s/%s -> %s",
            safe_user_id,
            safe_db_name,
            db_path,
        )
        return str(db_path)

    def get_shared_storage_path(
        self,
        user_id: str,
        thread_id: str,
        storage_type: str,
    ) -> Path:
        """获取共享存储目录路径 (线程级别, 非agent隔离).

        用于附件等需要跨agent共享的资源.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            storage_type: 存储类型 (如 'files/images')

        Returns:
            共享存储目录Path对象

        """
        if not storage_type.strip():
            raise ValueError("storage_type必须是非空字符串")

        safe_storage_type = storage_type.replace(":", "_")
        if not re.match(r"^[a-zA-Z0-9_/-]+$", safe_storage_type):
            raise ValueError(
                f"storage_type只能包含字母,数字,下划线,连字符和斜杠: {storage_type}",
            )

        safe_user_id, safe_thread_id = self._validate_ids(user_id, thread_id)
        thread_path = self.get_thread_base_path(safe_user_id, safe_thread_id)
        storage_dir = thread_path / "shared" / safe_storage_type

        self._ensure_directory(storage_dir, f"共享{safe_storage_type}存储目录")

        logger.debug(
            "🔧 创建共享存储路径: %s/%s/%s -> %s",
            safe_user_id,
            safe_thread_id,
            safe_storage_type,
            storage_dir,
        )
        return storage_dir

    def get_user_base_path(self, user_id: str) -> Path:
        """获取用户根目录路径.

        用于文件去重和配额管理等需要访问用户级目录的场景.
        路径: base_path/{user_id}/

        Args:
            user_id: 用户ID

        Returns:
            用户根目录Path对象

        """
        safe_user_id = IDValidator.validate_user_id(user_id)
        return self.base_path / safe_user_id

    # === 私有辅助方法 ===

    def _validate_ids(self, user_id: str, thread_id: str) -> tuple[str, str]:
        """验证并返回安全的用户ID和线程ID.

        Args:
            user_id: 原始用户ID
            thread_id: 原始线程ID

        Returns:
            验证后的用户ID和线程ID元组

        """
        if not isinstance(user_id, str):
            raise TypeError(f"user_id必须是字符串,得到: {type(user_id)}")
        if not isinstance(thread_id, str):
            raise TypeError(f"thread_id必须是字符串,得到: {type(thread_id)}")

        if not user_id.strip():
            raise ValueError("user_id不能为空字符串")
        if not thread_id.strip():
            raise ValueError("thread_id不能为空字符串")

        try:
            safe_user_id = IDValidator.validate_user_id(user_id)
            safe_thread_id = IDValidator.validate_thread_id(thread_id)
        except Exception as e:
            logger.error(
                "❌ ID验证失败,无法继续处理: user_id=%s, thread_id=%s, 错误: %s",
                user_id,
                thread_id,
                e,
            )
            raise ValueError(f"无效的用户ID或线程ID: {e}") from e

        return safe_user_id, safe_thread_id

    def _validate_ids_with_agent(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
    ) -> tuple[str, str, str]:
        """验证并返回安全的用户ID,线程ID和Agent ID.

        Args:
            user_id: 原始用户ID
            thread_id: 原始线程ID
            agent_id: 原始Agent ID

        Returns:
            验证后的用户ID,线程ID和Agent ID三元组

        """
        if not isinstance(agent_id, str):
            raise TypeError(f"agent_id必须是字符串,得到: {type(agent_id)}")
        if not agent_id.strip():
            raise ValueError("agent_id不能为空字符串")

        safe_user_id, safe_thread_id = self._validate_ids(user_id, thread_id)

        try:
            safe_agent_id = IDValidator.validate_user_id(agent_id)
        except Exception as e:
            logger.error("❌ agent_id验证失败: agent_id=%s, 错误: %s", agent_id, e)
            raise ValueError(f"无效的Agent ID: {e}") from e

        return safe_user_id, safe_thread_id, safe_agent_id

    def _ensure_directory(self, path: Path, description: str = "目录") -> None:
        """确保目录存在

        Args:
            path: 目录路径
            description: 目录描述(用于日志)

        并发安全:直接调用mkdir(parents=True, exist_ok=True)避免TOCTOU竞态条件

        """
        try:
            # 直接创建目录,不进行存在性检查,避免check-then-act竞态条件
            # exist_ok=True 确保目录已存在时不会报错
            # parents=True 确保父目录也会被创建
            path.mkdir(parents=True, exist_ok=True)

            # 记录日志
            logger.debug("✅ 成功创建%s: %s", description, path)

        except OSError as e:
            error_msg = f"❌ 创建{description}失败: {path}, 错误: {e}"
            logger.error(error_msg)
            raise

    def _log_debug_path(
        self,
        user_id: str,
        thread_id: str,
        path_type: str,
        path: Path,
    ) -> None:
        """记录路径创建的调试信息

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            path_type: 路径类型
            path: 路径对象

        """
        logger.debug("🔧 创建%s路径: %s/%s -> %s", path_type, user_id, thread_id, path)


# 全局单例实例
def get_user_path_resolver() -> UserDataPathResolver:
    """获取全局用户路径解析器单例"""
    return UserDataPathResolver()


# 核心便捷函数


def get_thread_base_path(user_id: str, thread_id: str) -> Path:
    """获取线程基础路径的便捷函数."""
    return get_user_path_resolver().get_thread_base_path(user_id, thread_id)


def get_database_path(
    user_id: str,
    thread_id: str,
    db_name: str,
    *,
    agent_id: str,
) -> str:
    """获取数据库路径的便捷函数 (Agent物理隔离)."""
    return get_user_path_resolver().get_database_path(
        user_id,
        thread_id,
        db_name,
        agent_id=agent_id,
    )


def get_vector_path(user_id: str, thread_id: str, *, agent_id: str) -> Path:
    """获取向量存储路径的便捷函数 (Agent物理隔离)."""
    return get_user_path_resolver().get_vector_path(
        user_id,
        thread_id,
        agent_id=agent_id,
    )


def get_user_database_path(user_id: str, db_name: str) -> str:
    """获取用户级数据库路径的便捷函数."""
    return get_user_path_resolver().get_user_database_path(user_id, db_name)


def get_shared_storage_path(user_id: str, thread_id: str, storage_type: str) -> Path:
    """获取共享存储路径的便捷函数."""
    return get_user_path_resolver().get_shared_storage_path(
        user_id,
        thread_id,
        storage_type,
    )


def resolve_attachment_internal_path(
    internal_path: str,
    user_id: str,
    thread_id: str,
) -> Path:
    """解析附件 internal_path 为完整文件路径.

    支持两种路径格式:
    - 线程内路径: files/images/xxx.jpg (兼容旧数据)
    - 跨线程路径: {thread_id}/shared/files/images/xxx.jpg (去重后的新格式)

    Args:
        internal_path: 附件注册表中的内部路径
        user_id: 用户ID
        thread_id: 当前线程ID

    Returns:
        附件完整文件路径

    """
    resolver = get_user_path_resolver()
    if "/shared/files/" in internal_path:
        # 跨线程路径: {thread_id}/shared/files/...
        return resolver.base_path / user_id / internal_path
    # 线程内路径: files/images/... (兼容旧数据)
    return resolver.get_thread_base_path(user_id, thread_id) / "shared" / internal_path
