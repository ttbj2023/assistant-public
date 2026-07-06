"""chart_maker 单元测试 conftest.

chart_builder 模块已迁至 docker/tool-runtime/ (tool-runtime 部署单元).
测试跨界导入: 注入 docker/tool-runtime/ 到 sys.path.
仅测试用, 生产代码零跨部署单元耦合.
"""

import sys
from pathlib import Path

_TOOL_RUNTIME_DIR = Path(__file__).resolve().parents[4] / "docker" / "tool-runtime"
if str(_TOOL_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_RUNTIME_DIR))
