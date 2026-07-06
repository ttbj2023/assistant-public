"""简化版 FastAPI 应用模块 - 提供标准 OpenAI API.

基于新架构的简洁实现,直接提供OpenAI兼容接口.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.agent.manager import get_agent_manager
from src.api.error_handling import ErrorHandlingMiddleware
from src.api.middleware.openclaw_filter import OpenClawFilterMiddleware
from src.api.routes.chat import router as chat_router
from src.api.routes.files import router as files_router
from src.api.routes.health import router as health_router
from src.api.routes.usage import router as usage_router
from src.auth import get_auth_manager
from src.config import runtime_env
from src.config.api_config import get_config
from src.inference.llm.model_loader import get_llm_factory
from src.utils.debug_config import is_debug_enabled

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

# 全局设置对象 - 使用类型安全的配置体系
settings = get_config()

logger = logging.getLogger(__name__)

_startup_bg_tasks: set[asyncio.Task] = set()


async def _background_init_scheduled_services() -> None:
    """后台预加载所有用户的定时消息服务, 注册定时器."""
    try:
        from src.storage.service.scheduled_message_service import (
            initialize_all_scheduled_services,
        )

        stats = await initialize_all_scheduled_services()
        logger.info(
            "⏰ 定时消息启动预加载: %d个用户, %d条定时器",
            stats.get("users", 0),
            stats.get("timers", 0),
        )
    except Exception as e:
        logger.warning("⏰ 定时消息启动预加载失败(非致命): %s", e)


# 语义缓存周期清理任务引用(启动时赋值, 关闭时cancel)
_semantic_cache_cleanup_task: asyncio.Task | None = None


async def _periodic_semantic_cache_cleanup() -> None:
    """周期清理语义缓存过期条目, 防止 ChromaDB 落盘数据无限累积.

    SemanticCache 的 TTL 过期条目仅在查询时被过滤而非物理删除,
    需周期触发 cleanup() 删除磁盘上的过期数据.
    """
    from src.tools.shared.semantic_cache import (
        _DEFAULT_CLEANUP_INTERVAL,
        get_semantic_cache,
    )

    while True:
        await asyncio.sleep(_DEFAULT_CLEANUP_INTERVAL)
        try:
            removed = await get_semantic_cache().cleanup()
            if removed > 0:
                logger.info("🧹 语义缓存清理: 删除%d条过期条目", removed)
        except Exception as e:
            logger.warning("🧹 语义缓存周期清理异常(非致命): %s", e)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期管理 - 使用新的工厂模式架构"""
    global _semantic_cache_cleanup_task
    # 启动时的初始化逻辑
    try:
        logger.info("🚀 开始初始化Agent系统...")

        # 初始化Agent管理器
        get_agent_manager()
        logger.info("✅ Agent管理器初始化完成")

        # 预热HTTP客户端
        logger.info("🔥 开始预热HTTP客户端...")
        try:
            # Embedding 模型来自 inference 配置, 各 Agent 主对话模型来自 agent.yaml
            from src.agent.factory import AgentFactory
            from src.config.inference_config import (
                get_config as get_inference_config,
            )
            from src.inference.embeddings.factory import (
                get_embeddings_factory,
            )

            embedding_model_id = get_inference_config().embeddings.model

            # 收集所有 Agent 的主对话模型 ID 去重后预热
            agent_factory = AgentFactory()
            llm_model_ids: set[str] = set()
            for agent_id in agent_factory.get_supported_agents():
                try:
                    agent_cfg = await agent_factory.load_agent_config(agent_id)
                    if agent_cfg.model_id:
                        llm_model_ids.add(agent_cfg.model_id)
                except Exception as e:
                    logger.warning(
                        "⚠️ 加载 Agent[%s] 配置失败, 跳过该 Agent 预热: %s",
                        agent_id,
                        e,
                    )

            factory = get_llm_factory()
            for mid in llm_model_ids:
                try:
                    factory.get_llm(mid)
                    logger.info("✅ LLM 客户端预热完成: %s", mid)
                except Exception as e:
                    logger.warning("⚠️ LLM 客户端预热失败 %s: %s", mid, e)

            if embedding_model_id:
                try:
                    get_embeddings_factory().get_embeddings(embedding_model_id)
                    logger.info(
                        "✅ Embeddings 客户端预热完成: %s",
                        embedding_model_id,
                    )
                except Exception as e:
                    logger.warning(
                        "⚠️ Embeddings 客户端预热失败 %s: %s",
                        embedding_model_id,
                        e,
                    )

            # 输出客户端统计信息
            stats = factory.stats()
            logger.info("📊 客户端统计: %s", stats)

        except Exception as e:
            logger.warning("⚠️ HTTP客户端预热失败: %s", e)

        logger.info("🎉 Agent系统初始化完成")

        # 后台预加载所有用户的定时消息服务(注册asyncio定时器)
        task = asyncio.create_task(_background_init_scheduled_services())
        _startup_bg_tasks.add(task)
        task.add_done_callback(_startup_bg_tasks.discard)

        # 启动语义缓存周期清理(防止ChromaDB落盘数据无限累积)
        _semantic_cache_cleanup_task = asyncio.create_task(
            _periodic_semantic_cache_cleanup(),
        )
        logger.info("🧹 语义缓存周期清理已启动")

        # 启动价格监控轮询引擎 (一次性语义, 遍历所有用户规则)
        from src.storage.service.price_alert_service import (
            get_price_alert_engine,
        )

        await get_price_alert_engine().start()
        logger.info("📊 价格监控引擎已启动")

        # 健康检查系统已简化,无需初始化注册
        logger.info("🏥 健康检查系统使用简化设计,无需预注册")

    except Exception as e:
        logger.error("❌ Agent系统初始化失败: %s", e)
        # 不阻止应用启动,只记录错误

    yield  # noqa: RUF075

    # 关闭时的清理逻辑
    try:
        logger.info("🔄 开始应用关闭流程...")

        # 1. 停止语义缓存周期清理任务 (需await cancel, 手动处理)
        if _semantic_cache_cleanup_task is not None:
            _semantic_cache_cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await _semantic_cache_cleanup_task
            _semantic_cache_cleanup_task = None

        # 2. 注册按维度资源 (全局单例已在各自get/create中自注册)
        from src.core.lifecycle import get_lifecycle_registry, register_resource
        from src.storage.dao.async_database_manager import close_all_db_managers
        from src.storage.service.price_alert_service import (
            shutdown_price_alert_engine,
        )
        from src.storage.service.scheduled_message_service import (
            shutdown_all_scheduled_services,
        )
        from src.storage.service.service_factory import clear_vector_cache

        register_resource("scheduled_messages", shutdown_all_scheduled_services)
        register_resource("price_alert", shutdown_price_alert_engine)
        register_resource("vector_cache", clear_vector_cache)

        # 3. 按注册逆序关闭全部已注册资源 (异常隔离)
        await get_lifecycle_registry().close_all()

        # 4. DB 连接最后关闭 (不纳入注册表, 单独保证最后执行)
        await close_all_db_managers()

        logger.info("✅ 应用关闭流程完成")
    except Exception as e:
        logger.error("❌ 应用关闭流程失败: %s", e)


