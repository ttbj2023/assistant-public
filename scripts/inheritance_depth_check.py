"""继承深度检查器 - 业务继承深度门禁.

白名单策略: 只计项目内定义的基类层数, 外部框架基类(BaseTool/BaseModel/ABC/
SQLModel 等)不在项目 classes_map 中, 天然被排除, 无需维护黑名单.
业务继承深度 > 2 视为违规(违反"组合优于继承"约束).
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ClassInfo:
    """类的定义信息."""

    name: str
    file_path: Path
    line: int
    bases: list[str]


@dataclass
class Violation:
    """继承深度违规."""

    class_name: str
    file_path: Path
    line: int
    depth: int
    chain: list[str]


def _base_name(node: ast.expr) -> str | None:
    """解析基类 AST 节点的末段名.

    Name -> id; Attribute -> 末段 attr; 其他(Subscript/Call 等) -> None.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def scan_project_classes(src_dir: Path) -> dict[str, ClassInfo]:
    """扫描目录下所有 .py 文件, 收集 class 定义.

    同名类后者覆盖前者(项目内类名应唯一, 冲突时记 debug 日志).
    """
    classes: dict[str, ClassInfo] = {}
    for py_file in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError as e:
            logger.debug("解析失败 %s: %s", py_file, e)
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [
                name for base in node.bases if (name := _base_name(base)) is not None
            ]
            if node.name in classes:
                logger.debug("同名类冲突, 后者覆盖: %s", node.name)
            classes[node.name] = ClassInfo(
                name=node.name,
                file_path=py_file,
                line=node.lineno,
                bases=bases,
            )
    return classes


def compute_business_depth(class_name: str, classes: dict[str, ClassInfo]) -> int:
    """计算业务继承深度(沿项目内基类向上的最大层数).

    基类不在 classes 中(外部基类)则截断该链, 不计入深度.
    共享 visited 集合同时处理循环防护与菱形继承.
    """
    if class_name not in classes:
        return 0

    visited: set[str] = set()

    def _depth(name: str) -> int:
        if name in visited:
            return 0  # 循环防护
        visited.add(name)
        info = classes.get(name)
        if info is None:
            return 0
        # 仅对项目内基类递归, 外部基类(BaseTool/BaseModel 等)不计入深度
        project_depths = [_depth(b) for b in info.bases if b in classes]
        if not project_depths:
            return 0
        return 1 + max(project_depths)

    return _depth(class_name)


def _trace_chain(class_name: str, classes: dict[str, ClassInfo]) -> list[str]:
    """沿项目内基类链向上追溯, 返回从叶子到根的类名列表(取一条代表链)."""
    chain: list[str] = []
    current: str | None = class_name
    visited: set[str] = set()
    while current is not None and current not in visited and current in classes:
        visited.add(current)
        chain.append(current)
        info = classes[current]
        project_bases = [b for b in info.bases if b in classes]
        current = project_bases[0] if project_bases else None
    return chain


def find_depth_violations(src_dir: Path, max_depth: int = 2) -> list[Violation]:
    """找出业务继承深度超过 max_depth 的所有类."""
    classes = scan_project_classes(src_dir)
    violations: list[Violation] = []
    for name, info in classes.items():
        depth = compute_business_depth(name, classes)
        if depth <= max_depth:
            continue
        violations.append(
            Violation(
                class_name=name,
                file_path=info.file_path,
                line=info.line,
                depth=depth,
                chain=_trace_chain(name, classes),
            )
        )
    violations.sort(key=lambda v: (str(v.file_path), v.line))
    return violations


__all__ = [
    "ClassInfo",
    "Violation",
    "compute_business_depth",
    "find_depth_violations",
    "scan_project_classes",
]
