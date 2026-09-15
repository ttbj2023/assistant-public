"""依赖方向检查器 - src 不得 import scripts 的门禁.

架构约束: scripts/ 可以 import src/ (如 benchmark 取 provider 配置),
禁止 src/ 反向 import scripts/. 违例即 STRICT, 阻断 CI.
用 AST 解析 (非 regex), 精确区分 import/ImportFrom 与相对导入.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ImportViolation:
    """src -> scripts 反向依赖违规."""

    module: str
    file_path: Path
    line: int


def _is_scripts_import(module_name: str) -> bool:
    """判断 import 目标是否落在 scripts 包根下."""
    return module_name.split(".", 1)[0] == "scripts"


def scan_scripts_imports(src_dir: Path) -> list[ImportViolation]:
    """扫描目录下所有 .py 文件, 收集指向 scripts 包的 import.

    覆盖两种形态: `import scripts[.x]` 与 `from scripts[.x] import y`;
    包内相对导入 (level > 0) 不属于跨包依赖, 天然排除.
    """
    violations: list[ImportViolation] = []
    for py_file in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError as e:
            logger.warning("语法错误, 跳过: %s (%s)", py_file, e)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_scripts_import(alias.name):
                        violations.append(
                            ImportViolation(alias.name, py_file, node.lineno)
                        )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and _is_scripts_import(node.module)
            ):
                violations.append(ImportViolation(node.module, py_file, node.lineno))
    return violations


def find_direction_violations(src_dir: Path) -> list[ImportViolation]:
    """入口: 返回 src_dir 下所有 src -> scripts 反向依赖."""
    return scan_scripts_imports(src_dir)
