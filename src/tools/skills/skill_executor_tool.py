"""Skill执行器工具 - executable skill的代码执行入口.

调用工具运行时容器(docker/tool-runtime)执行LLM临场代码,
产物(/workspace/output/)base64回传 → app侧解码 → register_tool_output → file_id.

通过SkillLoadMiddleware动态注入(load_skill激活后), 不在初始工具列表.
base_url由环境变量TOOL_RUNTIME_BASE_URL配置(开发localhost/生产容器名).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, ClassVar, override

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.config.runtime_env import get_tool_runtime_base_url
from src.files.paths import FILES_EXPORTS
from src.tools.shared.base_external_tool import BaseExternalTool

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 120.0


class SkillExecutorInput(BaseModel):
    """Skill执行请求."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"additionalProperties": False}
    )

    code: str = Field(
        min_length=1,
        max_length=50000,
        description=(
            "要在skill执行环境中运行的Python代码. "
            "产物请写入 /workspace/output/ 目录"
            "(如 wb.save('/workspace/output/report.xlsx')). "
            "可调用/skills/下预装脚本"
            "(如 subprocess.run(['python', '/skills/xlsx/scripts/recalc.py', ...])). "
            "注意: 每次调用都在独立的容器环境中执行, 前一次调用写入/workspace/output/的文件"
            "在后续调用中不可见; 如需生成后验证/修改, 必须在同一次code中完成全部操作."
        ),
    )
    stdin: str = Field(default="", max_length=20000, description="标准输入")
    timeout_seconds: float = Field(
        default=DEFAULT_TIMEOUT,
        ge=0.5,
        le=MAX_TIMEOUT,
        description="执行超时(秒)",
    )
    title: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "本次生成产物的简短标题(如'季度销售报表'), "
            "用作对话历史标记. 建议必传, 不传则历史仅显示文件名"
        ),
    )