# 获取API文档配置 - 使用配置对象方式
docs_url: str | None
redoc_url: str | None
openapi_url: str | None
if settings.docs.enable_swagger_ui:
    docs_url = settings.docs.docs_url
    redoc_url = settings.docs.redoc_url if settings.docs.enable_redoc else None
    openapi_url = settings.docs.openapi_url
else:
    docs_url = None
    redoc_url = None
    openapi_url = None

# 创建 FastAPI 应用实例
app = FastAPI(
    title="Personal Agent Assistant API",
    description="基于双层记忆 + LangChain v1.0的个人助手 - OpenAI兼容API",
    version="1.0.0",
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url,
    lifespan=lifespan,  # 使用现代的lifespan处理器
)


# 配置OpenAPI Security Schemes - 支持Swagger UI API密钥输入
def custom_openapi() -> dict[str, Any]:
    """自定义OpenAPI schema,添加security schemes.

    使用函数而不是直接赋值app.openapi_schema,避免覆盖FastAPI自动生成的paths.
    """
    if app.openapi_schema:
        return app.openapi_schema

    # 让FastAPI先生成完整的schema(包含paths,schemas等)
    # 注意:必须直接从FastAPI类获取原始方法,避免递归
    from fastapi import FastAPI

    openapi_schema = FastAPI.openapi(app)

    # 添加自定义的security schemes
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "description": "请输入您的API密钥 (格式: Bearer sk-project-...)",
        },
    }
    openapi_schema["security"] = [{"BearerAuth": []}]

    # 缓存结果
    app.openapi_schema = openapi_schema
    return openapi_schema


