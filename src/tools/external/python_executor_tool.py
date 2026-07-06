"""Python精确计算与数据分析沙箱工具 - 无状态全局共享, 通过TCP调用Docker沙箱."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, ClassVar, override

import httpx
from pydantic import ConfigDict, Field

from src.config.runtime_env import get_tool_runtime_base_url
from src.tools.shared.base_external_tool import BaseExternalTool
from src.tools.shared.query_alias_model import QueryAliasModel

logger = logging.getLogger(__name__)


class PythonExecutorInput(QueryAliasModel):
    """Python执行请求."""

    _field_aliases: ClassVar[dict[str, str]] = {"query": "code"}

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    code: str = Field(
        min_length=1,
        max_length=20000,
        description=(
            "要在隔离沙箱中执行的 Python 代码. "
            "必须是语法正确的,可直接运行的 Python 代码, 不能是自然语言描述,需求说明或伪代码. "
            "如果需要把结果返回给用户, 代码中必须使用 print() 将结果输出到标准输出."
        ),
    )
    stdin: str = Field(
        default="",
        max_length=20000,
        description="传递给程序标准输入的文本, 可选",
    )
    timeout_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        description="执行超时时间, 单位秒",
    )


class PythonExecutorTool(BaseExternalTool):
    """Python精确计算与数据分析沙箱工具 - 将代码发送到Docker沙箱执行并返回输出."""

    name: str = "python_executor"
    summary: str = (
        "精确计算与数据分析工具, 支持数值计算/统计/数据处理(预装numpy/pandas)"
    )
    search_keywords: ClassVar[list[str]] = [
        "计算",
        "统计",
        "数据分析",
        "数据处理",
        "代码",
        "运行代码",
        "Python",
        "编程",
        "脚本",
        "执行代码",
    ]
    description: str = (
        "精确计算与数据分析工具.\n"
        "当需要保证数值计算正确性(避免心算出错),处理或统计数据,或执行确定性批量运算时使用.\n"
        "预装 numpy/pandas, 输出为程序标准输出的文本.\n"
        "\n"
        "注意: code 参数必须是语法正确的可执行 Python 代码, 不能是自然语言描述,需求说明或伪代码; "
        "如需将结果返回给用户, 代码中必须使用 print() 将结果输出到标准输出.\n"
        "\n"
        "适用场景: 复杂多步运算,浮点/大数计算,数据清洗与聚合等需要确定性的任务.\n"
        "限制: 无法访问网络, 无法读写你的文件, 仅输出文本不生成图片. 简单运算可直接心算, 无需调用本工具."
    )
    args_schema: type[PythonExecutorInput] = PythonExecutorInput

    base_url: str = Field(
        default_factory=get_tool_runtime_base_url,
        description="工具运行时服务地址(与 skill_executor 共用 tool-runtime)",
    )
    connect_timeout: float = Field(default=3.0, description="连接超时(秒)")
    default_timeout_seconds: float = Field(default=5.0, description="默认执行超时")
    max_timeout_seconds: float = Field(default=30.0, description="最大执行超时")
    max_code_chars: int = Field(default=20000, description="代码最大字符数")
    max_stdin_chars: int = Field(default=20000, description="stdin最大字符数")
    max_stdout_chars: int = Field(default=20000, description="stdout最大字符数")
    max_stderr_chars: int = Field(default=12000, description="stderr最大字符数")

    @override
    async def _arun(
        self,
        code: str,
        stdin: str = "",
        timeout_seconds: float | None = None,
    ) -> str:
        started = time.monotonic()
        try:
            self._validate_request(code, stdin)
            timeout = self._normalize_timeout(timeout_seconds)
            result = await self._execute_remote(code, stdin, timeout)
            duration_ms = int((time.monotonic() - started) * 1000)
            return self._format_execution_result(result, duration_ms)
        except Exception as e:
            logger.exception("PythonExecutorTool执行失败: %s", e)
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
            "max_stdout_chars": self.max_stdout_chars,
            "max_stderr_chars": self.max_stderr_chars,
            "collect_outputs": False,
        }

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
        ) as client:
            response = await client.post("/execute", json=payload)
            response.raise_for_status()
            data = response.json()

        if not isinstance(data, dict):
            raise ValueError("沙箱返回了无效响应")
        return data

    def _validate_request(self, code: str, stdin: str) -> None:
        if len(code) > self.max_code_chars:
            raise ValueError(f"代码过长, 最大{self.max_code_chars}字符")
        if len(stdin) > self.max_stdin_chars:
            raise ValueError(f"stdin过长, 最大{self.max_stdin_chars}字符")

    def _normalize_timeout(self, timeout_seconds: float | None) -> float:
        timeout = (
            self.default_timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        if timeout <= 0:
            raise ValueError("timeout_seconds必须大于0")
        return min(timeout, self.max_timeout_seconds)

    def _format_execution_result(self, result: dict[str, Any], duration_ms: int) -> str:
        stdout, stdout_truncated = self._truncate(
            str(result.get("stdout") or ""),
            self.max_stdout_chars,
        )
        stderr, stderr_truncated = self._truncate(
            str(result.get("stderr") or ""),
            self.max_stderr_chars,
        )

        timed_out = bool(result.get("timed_out"))
        exit_code = result.get("exit_code")
        success = bool(result.get("success", exit_code == 0 and not timed_out))

        payload = {
            "success": success,
            "message": self._build_message(success, timed_out, exit_code),
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": result.get("duration_ms", duration_ms),
            "stdout_truncated": stdout_truncated
            or bool(result.get("stdout_truncated")),
            "stderr_truncated": stderr_truncated
            or bool(result.get("stderr_truncated")),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _truncate(value: str, max_chars: int) -> tuple[str, bool]:
        if len(value) <= max_chars:
            return value, False
        return value[:max_chars], True

    @staticmethod
    def _build_message(success: bool, timed_out: bool, exit_code: Any) -> str:
        if timed_out:
            return "Python代码执行超时"
        if success:
            return "Python代码执行成功"
        return f"Python代码执行失败, exit_code={exit_code}"


__all__ = ["PythonExecutorInput", "PythonExecutorTool"]
