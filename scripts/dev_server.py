#!/usr/bin/env python3
"""开发服务器启动脚本 - 简化版

实现线性流程：识别 → 清理 → 启动
支持多端口实例精准管理, 每个端口独立 PID 文件.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


def ensure_venv() -> None:
    """检测并自动切换到项目虚拟环境, 避免因未激活venv导致第三方库导入失败."""
    venv_python = Path(__file__).parent.parent / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return

    expected_prefix = str(Path(__file__).parent.parent / ".venv")
    if sys.prefix == expected_prefix:
        return

    print("🔄 当前未在项目虚拟环境中, 自动切换...")
    print(f"   当前: {sys.executable}")
    print(f"   目标: {venv_python}")
    os.execv(str(venv_python), [str(venv_python), *sys.argv])


ensure_venv()

import httpx
import uvicorn
from dotenv import load_dotenv


# 计算项目根目录（增强版本，支持多种环境变量）
def get_project_root() -> Path:
    """获取项目根目录路径，支持多种方式指定路径."""

    # 1. 优先使用PROJECT_ROOT环境变量（由e2e测试设置）
    if "PROJECT_ROOT" in os.environ:
        root_path = Path(os.environ["PROJECT_ROOT"]).resolve()
        if (root_path / "scripts" / "dev_server.py").exists():
            return root_path
        print(f"⚠️ PROJECT_ROOT环境变量指向的路径不正确: {root_path}")

    # 2. 使用脚本位置计算
    script_root = Path(__file__).parent.parent.resolve()
    if (script_root / "scripts" / "dev_server.py").exists():
        return script_root

    # 3. 使用当前工作目录
    cwd_root = Path.cwd()
    if (cwd_root / "scripts" / "dev_server.py").exists():
        return cwd_root

    # 4. 最后尝试当前目录的父目录
    parent_root = Path.cwd().parent
    if (parent_root / "scripts" / "dev_server.py").exists():
        return parent_root

    # 如果都失败了，返回脚本位置作为默认值
    print("⚠️ 无法自动确定项目根目录，使用脚本位置作为默认值")
    return script_root


# 获取项目根目录
project_root = get_project_root()

# 加载.env文件（使用绝对路径）
env_file = project_root / ".env"
if env_file.exists():
    load_dotenv(env_file)
    print(f"✅ 已加载环境变量文件: {env_file}")
else:
    print(f"ℹ️ 环境变量文件不存在: {env_file}")

# 添加项目根目录到Python路径
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
    print(f"✅ 已添加项目根目录到Python路径: {project_root}")

# 启动时打印关键信息（用于调试）
print(f"📁 项目根目录: {project_root}")
print("🔧 关键文件验证:")
key_files = [
    "scripts/dev_server.py",
    "src/auth/static_users.yaml",
    "src/api/fastapi_app.py",
    "config.yaml",
]

for rel_path in key_files:
    full_path = project_root / rel_path
    status = "✅" if full_path.exists() else "❌"
    print(f"  {status} {rel_path}: {full_path}")

# 移除不必要的配置导入

logger = logging.getLogger(__name__)

# 全局变量,用于保存服务器实例
server = None


class SimpleDevServer:
    """简化的开发服务器管理器.

    核心流程: 识别 → 清理 → 启动.
    支持按端口隔离 PID 文件, 实现多端口实例精准管理.
    """

    PID_DIR = "logs"
    LEGACY_PID_FILE = "logs/.dev_server.pid"

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._pid_dir = Path(self.PID_DIR)

    def _get_pid_file(self, port: int) -> Path:
        """获取指定端口的 PID 文件路径."""
        return self._pid_dir / f".dev_server.{port}.pid"

    def _read_pid_file(self, port: int) -> int | None:
        """读取指定端口的 PID 文件."""
        pid_file = self._get_pid_file(port)
        if not pid_file.exists():
            return None
        try:
            content = pid_file.read_text().strip()
            if content:
                return int(content)
        except Exception as e:
            self.logger.warning(f"⚠️ 读取 PID 文件失败 {pid_file}: {e}")
        return None

    def _write_pid_file(self, port: int, pid: int) -> None:
        """写入指定端口的 PID 文件."""
        self._pid_dir.mkdir(exist_ok=True)
        self._get_pid_file(port).write_text(str(pid))

    def _remove_pid_file(self, port: int) -> None:
        """删除指定端口的 PID 文件."""
        self._get_pid_file(port).unlink(missing_ok=True)

    def _cleanup_legacy_pid_file(self) -> None:
        """清理旧版单 PID 文件.

        旧版本使用 logs/.dev_server.pid 记录所有端口实例, 现在按端口隔离.
        仅处理文件, 不杀进程; 若旧进程仍占用端口, 后续 cleanup 会处理.
        """
        legacy_file = Path(self.LEGACY_PID_FILE)
        if not legacy_file.exists():
            return
        try:
            pid = int(legacy_file.read_text().strip())
            if pid == os.getpid():
                legacy_file.unlink(missing_ok=True)
                return
            if self._is_process_running(pid):
                self.logger.warning(
                    f"⚠️ 发现旧版 PID 文件指向进程 {pid}, "
                    f"建议使用对应端口的 --stop 命令清理"
                )
            else:
                legacy_file.unlink(missing_ok=True)
        except Exception:
            legacy_file.unlink(missing_ok=True)

    def check_port(self, host: str, port: int) -> bool:
        """检查端口是否被占用."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex((host, port))
                return result == 0
        except Exception:
            return False

    def _is_process_running(self, pid: int) -> bool:
        """检查进程是否仍在运行."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _pid_uses_port(self, pid: int, port: int) -> bool:
        """检查指定 PID 是否监听指定端口.

        优先通过 /proc/{pid}/net/tcp 读取, 失败时降级到 lsof.
        """
        # 优先使用 /proc, 不依赖外部命令
        try:
            tcp_file = Path(f"/proc/{pid}/net/tcp")
            if tcp_file.exists():
                hex_port = f"{port:04X}"
                content = tcp_file.read_text()
                # 格式: local_address ... 0A:1F90 -> 端口 8080
                return f":{hex_port}" in content
        except Exception:
            pass

        # 兜底: lsof
        try:
            result = subprocess.run(
                ["lsof", "-a", "-p", str(pid), "-i", f":{port}"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            return False

    def _kill_pid(self, pid: int) -> bool:
        """优雅终止指定 PID 的进程 (TERM → KILL)."""
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(10):
                time.sleep(0.5)
                if not self._is_process_running(pid):
                    return True
            os.kill(pid, signal.SIGKILL)
            for _ in range(5):
                time.sleep(0.5)
                if not self._is_process_running(pid):
                    return True
            return False
        except ProcessLookupError:
            return True
        except Exception as e:
            self.logger.warning(f"⚠️ 终止进程 {pid} 失败: {e}")
            return False

    async def check_dev_server_running(self, port: int) -> bool:
        """检查是否有 dev_server 在指定端口运行."""
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"http://localhost:{port}/health")
                return response.status_code == 200
        except Exception:
            return False

    def cleanup_dev_server(self, port: int) -> bool:
        """清理指定端口的 dev_server 进程.

        清理优先级:
        1. 当前端口 PID 文件中的进程
        2. pkill 匹配命令行中显式带 --port 的进程
        3. lsof 按端口兜底
        """
        try:
            self.logger.info(f"🧹 开始清理端口 {port} 上的 dev_server 进程...")

            # 1. 优先通过 PID 文件精准清理
            pid = self._read_pid_file(port)
            if pid and self._is_process_running(pid) and self._pid_uses_port(pid, port):
                self.logger.info(f"🔍 通过 PID 文件找到端口 {port} 的进程: {pid}")
                if self._kill_pid(pid):
                    self._remove_pid_file(port)
                    self.logger.info(f"✅ 端口 {port} 的进程已停止")
                    return True
                self.logger.warning(f"⚠️ PID 文件中的进程 {pid} 未能停止")

            # 清理过期 PID 文件
            if pid and not self._is_process_running(pid):
                self._remove_pid_file(port)

            # 2. pkill 兜底: 匹配命令行中显式带 --port 的进程
            result = subprocess.run(
                ["pkill", "-f", f"python.*dev_server.py.*--port {port}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                self.logger.info("✅ pkill 执行成功，等待进程退出...")
                time.sleep(1)

            # 3. 等待端口释放
            for _ in range(10):
                if not self.check_port("localhost", port):
                    self._remove_pid_file(port)
                    self.logger.info(f"✅ 端口 {port} 已释放")
                    return True
                time.sleep(1)

            # 4. 备用方案: lsof 按端口清理
            self.logger.warning(f"⚠️ 端口 {port} 仍未释放，使用 lsof 兜底...")
            try:
                result = subprocess.run(
                    ["lsof", "-ti", f":{port}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
                for pid_str in pids:
                    self._kill_pid(int(pid_str))

                if not self.check_port("localhost", port):
                    self._remove_pid_file(port)
                    self.logger.info(f"✅ 端口 {port} 已释放")
                    return True
            except subprocess.TimeoutExpired:
                self.logger.error("❌ lsof 命令超时")
            except FileNotFoundError:
                self.logger.warning("⚠️ lsof 命令不可用")

            # 最终检查
            return not self.check_port("localhost", port)

        except Exception as e:
            self.logger.error(f"❌ 清理过程中发生错误: {e}")
            return False

    def setup_pid_tracking(self, port: int) -> bool:
        """设置指定端口的 PID 跟踪和单实例保护."""
        try:
            self._pid_dir.mkdir(exist_ok=True)

            # 仅默认端口清理旧版 PID 文件提示
            if port == 8000:
                self._cleanup_legacy_pid_file()

            current_pid = os.getpid()
            existing_pid = self._read_pid_file(port)

            if existing_pid:
                if existing_pid == current_pid:
                    return True
                if self._is_process_running(existing_pid) and self._pid_uses_port(existing_pid, port):
                    self.logger.error(
                        f"❌ 端口 {port} 已有 dev_server 在运行 (PID: {existing_pid})"
                    )
                    return False
                # PID 文件过期
                self.logger.info(f"🧹 清理过期的 PID 文件: {existing_pid}")
                self._remove_pid_file(port)

            self._write_pid_file(port, current_pid)
            atexit.register(self.release_single_instance, port)
            self.logger.info(f"📍 PID 跟踪已设置 (端口: {port}, PID: {current_pid})")
            return True

        except Exception as e:
            self.logger.error(f"❌ PID 跟踪设置失败: {e}")
            return False

    def release_single_instance(self, port: int) -> None:
        """释放指定端口的单实例锁."""
        try:
            self._remove_pid_file(port)
            self.logger.info(f"🔓 端口 {port} 的单实例锁已释放")
        except Exception as e:
            self.logger.error(f"❌ PID 文件清理失败: {e}")

    async def show_status(self, port: int) -> None:
        """显示指定端口的服务状态."""
        print(f"🔍 检查端口 {port} 的服务状态...")

        pid = self._read_pid_file(port)
        if pid:
            running = self._is_process_running(pid)
            uses_port = self._pid_uses_port(pid, port) if running else False
            print(f"  PID 文件: {pid} ({'运行中' if running else '已退出'})")
            if running and uses_port:
                print("  端口占用: 是")
            elif running:
                print("  端口占用: 否 (PID 文件可能过期)")
            else:
                print("  端口占用: 否")

        if self.check_port("localhost", port):
            if await self.check_dev_server_running(port):
                print(f"✅ dev_server 正在端口 {port} 健康运行")
                print(f"   📍 访问地址: http://localhost:{port}")
                print(f"   📖 API文档: http://localhost:{port}/docs")
            else:
                print(f"⚠️ 端口 {port} 被其他服务占用")
        else:
            print(f"ℹ️ 端口 {port} 未被占用")

    async def restart_server(self, port: int) -> bool:
        """重启指定端口的服务器."""
        print(f"🔄 重启 dev_server (端口: {port})")

        if self.check_port("localhost", port):
            if await self.check_dev_server_running(port):
                print("🔧 检测到运行的 dev_server，进行清理...")
            else:
                print("⚠️ 端口被其他服务占用，尝试清理...")

        if not self.cleanup_dev_server(port):
            print("❌ 服务清理失败")
            return False

        print("✅ 服务清理完成")
        return True

    def stop_server(self, port: int) -> bool:
        """停止指定端口上的服务.

        与 cleanup_dev_server 不同, 此方法不执行 pkill,
        优先使用 PID 文件精准停止, 再用 lsof 兜底.
        """
        print(f"🛑 停止端口 {port} 上的服务...")

        # 1. 优先通过 PID 文件精准停止
        pid = self._read_pid_file(port)
        if pid and self._is_process_running(pid) and self._pid_uses_port(pid, port):
            print(f"🔍 通过 PID 文件找到进程: {pid}")
            if self._kill_pid(pid):
                self._remove_pid_file(port)
                print(f"✅ 端口 {port} 已停止")
                return True
            print(f"❌ 进程 {pid} 未能停止")
            return False

        # 清理过期 PID 文件
        if pid and not self._is_process_running(pid):
            self._remove_pid_file(port)

        # 2. 兜底: lsof 按端口
        if not self.check_port("localhost", port):
            print(f"ℹ️ 端口 {port} 未被占用")
            return True

        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            print("❌ lsof 命令超时")
            return False
        except FileNotFoundError:
            print("❌ lsof 命令不可用")
            return False

        pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        if not pids:
            self._remove_pid_file(port)
            print(f"✅ 端口 {port} 已释放")
            return True

        for pid_str in pids:
            self._kill_pid(int(pid_str))

        if not self.check_port("localhost", port):
            self._remove_pid_file(port)
            print(f"✅ 端口 {port} 已释放")
            return True

        print(f"❌ 端口 {port} 仍未释放")
        return False

    def setup_signal_handlers(self) -> None:
        """设置信号处理器."""

        def signal_handler(signum, frame) -> None:
            print(f"\n收到信号 {signum},正在关闭服务器...")
            if server:
                server.should_exit = True
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    async def start_server(self, host: str, port: int, log_level: str) -> None:
        """启动开发服务器."""
        global server

        from src.core.logger_setup import configure_logging

        log_file, log_config = configure_logging(log_level, port)

        try:
            print("开发服务器配置:")
            print(f"  主机: {host}")
            print(f"  端口: {port}")
            print(f"  日志级别: {log_level}")
            print(f"  日志文件: {log_file}")
            print("  环境配置: 请查看 config.yaml")
            print("  模型配置: 请查看 config.yaml")

            self.setup_signal_handlers()

            print("\n🚀 启动开发服务器")
            print("")
            print("🌐 服务访问信息:")
            print(f"  📍 API服务: http://{host}:{port}")
            print(f"  📖 交互文档: http://{host}:{port}/docs")
            print(f"  🔧 ReDoc文档: http://{host}:{port}/redoc")
            print(f"  ❤️  健康检查: http://{host}:{port}/health")
            print(f"  🤖 可用Agent: http://{host}:{port}/v1/models")
            print("")
            print("⚙️  系统配置:")
            print("  🔄 热重载: 关闭（建议手动重启）")
            print("  🧠 模型配置: 查看 config.yaml")
            print("  📝 详细配置: 查看 config.yaml 文件")
            print("")
            print("📚 快速使用指南:")
            print(f"  1️⃣  访问 http://{host}:{port}/docs 查看API文档")
            print("  2️⃣  在 Swagger UI 中测试API接口")
            print("  3️⃣  使用 curl 或其他工具调用API:")
            print(f"     curl -X POST http://{host}:{port}/v1/chat/completions \\")
            print('          -H "Content-Type: application/json" \\')
            print('          -H "Authorization: Bearer <your-api-key>" \\')
            print(
                '          -d \'{"model": "personal-assistant", "messages": [{"role": "user", "content": "你好"}]}\''
            )
            print("")
            print("🔍 连接测试:")
            print(f"  测试API: curl http://{host}:{port}/health")
            print(f"  查可用Agent: curl http://{host}:{port}/v1/models")
            print("")
            print("💡 使用提示:")
            print(f"  ✅ 推荐: 直接使用默认端口 {port}，无需手动切换")
            print("  🔄 自动清理: 如端口被占用，会自动清理旧进程")
            print("  🌐 外部访问: 需要时使用 --host 0.0.0.0")
            print("  📋 调试模式: 设置 DEBUG=true 环境变量")
            print(f"  🔒 安全注意: 确保端口 {port} 已开放")
            print("")
            print("🔄 重启服务器:")
            print("  • 按 Ctrl+C 停止当前服务器")
            print("  • 重新运行: python scripts/dev_server.py")
            print("  • 或使用重启参数: python scripts/dev_server.py --restart")
            print("")
            print("💡 最佳实践建议:")
            print("  🎯 始终使用默认端口8000，享受自动清理便利")
            print("  🚫 无需频繁切换端口，避免配置混乱")
            print("  🔧 遇到问题时：重启脚本比换端口更有效")
            print("  📚 查看完整帮助：python scripts/dev_server.py --help")
            print("")
            print("按 Ctrl+C 停止服务器\n")

            if host == "0.0.0.0":
                print("🌐 外部访问模式已启用:")
                print("  • 服务器将监听所有网络接口")
                print("  • 外部设备可通过网络IP访问此服务")
                print(f"  • 请确保防火墙允许端口 {port} 的访问")
                print("")

            try:
                logger.info("FastAPI应用导入成功")
            except Exception as e:
                logger.error(f"FastAPI应用导入失败: {e}")
                import traceback

                logger.error(traceback.format_exc())
                return

            config = uvicorn.Config(
                app="src.api.fastapi_app:app",
                host=host,
                port=port,
                log_level=log_level.lower(),
                log_config=log_config,
                access_log=True,
                reload=False,
            )

            server = uvicorn.Server(config)
            await server.serve()

        except KeyboardInterrupt:
            print("\n👋 服务器已停止")
        except Exception as e:
            logger.error(f"服务器启动失败: {e}")
            import traceback

            logger.error(traceback.format_exc())
            sys.exit(1)


async def main() -> None:
    """主函数 - 强制清理并启动."""
    parser = argparse.ArgumentParser(
        description="开发服务器 - 智能清理版本（推荐使用默认端口8000）",
        epilog="""
