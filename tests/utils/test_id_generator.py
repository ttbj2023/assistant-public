"""测试线程ID生成工具.

提供线程ID生成函数, 供 conftest 的测试身份 fixture 使用:
- generate_test_thread_id: 基于测试函数名生成线程ID, 用于数据隔离
- generate_thread_variants: 为多线程隔离场景生成线程ID变体

设计原则:
- 线程ID绑定测试函数名, 保证可读性与跨worker唯一性
- 附加随机后缀, 保证 pytest-xdist 并发安全
"""

import secrets
import uuid


def generate_test_thread_id(test_category: str, function_name: str) -> str:
    """基于测试函数名生成线程ID.

    同一测试函数多次运行使用相同前缀, 不同测试函数互不相同;
    附加随机后缀保证并发安全.

    Args:
        test_category: 测试类别 ("unit" / "integration")
        function_name: 测试函数名称

    Returns:
        形如 "{category}_{function}_{suffix}" 的线程ID
    """
    random_suffix = secrets.token_hex(4)
    return f"{test_category}_{function_name}_{random_suffix}"


def generate_thread_variants(
    base_thread_id: str, variants: list[str]
) -> dict[str, str]:
    """为测试隔离生成多个线程ID变体.

    用于同一测试内模拟多个线程的隔离性验证;
    每个变体附加同一 worker 后缀, 保证 pytest-xdist 并发安全.

    Args:
        base_thread_id: 基础线程ID(通常来自 test_thread_id fixture)
        variants: 变体名称列表, 如 ["t1", "t2", "t3"]

    Returns:
        变体名称到完整线程ID的映射字典
    """
    worker_suffix = uuid.uuid4().hex[:8]
    return {
        variant: f"{base_thread_id}_{variant}_{worker_suffix}"
        for variant in variants
    }