class SkillExecutorTool(BaseExternalTool):
    """Skill执行器工具 - 调用工具附属镜像执行代码并回收产物为附件."""

    name: str = "skill_executor"
    summary: str = "skill代码执行器, 运行临场代码(openpyxl等)并回收产物为附件"
    search_keywords: ClassVar[list[str]] = ["excel", "xlsx", "生成文件", "代码执行"]
    description: str = (
        "Skill代码执行器, 在工具附属镜像中运行Python代码.\n"
        "用于执行skill加载后的领域代码(如用openpyxl生成Excel), 产物自动回收为可下载附件.\n"
        "代码工作目录为/workspace, 产物须写入/workspace/output/才会被回收.\n"
        "\n"
        "重要: 每次调用都是独立的容器执行环境, 前一次调用产生的文件在后续调用中不可用. "
        "如需对产物做二次处理(如公式重算/格式调整/追加sheet), 必须在同一次code内完成, "
        "不能依赖跨调用的文件持久化.\n"
        "\n"
        "注意: code须是可执行Python代码; 可调用/skills/下预装脚本(绝对路径); "
        "预装openpyxl/numpy/pandas + LibreOffice(公式重算).\n"
        "\n"
        "title(建议必传)为产物简短标题, 用作对话历史标记; 不传则历史仅显示文件名."
    )
    args_schema: type[SkillExecutorInput] = SkillExecutorInput

    base_url: str = Field(
        default_factory=get_tool_runtime_base_url,
        description="工具运行时服务地址",
    )
    connect_timeout: float = Field(default=5.0, description="连接超时(秒)")
    max_stdout_chars: int = Field(default=20000)

    @override
    async def _arun(
        self,
        code: str,
        stdin: str = "",
        timeout_seconds: float | None = None,
        title: str | None = None,
    ) -> str:
        started = time.monotonic()
        try:
            timeout = self._normalize_timeout(timeout_seconds)
            result = await self._execute_remote(code, stdin, timeout)
            duration_ms = int((time.monotonic() - started) * 1000)
            return await self._format_result(result, duration_ms, code, title)
        except Exception as e:
            logger.exception("SkillExecutorTool执行失败: %s", e)
            return self._format_error(e)

    async def _execute_remote(
        self,
        code: str,
        stdin: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        request_timeout = timeout_seconds + self.connect_timeout + 2.0
        timeout = httpx.Timeout(
            request_timeout,
            connect=self.connect_timeout,
            read=request_timeout,
            write=self.connect_timeout,
            pool=self.connect_timeout,
        )
        payload = {
            "code": code,
            "stdin": stdin,
            "timeout_seconds": timeout_seconds,
            "collect_outputs": True,
        }
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
        ) as client:
            response = await client.post("/execute", json=payload)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ValueError("skill执行服务返回无效响应")
        return data

    async def _format_result(
        self,
        result: dict[str, Any],
        duration_ms: int,
        source_code: str,
        title: str | None,
    ) -> str:
        stdout = str(result.get("stdout") or "")[: self.max_stdout_chars]
        stderr = str(result.get("stderr") or "")[: self.max_stdout_chars]
        timed_out = bool(result.get("timed_out"))
        exit_code = result.get("exit_code")
        success = bool(result.get("success", exit_code == 0 and not timed_out))

        # 产物回收 → file_id(仅成功时)
        registered: list[dict[str, Any]] = []
        created_files = result.get("created_files") or []
        if success and created_files:
            registered = await self._register_outputs(
                created_files, source_code, title
            )

        payload = {
            "success": success,
            "message": self._build_message(success, timed_out, exit_code, registered),
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": result.get("duration_ms", duration_ms),
            "created_files": registered,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    async def _register_outputs(
        self,
        created_files: list[dict[str, Any]],
        source_code: str,
        title: str | None,
    ) -> list[dict[str, Any]]:
        """回收产物: base64解码 → 落盘 → register_tool_output → file_id."""
        from src.core.context import get_user_context_or_none
        from src.core.path_resolver import get_user_path_resolver
        from src.files.desc_writer import write_desc
        from src.tools.shared.file_output import register_tool_output

        ctx = get_user_context_or_none()
        if ctx is None:
            logger.warning("无UserContext, 跳过skill产物回收")
            return []

        resolver = get_user_path_resolver()
        export_dir = resolver.get_shared_storage_path(
            ctx.user_id, ctx.thread_id, FILES_EXPORTS
        )

        results: list[dict[str, Any]] = []
        for f in created_files:
            try:
                filename = f.get("filename") or f.get("relative_path") or "output"
                content_b64 = f.get("content_b64") or ""
                if not content_b64:
                    continue
                raw = base64.b64decode(content_b64)
                output_format = Path(filename).suffix.lstrip(".") or "bin"
                # 磁盘文件名加时间戳+随机后缀避免冲突
                ts = time.strftime("%Y%m%d_%H%M%S")
                short = os.urandom(4).hex()
                stem = Path(filename).stem
                disk_filename = f"{stem}_{ts}_{short}.{output_format}"
                output_path = export_dir / disk_filename
                output_path.write_bytes(raw)

                reg = await register_tool_output(
                    output_path=output_path,
                    display_filename=filename,
                    output_filename=disk_filename,
                    output_format=output_format,
                    file_type="document",
                    content=f"skill执行产物: {filename}",
                    summary=None,
                    user_id=ctx.user_id,
                    thread_id=ctx.thread_id,
                    brief=title,
                )
                # 源码即描述: code 写入 .desc.md
                file_id = reg.get("file_id")
                if file_id:
                    write_desc(ctx.user_id, file_id, source_code)
                results.append(reg)
            except Exception as e:
                logger.warning("skill产物回收失败 %s: %s", f.get("filename"), e)
                results.append({
                    "success": False,
                    "filename": f.get("filename"),
                    "error": str(e),
                })
        return results

    def _normalize_timeout(self, timeout_seconds: float | None) -> float:
        timeout = DEFAULT_TIMEOUT if timeout_seconds is None else float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("timeout_seconds必须大于0")
        return min(timeout, MAX_TIMEOUT)

    @staticmethod
    def _build_message(
        success: bool,
        timed_out: bool,
        exit_code: Any,
        registered: list[dict[str, Any]],
    ) -> str:
        if timed_out:
            return "skill代码执行超时"
        if not success:
            return f"skill代码执行失败, exit_code={exit_code}"
        file_ids = [r.get("file_id") for r in registered if r.get("file_id")]
        if file_ids:
            return f"skill代码执行成功, 生成附件: {', '.join(file_ids)}"
        return "skill代码执行成功(无产物)"


__all__ = ["SkillExecutorInput", "SkillExecutorTool"]