使用示例:
  %(prog)s                     # 推荐：使用默认端口8000启动（自动清理冲突）
  %(prog)s --restart          # 重启默认端口8000上的服务
  %(prog)s --status           # 检查默认端口8000的服务状态
  %(prog)s --stop             # 停止默认端口8000上的服务（不启动新实例）
  %(prog)s --port 8080        # 仅在必要时使用其他端口（会自动清理该端口）

重要提示:
  • 脚本具有智能清理功能，无需手动切换端口
  • 强烈建议始终使用默认端口8000，让系统自动处理端口冲突
  • 只有在默认端口被系统其他服务占用时才考虑更换端口
  • 优先使用 --stop 关闭服务, 避免 pkill -f 在交互终端触发自反杀
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--host",
        default=None,
        help="服务器主机地址（默认：127.0.0.1）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="服务器端口（警告：非必要时请勿修改，推荐使用默认端口8000，可通过API_PORT环境变量配置）",
    )
    parser.add_argument("--log-level", default=None, help="日志级别（默认：info）")
    parser.add_argument(
        "--restart",
        action="store_true",
        help="重启服务（使用环境变量API_PORT配置的端口）",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="检查服务状态（使用环境变量API_PORT配置的端口）",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="停止指定端口的服务（按 PID 文件优先, lsof 兜底）",
    )

    args = parser.parse_args()

    dev_server = SimpleDevServer()

    host = args.host or "127.0.0.1"
    if args.port is not None:
        port = args.port
    else:
        try:
            port = int(os.getenv("API_PORT", "8000"))
        except (ValueError, TypeError):
            port = 8000
    log_level = args.log_level or "info"

    if args.status:
        await dev_server.show_status(port)
        return

    if args.stop:
        if not dev_server.stop_server(port):
            logger.error(f"❌ 停止端口 {port} 上的服务失败")
            sys.exit(1)
        return

    if args.restart:
        if not await dev_server.restart_server(port):
            logger.error("❌ 服务重启失败")
            sys.exit(1)
        await dev_server.start_server(host, port, log_level)
        return

    # 默认启动流程
    if not dev_server.setup_pid_tracking(port):
        sys.exit(1)

    try:
        if dev_server.check_port(host, port):
            logger.info(f"🔍 检测到端口 {port} 被占用，执行自动清理...")

            if await dev_server.check_dev_server_running(port):
                logger.info("检测到dev_server进程，强制清理...")
            else:
                logger.info("端口被其他服务占用，尝试清理...")

            if not dev_server.cleanup_dev_server(port):
                logger.error("❌ 端口清理失败")
                logger.error("可能的解决方案:")
                logger.error(f"  1. 检查权限设置后重试使用默认端口 {port}")
                logger.error(
                    f"  2. 只有在端口被系统其他服务占用时才考虑: --port {port + 1}"
                )
                logger.error("  3. 推荐：让系统自动处理端口冲突，始终使用默认端口")
                sys.exit(1)

            logger.info("✅ 端口清理完成，准备启动新服务")

        await dev_server.start_server(host, port, log_level)

    except KeyboardInterrupt:
        logger.info("\n👋 服务器已停止")
    except Exception as e:
        logger.error(f"❌ 服务器运行异常: {e}")
        sys.exit(1)
    finally:
        dev_server.release_single_instance(port)


if __name__ == "__main__":
    asyncio.run(main())
