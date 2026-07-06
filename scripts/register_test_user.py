#!/usr/bin/env python3
"""交互式测试用户注册脚本 (运维工具)

启动交互式终端会话, 微信扫码后自动完成 OpenClaw 渠道绑定与 assistant 用户注册:
1. 输入用户信息 (user_id + 显示名称)
2. openclaw channels login 生成微信二维码, 等待扫码
3. 扫码后自动写入 static_users.yaml (用户/API Key) 和 openclaw.json (provider/agent/binding)
4. 重启服务使配置生效 (mac 模式: docker 重启 assistant + launchd 重启 gateway; local 模式提示手动重启)

两种模式互相独立, 操作各自机器的 OpenClaw 渠道与 assistant 用户:
- mac:   Mac mini 生产环境 (脚本所在项目目录, homebrew openclaw, launchd 重启)
- local: 本机开发环境 (脚本所在项目目录, PATH 中的 openclaw, 手动重启)

默认根据平台自动探测 (Darwin + homebrew openclaw => mac, 否则 local),
可用 --env 显式覆盖.

用法:
    python scripts/register_test_user.py              # 自动探测环境
    python scripts/register_test_user.py --env mac    # 强制 Mac 生产模式
    python scripts/register_test_user.py --env local  # 强制本机开发模式
    python scripts/register_test_user.py --check      # 仅检查前置条件后退出

前置条件:
- Python 3.12+
- PyYAML: pip install pyyaml
- openclaw 已安装且 gateway 已运行
- assistant 后端已运行 (http://127.0.0.1:8000)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
OPENCLAW_DIR = Path.home() / ".openclaw"
WEIXIN_DIR = OPENCLAW_DIR / "openclaw-weixin"
OPENCLAW_CONFIG = OPENCLAW_DIR / "openclaw.json"
STATIC_USERS_FILE = PROJECT_DIR / "src" / "auth" / "static_users.yaml"
ASSISTANT_BASE_URL = "http://127.0.0.1:8000/v1"
MAC_OPENCLAW_BIN = "/opt/homebrew/bin/openclaw"
LAUNCHD_GATEWAY_LABEL = "ai.openclaw.gateway"

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_CYAN = "\033[36m"


def print_header(text: str) -> None:
    print(f"\n{ANSI_BOLD}{ANSI_CYAN}{'=' * 50}{ANSI_RESET}")
    print(f"{ANSI_BOLD}{ANSI_CYAN}  {text}{ANSI_RESET}")
    print(f"{ANSI_BOLD}{ANSI_CYAN}{'=' * 50}{ANSI_RESET}\n")


def print_step(step: int, text: str) -> None:
    print(f"\n{ANSI_BOLD}[Step {step}] {text}{ANSI_RESET}")


def print_ok(text: str) -> None:
    print(f"  {ANSI_GREEN}✓{ANSI_RESET} {text}")


def print_err(text: str) -> None:
    print(f"  {ANSI_RED}✗{ANSI_RESET} {text}")


def print_warn(text: str) -> None:
    print(f"  {ANSI_YELLOW}!{ANSI_RESET} {text}")


def detect_env() -> str:
    if platform.system() == "Darwin" and Path(MAC_OPENCLAW_BIN).is_file():
        return "mac"
    return "local"


def resolve_openclaw_bin(env: str) -> str:
    if env == "mac" and Path(MAC_OPENCLAW_BIN).is_file():
        return MAC_OPENCLAW_BIN
    return shutil.which("openclaw") or "openclaw"


def hash_identifier(identifier: str) -> str:
    safe_id = re.sub(r"[^\w\-_.@]", "_", identifier)
    if safe_id and safe_id[0].isdigit():
        safe_id = f"id_{safe_id}"
    safe_id = safe_id[:100] or "default"
    hash_obj = hashlib.sha256(safe_id.encode("utf-8"))
    return hash_obj.hexdigest()[:12]


def generate_api_key(user_id: str, thread_id: str = "main") -> str:
    user_hash = hash_identifier(user_id)
    thread_hash = hash_identifier(thread_id)
    random_suffix = secrets.token_urlsafe(12)
    return f"sk-project-{user_hash}-{thread_hash}-{random_suffix}"


def validate_user_id(user_id: str) -> str | None:
    if not user_id:
        return "user_id 不能为空"
    if not re.match(r"^[a-zA-Z0-9_-]+$", user_id):
        return "user_id 只能包含英文字母、数字、下划线、连字符"
    if len(user_id) > 64:
        return "user_id 长度不能超过 64"
    return None


def load_static_users() -> dict:
    with open(STATIC_USERS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_static_users(data: dict) -> None:
    with open(STATIC_USERS_FILE, "w", encoding="utf-8") as f:
        yaml.dump(
            data, f, default_flow_style=False, allow_unicode=True, sort_keys=False
        )


def load_openclaw_config() -> dict:
    with open(OPENCLAW_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def save_openclaw_config(config: dict) -> None:
    with open(OPENCLAW_CONFIG, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")


def get_existing_account_ids() -> set[str]:
    accounts_file = WEIXIN_DIR / "accounts.json"
    if not accounts_file.exists():
        return set()
    with open(accounts_file, encoding="utf-8") as f:
        return set(json.load(f))


def wait_for_new_account(existing_ids: set[str], timeout: int = 300) -> str | None:
    print_warn(f"等待微信扫码 (超时 {timeout} 秒)...")
    start = time.time()
    while time.time() - start < timeout:
        current_ids = get_existing_account_ids()
        new_ids = current_ids - existing_ids
        if new_ids:
            account_id = new_ids.pop()
            return account_id
        time.sleep(2)
    return None


def run_channels_login(env: str) -> subprocess.Popen | None:
    openclaw_bin = resolve_openclaw_bin(env)
    try:
        env_vars = os.environ.copy()
        if env == "mac":
            env_vars["PATH"] = f"/opt/homebrew/bin:{env_vars.get('PATH', '')}"
        return subprocess.Popen(
            [openclaw_bin, "channels", "login", "--channel", "openclaw-weixin"],
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            env=env_vars,
        )
    except FileNotFoundError:
        return None


def _launchctl_kickstart(label: str) -> bool:
    uid = os.getuid()
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def restart_assistant() -> bool:
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "docker/docker-compose.production.yml",
                "restart",
                "app",
            ],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0 and result.stderr:
            print_err(f"docker compose stderr: {result.stderr.strip()}")
        return result.returncode == 0
    except Exception as e:
        print_err(f"docker compose 执行异常: {e}")
        return False


def restart_gateway() -> bool:
    return _launchctl_kickstart(LAUNCHD_GATEWAY_LABEL)


def check_prerequisites(env: str) -> bool:
    errors = []
    if not STATIC_USERS_FILE.exists():
        errors.append(f"static_users.yaml 不存在: {STATIC_USERS_FILE}")
    if not OPENCLAW_CONFIG.exists():
        errors.append(f"openclaw.json 不存在: {OPENCLAW_CONFIG}")
    if not WEIXIN_DIR.exists():
        errors.append(f"openclaw-weixin 目录不存在: {WEIXIN_DIR}")

    openclaw_bin = resolve_openclaw_bin(env)
    if not Path(openclaw_bin).is_file():
        errors.append(f"openclaw 二进制不存在: {openclaw_bin}")

    if errors:
        print_err("前置条件检查失败:")
        for e in errors:
            print(f"    - {e}")
        return False

    print_ok("前置条件检查通过")
    return True


def step1_input_user_info() -> tuple[str, str]:
    print_step(1, "输入用户信息")
    while True:
        user_id = input("  用户ID (英文/数字/下划线): ").strip()
        err = validate_user_id(user_id)
        if err:
            print_err(err)
            continue

        users_data = load_static_users()
        if user_id in users_data.get("users", {}):
            print_err(f"用户 '{user_id}' 已存在")
            continue
        break

    display_name = input("  显示名称: ").strip() or user_id
    print_ok(f"用户: {user_id} ({display_name})")
    return user_id, display_name


def step2_qr_login(env: str) -> str | None:
    print_step(2, "微信扫码绑定")
    print_warn("即将执行 openclaw channels login, 请在终端中扫描二维码\n")

    existing_ids = get_existing_account_ids()

    proc = run_channels_login(env)
    if proc is None:
        print_err(f"无法启动 openclaw, 请确认 {resolve_openclaw_bin(env)} 存在")
        return None

    proc.wait()

    if proc.returncode != 0:
        print_err(f"channels login 失败 (exit code: {proc.returncode})")
        return None

    print_ok("channels login 命令已完成")

    account_id = wait_for_new_account(existing_ids)
    if account_id is None:
        print_err("等待扫码超时")
        return None

    print_ok(f"检测到新账号: {account_id}")
    return account_id


def step3_register(
    user_id: str, display_name: str, account_id: str, env: str
) -> str | None:
    print_step(3, "自动完成注册")

    # 3a: 生成 API Key
    api_key = generate_api_key(user_id)
    print_ok(f"API Key: {api_key}")

    # 3b: 更新 static_users.yaml
    try:
        users_data = load_static_users()
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000000")
        users_data.setdefault("users", {})[user_id] = {
            "user_id": user_id,
            "display_name": display_name,
            "email": f"{user_id}@example.com",
            "timezone": "Asia/Shanghai",
            "status": "active",
            "threads": [
                {
                    "api_key": api_key,
                    "thread_id": "main",
                    "description": f"{display_name}主工作线程",
                    "created_at": now,
                    "last_used_at": None,
                    "usage_count": 0,
                    "expires_at": None,
                    "is_active": True,
                    "key_name": f"{user_id}_main_thread",
                }
            ],
        }
        save_static_users(users_data)
        print_ok("static_users.yaml 已更新")
    except Exception as e:
        print_err(f"更新 static_users.yaml 失败: {e}")
        return None

    # 3c: 更新 openclaw.json
    try:
        config = load_openclaw_config()

        provider_name = user_id
        agent_id = f"{user_id}-assistant"

        config.setdefault("models", {}).setdefault("providers", {})[provider_name] = {
            "baseUrl": ASSISTANT_BASE_URL,
            "api": "openai-completions",
            "apiKey": api_key,
            "models": [
                {
                    "id": "personal-assistant",
                    "name": f"Personal Assistant ({display_name})",
                    "input": ["text"],
                    "contextWindow": 128000,
                    "maxTokens": 4096,
                }
            ],
        }

        config.setdefault("agents", {}).setdefault("list", []).append({
            "id": agent_id,
            "name": f"{display_name} Assistant",
            "default": False,
            "model": {"primary": f"{provider_name}/personal-assistant"},
            "skills": [],
            "tools": {},
            "heartbeat": {"every": ""},
        })

        config.setdefault("bindings", []).append({
            "agentId": agent_id,
            "match": {
                "channel": "openclaw-weixin",
                "accountId": account_id,
            },
        })

        save_openclaw_config(config)
        print_ok(f"openclaw.json 已更新 (provider={provider_name}, agent={agent_id})")
    except Exception as e:
        print_err(f"更新 openclaw.json 失败: {e}")
        return None

    # 3d/3e: 重启服务 (仅 mac 模式自动重启: assistant 走 docker, gateway 走 launchd)
    if env == "mac":
        print_warn("重启 assistant 服务...")
        if restart_assistant():
            print_ok("assistant 服务已重启")
        else:
            print_err("assistant 服务重启失败, 请手动重启")
            return api_key

        print_warn("重启 OpenClaw gateway...")
        if restart_gateway():
            print_ok("gateway 已重启")
        else:
            print_err("gateway 重启失败, 请手动重启")
            return api_key
    else:
        print_warn(
            "local 模式不自动重启, 请手动重启 assistant 与 OpenClaw gateway 以加载新配置"
        )

    return api_key


def main() -> None:
    parser = argparse.ArgumentParser(
        description="交互式测试用户注册 (运维工具): 微信扫码自动绑定 OpenClaw 渠道并注册 assistant 用户"
    )
    parser.add_argument(
        "--env",
        choices=["mac", "local"],
        help="目标环境 (默认自动探测: Darwin + homebrew openclaw => mac)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅检查前置条件与解析路径后退出",
    )
    args = parser.parse_args()

    env = args.env or detect_env()

    print_header(f"测试用户注册 [{env} 模式]")

    if args.check:
        ok = check_prerequisites(env)
        if ok:
            print_ok(f"环境:       {env}")
            print_ok(f"项目目录:   {PROJECT_DIR}")
            print_ok(f"openclaw:   {resolve_openclaw_bin(env)}")
            print_ok(f"用户配置:   {STATIC_USERS_FILE}")
            print_ok(f"渠道配置:   {OPENCLAW_CONFIG}")
            print_ok(f"weixin目录: {WEIXIN_DIR}")
        sys.exit(0 if ok else 1)

    if not check_prerequisites(env):
        sys.exit(1)

    user_id, display_name = step1_input_user_info()

    account_id = step2_qr_login(env)
    if account_id is None:
        print_err("注册中止: 未获取到微信账号")
        sys.exit(1)

    api_key = step3_register(user_id, display_name, account_id, env)
    if api_key is None:
        print_err("注册中止: 自动配置失败")
        sys.exit(1)

    print_header("注册完成")
    print(f"  用户ID:    {user_id}")
    print(f"  显示名称:  {display_name}")
    print(f"  微信账号:  {account_id}")
    print(f"  API Key:   {api_key}")
    print("\n  用户现在可以通过微信直接与 Personal Assistant 对话了!\n")


if __name__ == "__main__":
    main()
