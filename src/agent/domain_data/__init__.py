"""领域数据子系统 - 从对话中提取结构化领域数据的正交架构.

与 memory(对话上下文) 并列, 各 domain_data 实例自治存储, 互不影响.
"""

from __future__ import annotations

from .base_domain_data import BaseDomainData
from .domain_data_catalog import get_available_domain_data, get_domain_data_class
from .domain_data_dispatcher import DomainDataDispatcher

__all__ = [
    "BaseDomainData",
    "DomainDataDispatcher",
    "get_available_domain_data",
    "get_domain_data_class",
]
