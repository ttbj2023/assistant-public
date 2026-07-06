#!/usr/bin/env python3
"""配置治理检查与迁移工具.

默认不输出任何配置值或 secret, 只报告字段路径、变量名和问题类型.
"""

from __future__ import annotations

import argparse
import ast
import copy
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ALLOWED_ENV_READ_FILES = {
    Path("src/config/runtime_env.py"),
    Path("src/config/env_manager.py"),
    Path("src/config/credentials_registry.py"),
    Path("src/inference/llm/definitions/provider_registry.py"),
    Path("src/config/tools_config.py"),
    Path("src/tools/mcp/mcp_tool_manager.py"),
}

DEPRECATED_FIELD_PATHS = {
    "api.file_signing_secret": "FILE_SIGNING_SECRET 只允许放在 .env",
    "openclaw.gateway.token": "OPENCLAW_GATEWAY_TOKEN 只允许放在 .env",
    "storage.database": "数据库路径由 path_resolver 管理, 当前仅保留 storage.file_store",
    "tools.internal_tools.scheduled_messenger.config.openclaw_defaults": (
        "openclaw 渠道默认已迁移到 openclaw.notification_defaults"
    ),
    "tools.internal_tools.scheduled_messenger.config.smtp_config": (
        "SMTP 已迁移到系统级 smtp 段"
    ),
    "core.cache.settings_cache_size": "配置序列化缓存已移除",
    "core.cache.llm_client_cache_size": "LLM 客户端缓存不再通过 core.cache 配置",
    "core.cache.embedding_client_cache_size": "Embedding 客户端缓存不再通过 core.cache 配置",
    "core.cache.tool_cache_size": "工具缓存不再通过 core.cache 配置",
    "core.cache.path_cache_size": "路径缓存不再通过 core.cache 配置",
    "core.cache.connection_cache_size": "连接缓存不再通过 core.cache 配置",
}

RENAMED_TOOLS = {
    "examine_attachment": "read_file",
    "read_image": "analyze_image",
}


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    path: str = ""

    def format(self) -> str:
        prefix = f"[{self.severity}] {self.code}"
        if self.path:
            prefix += f" {self.path}"
        return f"{prefix}: {self.message}"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层必须是 mapping")
    return data


def _iter_paths(data: Any, prefix: str = ""):
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, value
            yield from _iter_paths(value, path)


def validate_app_config(config: dict[str, Any]) -> list[Issue]:
    from src.config.app_config import AppConfig

    issues: list[Issue] = []
    try:
        AppConfig(**config)
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(part) for part in err.get("loc", ()))
            issues.append(
                Issue(
                    severity="ERROR",
                    code="CONFIG_SCHEMA",
                    path=loc,
                    message=str(err.get("msg", "配置字段无效")),
                )
            )
    return issues


