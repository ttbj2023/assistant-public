"""配置治理工具测试."""

from __future__ import annotations

import ast

from scripts.config_doctor import (
    _env_read_kind,
    _merge_tools_overlay,
    check_deprecated_fields,
    check_dict_categories_not_none,
    check_notification_defaults,
    migrate_config,
)


def test_env_read_kind_detects_os_getenv() -> None:
    tree = ast.parse('import os\nvalue = os.getenv("DEBUG")\n')
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert _env_read_kind(calls[0]) == "os.getenv"


def test_migrate_config_removes_secret_fields() -> None:
    migrated, _issues = migrate_config({
        "api": {"file_signing_secret": "secret"},
        "openclaw": {"gateway": {"url": "http://x", "token": "token"}},
    })

    assert "file_signing_secret" not in migrated["api"]
    assert "token" not in migrated["openclaw"]["gateway"]


def test_check_deprecated_flags_scheduled_messenger_dead_config_as_error() -> None:
    """scheduled_messenger.config 下的 openclaw_defaults/smtp_config 已迁移到系统级, 应报 ERROR."""
    config = {
        "tools": {
            "internal_tools": {
                "scheduled_messenger": {
                    "config": {
                        "openclaw_defaults": {"weixin": {"channel": "x"}},
                        "smtp_config": {"host": "smtp.qq.com"},
                    },
                },
            },
        },
    }
    issues = check_deprecated_fields(config)
    paths = {i.path for i in issues if i.code == "DEPRECATED_FIELD"}
    assert "tools.internal_tools.scheduled_messenger.config.openclaw_defaults" in paths
    assert "tools.internal_tools.scheduled_messenger.config.smtp_config" in paths


def test_migrate_removes_scheduled_messenger_dead_config() -> None:
    """migrate 应清理死配置, 但保留同段其他有效字段."""
    config = {
        "tools": {
            "internal_tools": {
                "scheduled_messenger": {
                    "config": {
                        "openclaw_defaults": {"weixin": {"channel": "x"}},
                        "smtp_config": {"host": "smtp.qq.com"},
                        "default_channel": "wechat",
                    },
                },
            },
        },
    }
    migrated, _issues = migrate_config(config)
    cfg = migrated["tools"]["internal_tools"]["scheduled_messenger"]["config"]
    assert "openclaw_defaults" not in cfg
    assert "smtp_config" not in cfg
    assert cfg.get("default_channel") == "wechat"


def test_check_notification_defaults_warns_when_openclaw_section_missing() -> None:
    """缺整个 openclaw 段 (notification_defaults 无默认值) 应 WARNING."""
    issues = check_notification_defaults({})
    assert len(issues) == 1
    assert issues[0].severity == "WARNING"


def test_check_notification_defaults_warns_when_empty() -> None:
    """notification_defaults 显式空应 WARNING (微信渠道派发将静默失败)."""
    issues = check_notification_defaults(
        {"openclaw": {"notification_defaults": {}}},
    )
    assert len(issues) == 1
    assert issues[0].severity == "WARNING"


def test_check_notification_defaults_pass_when_configured() -> None:
    """notification_defaults 已配置应无 issue."""
    config = {
        "openclaw": {
            "notification_defaults": {"weixin": {"channel": "openclaw-weixin"}},
        },
    }
    assert check_notification_defaults(config) == []


class TestMergeToolsOverlayNoneDefense:
    """_merge_tools_overlay 必须与 tools_config.from_module_config 行为一致,
    dict 类别字段为 None 时跳过(不整体覆盖默认 catalog).
    """

    def test_internal_tools_none_keeps_default(self) -> None:
        """internal_tools: None 不应整体覆盖默认 catalog."""
        base = {
            "internal_tools": {"create_todo": {"name": "create_todo"}},
            "external_tools": {},
        }
        overlay = {"internal_tools": None}
        merged = _merge_tools_overlay(base, overlay)
        assert merged["internal_tools"] == {
            "create_todo": {"name": "create_todo"},
        }
        assert merged["external_tools"] == {}

    def test_multiple_dict_categories_none_simultaneously(self) -> None:
        """多个 dict 类别同时为 None 全部跳过."""
        base = {
            "internal_tools": {"a": {"name": "a"}},
            "external_tools": {"b": {"name": "b"}},
            "tool_groups": {"g_group": {"name": "g_group"}},
            "mcp_servers": {},
            "skills": {},
        }
        overlay = {
            "internal_tools": None,
            "external_tools": None,
            "tool_groups": None,
            "mcp_servers": None,
            "skills": None,
        }
        merged = _merge_tools_overlay(base, overlay)
        assert merged["internal_tools"] == {"a": {"name": "a"}}
        assert merged["external_tools"] == {"b": {"name": "b"}}
        assert merged["tool_groups"] == {"g_group": {"name": "g_group"}}

    def test_non_dict_value_in_dict_category_warns_and_skips(self, caplog) -> None:
        """dict 类别字段给了非 dict 非 None 值(如字符串)应 warn 并跳过."""
        base = {"internal_tools": {"a": {"name": "a"}}, "external_tools": {}}
        overlay = {"internal_tools": "not_a_dict"}
        with caplog.at_level("WARNING"):
            merged = _merge_tools_overlay(base, overlay)
        assert merged["internal_tools"] == {"a": {"name": "a"}}


class TestCheckDictCategoriesNotNone:
    """新增治理规则: tools 下 5 个 dict 类别字段禁止显式写 None.

    防止 YAML 写 `internal_tools:` 空键导致合并逻辑被绕过(即便有运行时防御,
    配置层面也应明示报错, 让用户删除空键而非依赖代码兜底).
    """

    def test_internal_tools_none_reports_error(self) -> None:
        config = {"tools": {"internal_tools": None}}
        issues = check_dict_categories_not_none(config)
        assert len(issues) == 1
        assert issues[0].severity == "ERROR"
        assert issues[0].code == "DICT_CATEGORY_NONE"
        assert "internal_tools" in issues[0].path

    def test_all_five_categories_none_reports_five_errors(self) -> None:
        config = {
            "tools": {
                "internal_tools": None,
                "external_tools": None,
                "tool_groups": None,
                "mcp_servers": None,
                "skills": None,
            },
        }
        issues = check_dict_categories_not_none(config)
        assert len(issues) == 5
        paths = {i.path for i in issues}
        assert paths == {
            "tools.internal_tools",
            "tools.external_tools",
            "tools.tool_groups",
            "tools.mcp_servers",
            "tools.skills",
        }

    def test_empty_dict_passes(self) -> None:
        """空 dict 是合法的(表示显式清空 catalog), 不应报错."""
        config = {"tools": {"internal_tools": {}}}
        assert check_dict_categories_not_none(config) == []

    def test_no_tools_section_passes(self) -> None:
        """无 tools 段不报错(走内置 catalog)."""
        assert check_dict_categories_not_none({}) == []

    def test_dict_with_entries_passes(self) -> None:
        """正常 dict 配置不报错."""
        config = {
            "tools": {
                "internal_tools": {"create_todo": {"name": "create_todo"}},
            },
        }
        assert check_dict_categories_not_none(config) == []