# 设置自定义openapi函数
app.openapi = custom_openapi

# 配置CORS - 使用配置对象
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_methods,
    allow_headers=settings.cors_headers,
)

# 添加统一错误处理中间件
app.add_middleware(ErrorHandlingMiddleware)

# 添加 OpenClaw 注入过滤中间件 (在 ErrorHandling 之后, Auth 之前)
app.add_middleware(OpenClawFilterMiddleware)

# 初始化统一认证管理器
auth_manager = get_auth_manager()


@app.middleware("http")
async def unified_auth_middleware(
    http_request: Request,
    call_next: Callable[[Request], Response],
) -> Response:
    """统一认证中间件 - 使用新的认证模块"""
    logger.info(f"[MIDDLEWARE] 收到请求: {http_request.method} {http_request.url.path}")

    if http_request.method == "OPTIONS":
        logger.info(f"[MIDDLEWARE] CORS预检请求,跳过认证: {http_request.url.path}")
        return await call_next(http_request)  # pyright: ignore[reportGeneralTypeIssues]

    # 公开端点白名单(无需认证)
    # 包括:健康检查,API文档,模型列表,文件下载(Token即凭证)
    public_paths = {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/v1/models",  # 模型列表端点,遵循OpenAI API规范
    }

    request_path = http_request.url.path
    is_file_download = (
        request_path.startswith(
            "/v1/files/dl/",
        )
        or "/v1/files/dl/" in request_path
    )

    if request_path in public_paths or is_file_download:
        logger.info(f"[MIDDLEWARE] 公开端点,跳过认证: {request_path}")
        return await call_next(http_request)  # pyright: ignore[reportGeneralTypeIssues]

    try:
        # 测试环境特殊处理:当禁用静态用户管理时,从API key解析用户信息
        # per-request 实时读取, 不走缓存的 auth_config (需响应运行时 env 切换)
        static_user_override = runtime_env.get_static_user_management_override()
        if static_user_override is False:
            logger.info("✅ 进入动态API Key解析逻辑")
            api_key = auth_manager._extract_api_key_from_request(http_request)
            logger.info(f"✅ 提取到API Key: {api_key[:50] if api_key else None}...")

            if api_key and api_key.startswith("sk-project-"):
                # 解析API key格式: sk-project-{user_id}-{thread_id}-{random_suffix}
                # 注意:thread_id使用横线而不是下划线,避免与random_suffix分隔符冲突
                # 正确的分割方式:移除前缀"sk-project-",然后找到最后一个横线(分隔符)
                key_body = api_key[len("sk-project-") :]  # 移除前缀

                # 找到分隔user_id和thread_id的第一个横线
                # 格式: {user_id}-{thread_id}-{random_suffix}
                first_dash = key_body.find("-")

                if first_dash > 0:
                    user_id = key_body[:first_dash]

                    # thread_id是剩余部分,去掉最后的random_suffix(8个字符)
                    rest = key_body[first_dash + 1 :]  # {thread_id}-{random}
                    last_dash = rest.rfind("-")

                    if last_dash > 0:
                        thread_id = rest[:last_dash]
                        # random_suffix = rest[last_dash + 1:]

                        # 将用户信息添加到请求状态
                        http_request.state.user_id = user_id
                        http_request.state.thread_id = thread_id
                        http_request.state.api_key = api_key
                        http_request.state.timezone = "Asia/Shanghai"

                        logger.info(
                            f"✅ 测试环境认证成功: user={user_id}, thread={thread_id}, api_key={api_key[:50]}...",
                        )

                        return await call_next(http_request)  # pyright: ignore[reportGeneralTypeIssues]
                    logger.warning(
                        "⚠️ API key格式错误(缺少随机后缀分隔符): %s",
                        api_key,
                    )
                else:
                    logger.warning(
                        "⚠️ API key格式错误(缺少user_id分隔符): %s",
                        api_key,
                    )

        # 使用统一认证系统
        user_id, thread_id = auth_manager.authenticate_request(http_request)

        # 将用户信息添加到请求状态
        http_request.state.user_id = user_id
        http_request.state.thread_id = thread_id
        http_request.state.api_key = auth_manager._extract_api_key_from_request(
            http_request,
        )

        # 注入用户时区到请求状态
        http_request.state.timezone = auth_manager.get_user_timezone(user_id)

        logger.debug("✅ 统一认证成功: user=%s, thread=%s", user_id, thread_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("❌ 统一认证失败: %s", e)

        # 检查是否是认证相关错误,直接返回JSONResponse而不是抛出HTTPException
        error_message = str(e).lower()

        if "api密钥缺失" in error_message or "missing_api_key" in error_message:
            response_data = {
                "error": "API_KEY_MISSING",
                "message": "缺少API密钥,请在请求头中添加有效的Authorization",
                "hint": "使用格式: Authorization: Bearer YOUR_API_KEY",
                "example": 'curl -X POST http://localhost:8000/v1/chat/completions -H "Authorization: Bearer sk-project-user-thread-key" -H "Content-Type: application/json" -d \'{"model": "personal_assistant", "messages": [{"role": "user", "content": "hello"}]}\'',
                "status_code": 401,
                "timestamp": int(time.time()),
                "error_id": f"auth_401_{int(time.time())}",
            }
            logger.warning("认证错误 - API密钥缺失: %s", response_data)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content=response_data,
            )
        if "api密钥无效" in error_message or "invalid_api_key" in error_message:
            response_data = {
                "error": "API_KEY_INVALID",
                "message": "API密钥无效或已过期",
                "hint": "请检查API密钥是否正确,或联系管理员获取新的API密钥",
                "help": "使用静态用户管理工具查看可用密钥: python scripts/api_key_manager.py list",
                "status_code": 401,
                "timestamp": int(time.time()),
                "error_id": f"auth_401_{int(time.time())}",
            }
            logger.warning("认证错误 - API密钥无效: %s", response_data)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content=response_data,
            )
        if "用户已被禁用" in error_message or "user_inactive" in error_message:
            response_data = {
                "error": "USER_INACTIVE",
                "message": "用户账户已被禁用",
                "hint": "请联系管理员激活您的账户",
                "status_code": 403,
                "timestamp": int(time.time()),
                "error_id": f"auth_403_{int(time.time())}",
            }
            logger.warning("认证错误 - 用户被禁用: %s", response_data)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content=response_data,
            )
        # 其他未知的认证错误
        response_data = {
            "error": "AUTHENTICATION_ERROR",
            "message": "认证服务出现错误",
            "debug_info": str(e) if is_debug_enabled() else None,
            "status_code": 500,
            "timestamp": int(time.time()),
            "error_id": f"auth_500_{int(time.time())}",
        }
        logger.error("认证错误 - 未知错误: %s", response_data)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=response_data,
        )

    return await call_next(http_request)  # pyright: ignore[reportGeneralTypeIssues]


# 添加路由
app.include_router(chat_router)
app.include_router(files_router)
app.include_router(health_router)
app.include_router(usage_router)


# 简化的全局异常处理器 - 主要用于特殊格式的异常
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    http_request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """处理请求验证错误"""
    from src.api.error_handling import create_error_response

    return create_error_response(
        ValueError(f"请求验证失败: {exc.errors()}"),
        trace_id=str(http_request.headers.get("X-Request-ID", "")),
    )