def check_deprecated_fields(config: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    present = {path for path, _ in _iter_paths(config)}
    for path, message in DEPRECATED_FIELD_PATHS.items():
        if path in present:
            issues.append(
                Issue(
                    severity="ERROR",
                    code="DEPRECATED_FIELD",
                    path=path,
                    message=message,
                )
            )
    return issues


def check_notification_defaults(config: dict[str, Any]) -> list[Issue]:
    """检查 openclaw.notification_defaults (微信渠道通知派发的系统级依赖).

    微信渠道(超长回复补发/定时消息/价格监控派发)经 resolve_delivery 读取此配置
    解析系统级渠道名; 缺失或为空时这些功能会静默失败(日志告警但消息不发出).
    因 config.yaml 被 gitignore, 生产环境易漏配, 故作为 WARNING 提示.
    """
    openclaw = config.get("openclaw")
    if not isinstance(openclaw, dict):
        return [
            Issue(
                severity="WARNING",
                code="MISSING_NOTIFICATION_DEFAULTS",
                path="openclaw",
                message=(
                    "缺 openclaw 段; 微信渠道(超长回复补发/定时消息/价格监控派发)"
                    "将静默失败, 见 docs/config-yaml-template.yaml 的 openclaw.notification_defaults"
                ),
            ),
        ]
    defaults = openclaw.get("notification_defaults")
    if not isinstance(defaults, dict) or not defaults:
        return [
            Issue(
                severity="WARNING",
                code="MISSING_NOTIFICATION_DEFAULTS",
                path="openclaw.notification_defaults",
                message=(
                    "为空或缺失; 微信渠道(超长回复补发/定时消息/价格监控派发)将静默失败"
                ),
            ),
        ]
    return []


def check_dict_categories_not_none(config: dict[str, Any]) -> list[Issue]:
    """检查 tools 段下 5 个 dict 类别字段是否被显式写为 None.

    YAML 写 `internal_tools:` 紧跟非缩进内容会被解析为 None, 是高频配置事故源
    (历史 bug: 全工具丢失). 即便运行时有 None 防御, 配置层面也应显式报错,
    让用户删除空键或填合理值, 而非依赖代码兜底.
    """
    dict_categories = (
        "internal_tools",
        "external_tools",
        "tool_groups",
        "mcp_servers",
        "skills",
    )
    issues: list[Issue] = []
    tools = config.get("tools")
    if not isinstance(tools, dict):
        return issues
    for key in dict_categories:
        if key in tools and tools[key] is None:
            issues.append(
                Issue(
                    severity="ERROR",
                    code="DICT_CATEGORY_NONE",
                    path=f"tools.{key}",
                    message=(
                        "显式 None 不允许; 删除此空键走内置 catalog 默认, "
                        "或填 {}(清空)/具体 dict(覆盖). "
                        "历史事故: 空键导致 Pydantic 校验崩溃, Agent 全工具丢失."
                    ),
                ),
            )
    return issues


def check_agent_references(config: dict[str, Any]) -> list[Issue]:
    from src.agent.agents_implementations import get_available_agents
    from src.agent.factory import AgentFactory
    from src.config.agent_config import AgentConfig
    from src.config.tools_config import ToolsConfig
    from src.tools.experts import EXPERT_TOOL_NAMES

    issues: list[Issue] = []
    tools_cfg = ToolsConfig.from_dict(
        _merge_tools_overlay(ToolsConfig.get_default_config(), config.get("tools", {}))
    )
    internal = {t.name for t in tools_cfg.internal_tools.values() if t.enabled}
    external = {t.name for t in tools_cfg.external_tools.values() if t.enabled}
    groups = set(tools_cfg.tool_groups)
    skills = {name for name, cfg in tools_cfg.skills.items() if cfg.enabled}
    mcp_tools: set[str] = set()
    for server in tools_cfg.mcp_servers.values():
        if server.enabled:
            mcp_tools.update(server.tool_names.values())
    known_tools = internal | external | groups | set(EXPERT_TOOL_NAMES) | mcp_tools

    factory = AgentFactory()
    for agent_id in get_available_agents():
        try:
            path = factory._resolve_config_path(agent_id)
            agent_cfg = AgentConfig(**_load_yaml(path))
        except Exception as e:
            issues.append(
                Issue(
                    severity="ERROR",
                    code="AGENT_CONFIG",
                    path=agent_id,
                    message=f"Agent 配置无法加载: {e}",
                )
            )
            continue
        for field in ("tools", "optional_tools"):
            for tool_name in getattr(agent_cfg, field):
                renamed = RENAMED_TOOLS.get(tool_name)
                if renamed:
                    issues.append(
                        Issue(
                            severity="ERROR",
                            code="RENAMED_TOOL",
                            path=f"{agent_id}.{field}",
                            message=f"{tool_name} 已更名为 {renamed}",
                        )
                    )
                elif tool_name not in known_tools:
                    issues.append(
                        Issue(
                            severity="ERROR",
                            code="UNKNOWN_TOOL",
                            path=f"{agent_id}.{field}",
                            message=f"未知工具或工具组: {tool_name}",
                        )
                    )
        for skill_name in agent_cfg.skills:
            if skill_name not in skills:
                issues.append(
                    Issue(
                        severity="ERROR",
                        code="UNKNOWN_SKILL",
                        path=f"{agent_id}.skills",
                        message=f"未知或未启用 skill: {skill_name}",
                    )
                )
    return issues


def _merge_tools_overlay(
    base: dict[str, Any],
    overlay: Any,
) -> dict[str, Any]:
    """按运行时规则合并内置工具 catalog 与 config.yaml overlay.

    与 src/config/tools_config.py 的 from_module_config 保持一致行为:
    dict 类别字段为 None 时跳过(保留 base 默认), 不整体覆盖.
    """
    if not isinstance(overlay, dict):
        return base
    merged = copy.deepcopy(base)
    deep_merge_categories = {
        "tool_groups",
        "internal_tools",
        "external_tools",
        "mcp_servers",
        "skills",
    }
    for key, value in overlay.items():
        if key in deep_merge_categories:
            if value is None:
                # 显式空键(如 `internal_tools:`), 跳过保留默认
                continue
            if not isinstance(value, dict):
                # 类型错误, 跳过并记日志(运行时也会 warn)
                print(
                    f"[WARNING] tools.{key} 应为 dict, 实际 {type(value).__name__}; 跳过",
                    file=sys.stderr,
                )
                continue
            bucket = merged.setdefault(key, {})
            for name, item_config in value.items():
                if (
                    name in bucket
                    and isinstance(bucket[name], dict)
                    and isinstance(item_config, dict)
                ):
                    bucket[name] = _deep_merge(bucket[name], item_config)
                else:
                    bucket[name] = item_config
        else:
            merged[key] = value
    return merged


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def check_env_reads() -> list[Issue]:
    issues: list[Issue] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(PROJECT_ROOT)
        if rel in ALLOWED_ENV_READ_FILES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            issues.append(Issue("ERROR", "PY_PARSE", str(e), str(rel)))
            continue
        for node in ast.walk(tree):
            hit = _env_read_kind(node)
            if hit:
                issues.append(
                    Issue(
                        severity="ERROR",
                        code="DIRECT_ENV_READ",
                        path=f"{rel}:{node.lineno}",
                        message=(
                            f"{hit} 不允许直接使用; 请通过 runtime_env / "
                            "credentials_registry / provider_registry"
                        ),
                    )
                )
    return issues


def _env_read_kind(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if (
            node.func.attr == "getenv"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
        ):
            return "os.getenv"
        if node.func.attr == "get" and isinstance(node.func.value, ast.Attribute):
            value = node.func.value
            if (
                value.attr == "environ"
                and isinstance(value.value, ast.Name)
                and value.value.id == "os"
            ):
                return "os.environ.get"
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
        value = node.value
        if (
            value.attr == "environ"
            and isinstance(value.value, ast.Name)
            and value.value.id == "os"
        ):
            return "os.environ[]"
    return None


def migrate_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[Issue]]:
    migrated = dict(config)
    issues: list[Issue] = []

    _drop_path(migrated, "api.file_signing_secret")
    _drop_path(migrated, "openclaw.gateway.token")
    _drop_path(migrated, "storage.database")
    _drop_path(
        migrated, "tools.internal_tools.scheduled_messenger.config.openclaw_defaults"
    )
    _drop_path(migrated, "tools.internal_tools.scheduled_messenger.config.smtp_config")
    for path in list(DEPRECATED_FIELD_PATHS):
        if path.startswith("core.cache."):
            _drop_path(migrated, path)

    tools = migrated.get("tools")
    if isinstance(tools, dict):
        for bucket_name in ("internal_tools", "external_tools"):
            bucket = tools.get(bucket_name)
            if isinstance(bucket, dict):
                for old, new in RENAMED_TOOLS.items():
                    if old in bucket:
                        old_cfg = bucket.pop(old)
                        bucket.setdefault(new, old_cfg)
                        if isinstance(bucket[new], dict):
                            bucket[new]["name"] = new
                        issues.append(
                            Issue(
                                "WARNING",
                                "MIGRATED_TOOL",
                                f"{old} 已迁移为 {new}",
                                f"tools.{bucket_name}",
                            )
                        )

    return migrated, issues


def _drop_path(data: dict[str, Any], dotted: str) -> None:
    parts = dotted.split(".")
    current: Any = data
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(part)
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _write_migrated_config(path: Path, config: dict[str, Any]) -> Path:
    backup = path.with_name(
        f"{path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    shutil.copy2(path, backup)
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return backup


def run(args: argparse.Namespace) -> int:
    config_path = PROJECT_ROOT / "config.yaml"
    config = _load_yaml(config_path)
    issues: list[Issue] = []

    if args.migrate:
        migrated, migration_issues = migrate_config(config)
        issues.extend(migration_issues)
        if args.write:
            backup = _write_migrated_config(config_path, migrated)
            print(f"已备份: {backup.relative_to(PROJECT_ROOT)}")
            config = migrated
        else:
            print("迁移预览完成; 使用 --write 写回 config.yaml")
            config = migrated

    if args.validate or args.strict or args.check_env:
        issues.extend(check_deprecated_fields(config))
        issues.extend(check_notification_defaults(config))
        issues.extend(check_dict_categories_not_none(config))
        issues.extend(validate_app_config(config))
        issues.extend(check_agent_references(config))
    if args.check_env or args.strict:
        issues.extend(check_env_reads())

    for issue in issues:
        print(issue.format())

    errors = [issue for issue in issues if issue.severity == "ERROR"]
    if errors:
        print(f"配置检查失败: {len(errors)} 个错误, {len(issues)} 个问题")
        return 1
    print(f"配置检查通过: {len(issues)} 个提示/警告")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="配置治理检查与迁移工具")
    parser.add_argument("--validate", action="store_true", help="校验 config.yaml")
    parser.add_argument("--strict", action="store_true", help="严格模式")
    parser.add_argument("--check-env", action="store_true", help="检查裸 env 读取")
    parser.add_argument("--migrate", action="store_true", help="迁移 config.yaml")
    parser.add_argument("--write", action="store_true", help="写回迁移结果")
    args = parser.parse_args()

    if not any((args.validate, args.strict, args.check_env, args.migrate)):
        args.validate = True

    sys.exit(run(args))


if __name__ == "__main__":
    main()
