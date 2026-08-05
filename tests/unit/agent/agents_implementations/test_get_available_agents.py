"""get_available_agents 单元测试.

验证返回的 Agent 列表顺序稳定 (按 agent_id 排序), 不依赖文件系统遍历顺序.
"""

from __future__ import annotations

import pathlib

import src.agent.agents_implementations as impl_mod


class TestGetAvailableAgents:
    def test_returns_sorted_regardless_of_filesystem_order(self, monkeypatch):
        """文件系统倒序遍历时, 返回结果仍应按 agent_id 排序."""
        real_iterdir = pathlib.Path.iterdir

        def reversed_iterdir(self):
            return iter(list(real_iterdir(self))[::-1])

        monkeypatch.setattr(pathlib.Path, "iterdir", reversed_iterdir)

        result = impl_mod.get_available_agents()

        assert result == sorted(result)
