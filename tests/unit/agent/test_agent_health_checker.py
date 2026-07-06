"""Agent健康检查器的单元测试.

测试Agent模块轻量级健康检查的各种场景和边界情况。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from src.agent.agent_health_checker import (
    check_agent_directory_structure,
    check_agent_import_path,
    check_agent_yaml_config,
    check_core_dependencies,
    lightweight_agent_health_check,
)


class TestAgentYamlConfigCheck:
    """测试Agent配置文件健康检查."""

    def test_check_agent_yaml_config_success(self, tmp_path):
        """测试正常的配置文件检查."""
        agent_dir = tmp_path / "personal-assistant"
        agent_dir.mkdir()
        config_file = agent_dir / "agent.yaml"

        config_content = {
            "agent_id": "personal-assistant",
            "name": "个人助手",
            "description": "通用AI助手",
            "model_id": "local:qwen3.5:9b",
        }

        config_file.write_text(yaml.dump(config_content), encoding="utf-8")

        with patch("src.agent.agent_health_checker.Path") as mock_path:
            mock_path.return_value = Path(config_file)
            result = check_agent_yaml_config("personal-assistant")

        assert result["available"] is True
        assert result["file_exists"] is True
        assert result["is_file"] is True
        assert result["yaml_valid"] is True
        assert result["has_required_fields"] is True
        assert result["config_entries_count"] > 0

    def test_check_agent_yaml_config_file_not_exists(self):
        """测试配置文件不存在的情况."""
        with patch("src.agent.agent_health_checker.Path") as mock_path:
            mock_path.return_value.exists.return_value = False
            result = check_agent_yaml_config("non-existent-agent")

        assert result["available"] is False
        assert result["file_exists"] is False
        assert "不存在" in result["error"]

    def test_check_agent_yaml_config_invalid_yaml(self, tmp_path):
        """测试无效YAML格式的情况."""
        agent_dir = tmp_path / "invalid-agent"
        agent_dir.mkdir()
        config_file = agent_dir / "agent.yaml"
        config_file.write_text("invalid: yaml: content:", encoding="utf-8")

        with patch("src.agent.agent_health_checker.Path") as mock_path:
            mock_path.return_value = Path(config_file)
            result = check_agent_yaml_config("invalid-agent")

        assert result["available"] is False
        assert result["yaml_valid"] is False
        assert "YAML格式错误" in result["error"]

    def test_check_agent_yaml_config_missing_required_fields(self, tmp_path):
        """测试缺少必需字段的情况."""
        agent_dir = tmp_path / "incomplete-agent"
        agent_dir.mkdir()
        config_file = agent_dir / "agent.yaml"

        # 缺少必需字段
        config_content = {"description": "不完整的配置"}

        config_file.write_text(yaml.dump(config_content), encoding="utf-8")

        with patch("src.agent.agent_health_checker.Path") as mock_path:
            mock_path.return_value = Path(config_file)
            result = check_agent_yaml_config("incomplete-agent")

        assert result["available"] is False
        assert result["has_required_fields"] is False
        assert "agent_id" in result["missing_fields"]
        assert "name" in result["missing_fields"]


class TestAgentImportPathCheck:
    """测试Agent导入路径健康检查."""

    def test_check_agent_import_path_success(self):
        """测试正常的导入路径检查."""
        with patch("importlib.util.find_spec") as mock_find_spec:
            mock_find_spec.return_value = MagicMock()
            result = check_agent_import_path("personal-assistant")

        assert result["main_module_available"] is True
        # 实现将agent_id中的"-"替换为"_"，所以路径使用下划线
        assert (
            "src.agent.agents_implementations.personal_assistant.main"
            in result["main_module_path"]
        )

    def test_check_agent_import_path_failure(self):
        """测试导入路径不可用的情况."""
        with patch("importlib.util.find_spec") as mock_find_spec:
            mock_find_spec.return_value = None
            result = check_agent_import_path("non-existent-agent")

        assert result["main_module_available"] is False
        assert "不可导入" in result["error"]

    def test_check_agent_import_path_exception(self):
        """测试导入路径检查异常."""
        with patch("importlib.util.find_spec") as mock_find_spec:
            mock_find_spec.side_effect = Exception("Import error")
            result = check_agent_import_path("error-agent")

        assert result["main_module_available"] is False
        assert "导入路径检查异常" in result["error"]


class TestAgentDirectoryStructureCheck:
    """测试Agent目录结构健康检查."""

    def test_check_agent_directory_structure_success(self, tmp_path):
        """测试正常的目录结构检查."""
        agent_dir = tmp_path / "personal-assistant"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text("test: config")
        (agent_dir / "main.py").write_text("print('test')")

        with patch("src.agent.agent_health_checker.Path") as mock_path:
            mock_path.return_value = Path(agent_dir)
            result = check_agent_directory_structure("personal-assistant")

        assert result["available"] is True
        assert result["directory_exists"] is True
        assert result["is_directory"] is True
        assert result["key_files"]["agent.yaml"] is True
        assert result["key_files"]["main.py"] is True
        assert len(result["missing_files"]) == 0

    def test_check_agent_directory_structure_dir_not_exists(self):
        """测试Agent目录不存在的情况."""
        with patch("src.agent.agent_health_checker.Path") as mock_path:
            mock_path.return_value.exists.return_value = False
            result = check_agent_directory_structure("non-existent-agent")

        assert result["available"] is False
        assert result["directory_exists"] is False

    def test_check_agent_directory_structure_missing_files(self, tmp_path):
        """测试缺少关键文件的情况."""
        agent_dir = tmp_path / "incomplete-agent"
        agent_dir.mkdir()
        # 只创建agent.yaml，缺少main.py
        (agent_dir / "agent.yaml").write_text("test: config")

        with patch("src.agent.agent_health_checker.Path") as mock_path:
            mock_path.return_value = Path(agent_dir)
            result = check_agent_directory_structure("incomplete-agent")

        assert result["available"] is False
        assert result["key_files"]["agent.yaml"] is True
        assert result["key_files"]["main.py"] is False
        assert "main.py" in result["missing_files"]


class TestCoreDependenciesCheck:
    """测试核心依赖健康检查."""

    def test_check_core_dependencies_success(self):
        """测试所有依赖都可用的情况."""
        with patch("importlib.util.find_spec") as mock_find_spec:
            # 模拟所有依赖都可用
            mock_find_spec.return_value = MagicMock()
            result = check_core_dependencies()

        assert result["available"] is True
        assert result["total_dependencies"] == 4
        assert result["available_dependencies"] == 4
        assert result["availability_ratio"] == 1.0

    def test_check_core_dependencies_partial_failure(self):
        """测试部分依赖不可用的情况."""
        with patch("importlib.util.find_spec") as mock_find_spec:
            # 模拟部分依赖不可用
            mock_find_spec.side_effect = [None, MagicMock(), MagicMock(), None]
            result = check_core_dependencies()

        assert result["available"] is False
        assert result["total_dependencies"] == 4
        assert result["available_dependencies"] == 2
        assert result["availability_ratio"] == 0.5


class TestIntegrationScenarios:
    """集成测试场景."""

    def test_health_check_should_handle_errors_gracefully_when_exception_occurs(self):
        """测试健康检查：发生异常时应该优雅处理错误"""
        with patch(
            "src.agent.agents_implementations.get_agent_directory"
        ) as mock_get_dir:
            mock_get_dir.side_effect = Exception("Directory scan failed")
            with patch(
                "src.agent.agent_health_checker.check_core_dependencies"
            ) as mock_deps:
                mock_deps.return_value = {"available": True}
                result = lightweight_agent_health_check()

        assert result["overall_status"] == "unhealthy"
        assert result["health_ratio"] == 0
        assert "health_check_execution_failed" not in result  # 确保不会完全崩溃


class TestAgentYamlConfigEdgeCases:
    """测试YAML配置额外边界路径."""

    def test_check_agent_yaml_config_path_is_not_file(self) -> None:
        """测试配置文件路径不是文件(如目录)."""
        with patch("src.agent.agent_health_checker.Path") as mock_path:
            mock_path.return_value.exists.return_value = True
            mock_path.return_value.is_file.return_value = False
            result = check_agent_yaml_config("personal-assistant")

        assert result["available"] is False
        assert result["is_file"] is False
        assert "不是文件" in result["error"]

    def test_check_agent_yaml_config_file_read_error(self) -> None:
        """测试配置文件读取时发生非YAML异常."""
        with patch("src.agent.agent_health_checker.Path") as mock_path:
            mock_path.return_value.exists.return_value = True
            mock_path.return_value.is_file.return_value = True
            mock_path.return_value.open.side_effect = IOError("Permission denied")
            result = check_agent_yaml_config("personal-assistant")

        assert result["available"] is False
        assert "文件读取错误" in result["error"]
        assert result["file_exists"] is True
        assert result["is_file"] is True


class TestAgentDirectoryStructureEdgeCases:
    """测试目录结构额外边界路径."""

    def test_check_agent_directory_structure_path_is_not_dir(self) -> None:
        """测试Agent路径不是目录(如文件)."""
        with patch("src.agent.agent_health_checker.Path") as mock_path:
            mock_path.return_value.exists.return_value = True
            mock_path.return_value.is_dir.return_value = False
            result = check_agent_directory_structure("personal-assistant")

        assert result["available"] is False
        assert result["is_directory"] is False
        assert "不是目录" in result["error"]


class TestCoreDependenciesEdgeCases:
    """测试核心依赖额外路径."""

    def test_check_core_dependencies_per_module_exception(self) -> None:
        """测试某个模块导入异常时优雅降级记录."""
        with patch("importlib.util.find_spec") as mock_find_spec:
            mock_find_spec.side_effect = [
                MagicMock(),
                Exception("导入模块异常"),
                MagicMock(),
                MagicMock(),
            ]
            result = check_core_dependencies()

        assert result["available"] is False
        assert result["available_dependencies"] == 3
        assert result["availability_ratio"] == 0.75


class TestLightweightHealthCheckEdgeCases:
    """测试轻量级健康检查主函数整体分支."""

    def test_health_check_core_deps_unhealthy(self) -> None:
        """测试核心依赖不健康时整体状态为unhealthy."""
        with patch(
            "src.agent.agents_implementations.get_available_agents",
        ) as mock_get:
            mock_get.return_value = ["test-agent"]
            with patch(
                "src.agent.agent_health_checker.check_core_dependencies",
            ) as mock_deps:
                mock_deps.return_value = {
                    "available": False,
                    "dependency_status": {},
                }
                result = lightweight_agent_health_check()

        assert result["overall_status"] == "unhealthy"
        assert "核心依赖不可用" in result["overall_message"]

    def test_health_check_some_agents_degraded(self) -> None:
        """测试部分Agent不健康时状态为degraded."""
        with patch(
            "src.agent.agents_implementations.get_available_agents",
        ) as mock_get:
            mock_get.return_value = ["healthy-agent", "unhealthy-agent"]
            with patch(
                "src.agent.agent_health_checker.check_core_dependencies",
            ) as mock_deps:
                mock_deps.return_value = {"available": True}
                with patch(
                    "src.agent.agent_health_checker.check_agent_directory_structure",
                ) as mock_dir:
                    mock_dir.side_effect = [
                        {"available": True},
                        {"available": False},
                    ]
                    with patch(
                        "src.agent.agent_health_checker.check_agent_yaml_config",
                    ) as mock_yaml:
                        mock_yaml.side_effect = [
                            {"available": True},
                            {"available": False},
                        ]
                        with patch(
                            "src.agent.agent_health_checker.check_agent_import_path",
                        ) as mock_import_path:
                            mock_import_path.side_effect = [
                                {"available": True},
                                {"available": False},
                            ]
                            result = lightweight_agent_health_check()

        assert result["overall_status"] == "degraded"
        assert "部分Agent不可用" in result["overall_message"]
        assert "unhealthy-agent" in result["unhealthy_agents"]
        assert result["healthy_agents_count"] == 1

    def test_health_check_all_agents_unhealthy(self) -> None:
        """测试所有Agent不健康时状态为unhealthy."""
        with patch(
            "src.agent.agents_implementations.get_available_agents",
        ) as mock_get:
            mock_get.return_value = ["agent1", "agent2"]
            with patch(
                "src.agent.agent_health_checker.check_core_dependencies",
            ) as mock_deps:
                mock_deps.return_value = {"available": True}
                with patch(
                    "src.agent.agent_health_checker.check_agent_directory_structure",
                ) as mock_dir:
                    mock_dir.return_value = {"available": False}
                    with patch(
                        "src.agent.agent_health_checker.check_agent_yaml_config",
                    ) as mock_yaml:
                        mock_yaml.return_value = {"available": False}
                        with patch(
                            "src.agent.agent_health_checker.check_agent_import_path",
                        ) as mock_import_path:
                            mock_import_path.return_value = {"available": False}
                            result = lightweight_agent_health_check()

        assert result["overall_status"] == "unhealthy"
        assert "所有Agent都不可用" in result["overall_message"]
        assert result["healthy_agents_count"] == 0

    def test_health_check_agent_check_exception(self) -> None:
        """测试单个Agent检查抛出异常时优雅降级."""
        with patch(
            "src.agent.agents_implementations.get_available_agents",
        ) as mock_get:
            mock_get.return_value = ["error-agent"]
            with patch(
                "src.agent.agent_health_checker.check_core_dependencies",
            ) as mock_deps:
                mock_deps.return_value = {"available": True}
                with patch(
                    "src.agent.agent_health_checker.check_agent_directory_structure",
                ) as mock_dir:
                    mock_dir.side_effect = Exception("检查异常")
                    result = lightweight_agent_health_check()

        assert result["agents"]["error-agent"]["healthy"] is False
        assert "健康检查执行失败" in result["agents"]["error-agent"]["error"]

    def test_health_check_get_agents_exception_fallback(self) -> None:
        """测试获取Agent列表异常时回退到默认Agent."""
        with patch(
            "src.agent.agents_implementations.get_available_agents",
        ) as mock_get:
            mock_get.side_effect = Exception("获取Agent列表失败")
            with patch(
                "src.agent.agent_health_checker.check_core_dependencies",
            ) as mock_deps:
                mock_deps.return_value = {"available": True}
                with patch(
                    "src.agent.agent_health_checker.check_agent_directory_structure",
                ) as mock_dir:
                    mock_dir.return_value = {"available": True}
                    with patch(
                        "src.agent.agent_health_checker.check_agent_yaml_config",
                    ) as mock_yaml:
                        mock_yaml.return_value = {"available": True}
                        with patch(
                            "src.agent.agent_health_checker.check_agent_import_path",
                        ) as mock_import_path:
                            mock_import_path.return_value = {
                                "available": True,
                            }
                            result = lightweight_agent_health_check()

        assert "personal_assistant" in result["agent_system"]["agent_ids"]
        assert result["agents_checked"] >= 1
