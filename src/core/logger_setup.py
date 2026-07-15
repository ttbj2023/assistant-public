"""日志基础设施.

提供统一的日志配置初始化函数, 支持终端 + 文件双写, 并与 uvicorn 日志集成.
"""

from __future__ import annotations

import logging.config
import os
from pathlib import Path
from typing import Any


def ensure_log_dir(port: int = 0) -> Path:
    """确保 logs 目录存在, 返回日志文件路径.

    Args:
        port: 服务端口, 0 表示非服务入口 (按 PID 隔离生成 logs/test_{pid}.log)

    Returns:
        日志文件路径
    """
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True, parents=True)
    if port:
        return log_dir / f"server_{port}.log"
    # port=0: 非服务入口 (pytest/脚本), 按 PID 隔离避免多进程共写串扰 dev_server 日志
    return log_dir / f"test_{os.getpid()}.log"


def build_log_config(
    log_level: str,
    log_file: Path,
    max_bytes: int,
    backup_count: int,
) -> dict[str, Any]:
    """构建 uvicorn dictConfig 配置.

    同时配置终端输出和轮转文件输出, 保证 app logger 与 uvicorn.* logger
    的日志写入同一文件. 此外单独输出 error 日志 (WARNING+), 便于快速定位问题.

    Args:
        log_level: 日志级别 (小写, 如 "info")
        log_file: 主日志文件路径; error 日志文件名由其派生 (插入 .error)
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的轮转备份文件数量

    Returns:
        logging dictConfig 字典
    """
    level = log_level.upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "uvicorn_default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(name)s %(message)s",
                "use_colors": None,
            },
            "uvicorn_access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
            "standard": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            },
        },
        "handlers": {
            "console_default": {
                "formatter": "uvicorn_default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "console_access": {
                "formatter": "uvicorn_access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "console_app": {
                "formatter": "standard",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "file_default": {
                "formatter": "standard",
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(log_file),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
            },
            "file_error": {
                "formatter": "standard",
                "class": "logging.handlers.RotatingFileHandler",
                # error 日志文件名派生自主日志: server_8000.log -> server_8000.error.log
                "filename": str(
                    log_file.with_name(f"{log_file.stem}.error{log_file.suffix}")
                ),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
                "level": "WARNING",
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["console_default", "file_default"],
                "level": level,
                "propagate": False,
            },
            "uvicorn.error": {
                "level": level,
                "propagate": True,
            },
            "uvicorn.access": {
                "handlers": ["console_access", "file_default"],
                "level": "INFO",
                "propagate": False,
            },
        },
        "root": {
            "handlers": ["console_app", "file_default", "file_error"],
            "level": level,
        },
    }


def configure_logging(log_level: str, port: int = 0) -> tuple[Path, dict[str, Any]]:
    """配置日志系统.

    读取 config.yaml 的 logging 块, 同时输出到终端和按端口命名的轮转日志文件.

    Args:
        log_level: CLI 传入的日志级别 (小写), 优先级高于 config.yaml
        port: 服务端口, 0 表示非服务入口 (按 PID 隔离, 生成 logs/test_{pid}.log)

    Returns:
        (日志文件路径, dictConfig 字典)
    """
    from src.config.logging_config import get_logging_config

    cfg = get_logging_config()
    # CLI 参数优先级高于配置文件
    effective_level = log_level if log_level else cfg.level
    log_file = ensure_log_dir(port)

    config = build_log_config(
        log_level=effective_level,
        log_file=log_file,
        max_bytes=cfg.file_max_bytes,
        backup_count=cfg.backup_count,
    )
    logging.config.dictConfig(config)
    return log_file, config
