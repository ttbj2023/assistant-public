"""顶级pytest配置文件.

处理全局pytest插件配置，避免非顶级conftest中的pytest_plugins警告。
"""

# 顶级pytest插件配置
pytest_plugins = []


def pytest_configure(config) -> None:
    """配置全局pytest标记.

    注意：所有pytest标记现在通过 pyproject.toml 统一管理，
    此函数仅保留作为框架，实际标记定义请参考 pyproject.toml [tool.pytest.ini_options] markers。
    """
    pass  # 标记定义已移至 pyproject.toml 以实现统一配置管理
