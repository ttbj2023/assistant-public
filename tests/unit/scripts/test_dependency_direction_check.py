"""dependency_direction_check 单元测试: src -> scripts 反向依赖检出."""

from pathlib import Path

from scripts.dependency_direction_check import find_direction_violations


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_scan_scripts_imports_检测from_import形式(tmp_path: Path) -> None:
    _write(
        tmp_path / "agent" / "processor.py",
        "from scripts.debug.tool_call_tracker import create_tool_call_tracker\n",
    )

    violations = find_direction_violations(tmp_path)

    assert len(violations) == 1
    assert violations[0].module == "scripts.debug.tool_call_tracker"
    assert violations[0].file_path == tmp_path / "agent" / "processor.py"
    assert violations[0].line == 1


def test_scan_scripts_imports_检测纯import形式(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "import scripts\n")
    _write(tmp_path / "b.py", "import scripts.debug.tool_call_tracker\n")

    violations = find_direction_violations(tmp_path)

    assert {v.module for v in violations} == {
        "scripts",
        "scripts.debug.tool_call_tracker",
    }


def test_scan_scripts_imports_合法导入不误报(tmp_path: Path) -> None:
    _write(
        tmp_path / "c.py",
        "\n".join([
            "import os",
            "from src.config import runtime_env",
            "from . import sibling",
            "from .neighbor import helper",
            "import scriptship",
            "from myscripts.legacy import thing",
        ]),
    )

    violations = find_direction_violations(tmp_path)

    assert violations == []


def test_scan_scripts_imports_函数内延迟导入也检出(tmp_path: Path) -> None:
    _write(
        tmp_path / "lazy.py",
        "def f() -> None:\n    from scripts.debug import prompt_capture\n",
    )

    violations = find_direction_violations(tmp_path)

    assert len(violations) == 1
    assert violations[0].module == "scripts.debug"
    assert violations[0].line == 2
