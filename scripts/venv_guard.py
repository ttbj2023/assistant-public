"""项目虚拟环境自检与自动切换 (脚本公共守卫).

被 dev_server / run_test_suite / static_analysis 在第三方依赖导入前调用:
解释器不是项目 .venv 时自动 os.execv 重入, 避免工具链在错误环境下静默失败.
仅依赖标准库, 任何解释器下均可安全执行.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_venv() -> None:
    """检测并自动切换到项目虚拟环境, 避免未激活 venv 导致工具/依赖解析失败."""
    project_root = Path(__file__).resolve().parent.parent
    venv_python = project_root / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return

    expected_prefix = str(project_root / ".venv")
    if sys.prefix == expected_prefix:
        return

    print("🔄 当前未在项目虚拟环境中, 自动切换...")
    print(f"   当前: {sys.executable}")
    print(f"   目标: {venv_python}")
    sys.stdout.flush()  # execv 前冲刷缓冲, 避免提示信息丢失
    os.execv(str(venv_python), [str(venv_python), *sys.argv])
