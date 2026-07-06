"""Mock系统导出.

提供测试所需的Mock对象访问, 遵循"按需使用, 重复三次再抽象"原则.
"""

from .service_mock_factory import ServiceMockFactory
from .unified_factory import UnifiedMockFactory

__all__ = [
    "ServiceMockFactory",
    "UnifiedMockFactory",
]
