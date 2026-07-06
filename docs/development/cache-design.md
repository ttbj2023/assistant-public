# 缓存设计文档

> **版本**: v1.0 | **最后更新**: 2026-07-02 | **适用项目版本**: v1.9.0+

## 概述

项目采用 **三层缓存 + 配置缓存 + 模块级状态** 的分层架构. 每一层有明确的职责边界, 遵循"只缓存有重量的东西"的核心原则.

```
┌─────────────────────────────────────────────────────────┐
│  Layer 0: 业务缓存 (有界 LRU/TTL)                        │
│  记忆组装 / 专家工具 / 语义搜索 / LLM 客户端              │
│  → 缓存计算结果, 避免重复 DB 查询 / API 调用              │
├─────────────────────────────────────────────────────────┤
│  Layer 1: DB Engine 池 (_db_manager_cache)               │
│  → SQLAlchemy engine + 连接池 + session_factory          │
│  → 按 database_url 复用, 全局唯一不可替代                 │
├─────────────────────────────────────────────────────────┤
│  Layer 2: 重量级资源单例 (LifecycleRegistry 自注册)       │
│  VectorService / HttpPool / McpBridge / Browser ...      │
│  → 持有 OS 资源的实例 (线程池/文件句柄/子进程/定时器)      │
├─────────────────────────────────────────────────────────┤
│  配置缓存 (子模块 _cached)                                │
│  各配置模块缓存 Pydantic 实例                             │
└─────────────────────────────────────────────────────────┘
```

## 核心设计原则

### 1. 只缓存"有重量"的东西

> 如果一个实例是无状态的方法容器 (底层资源已被 Layer 1 覆盖), 则**不缓存**. 只有实例持有不可轻易重建的 OS 级资源时, 才纳入缓存.

**不缓存的** (无状态薄壳, 底层 engine 已由 Layer 1 覆盖):
- ConversationService / TodoService / MemoryService / UserRequirementService
- NutritionService / UsageService / ConversationDataService
- HealthDataService / HealthDataExtractionService
- StorageQuotaService / FileDeduplicationService
- Agent 实例 (无状态处理架构, 每次请求创建)

**缓存的** (持有 OS 资源):
- VectorService (ChromaDB PersistentClient + ThreadPoolExecutor)
- HttpPool (httpx 连接池)
- McpBridge (MCP stdio 子进程)
- BrowserRenderer (HTTP 客户端, 调用 tool-runtime 容器)
- OpenClawClient (httpx 连接)
- ScheduledMessageService (asyncio TimerHandle)

### 2. 自注册优于集中维护

重量级资源在 `get/create` 函数中自动注册到 LifecycleRegistry, shutdown 时由 `close_all()` 统一关闭. 新增资源只需实现 `close()` + 一行 `register_resource()`, 不需要改 lifespan.

### 3. 异常隔离

`LifecycleRegistry.close_all()` 保证单个资源 close 失败不阻断后续资源关闭. 所有 close 调用都包裹在 try/except 中.

---

## Layer 1: DB Engine 池

**文件**: `src/storage/dao/async_database_manager.py`

| 维度 | 说明 |
|------|------|
| **Key** | `database_url` (按 `user_id/thread_id/agent_id` 组合) |
| **Value** | `AsyncDatabaseManager` (持有 engine + connection_pool + session_factory) |
| **关闭** | `close_all_db_managers()`, lifespan shutdown **最后**执行 |

### 为什么必须缓存

engine 创建 = 初始化 aiosqlite 连接池 + 表结构检查, 成本高. 且 aiosqlite 连接绑定事件循环, 跨循环复用会导致 ResourceWarning.

### 与 Service 工厂的关系

`service_factory.create_xxx_service()` 每次调用都通过 `_get_or_create_db_manager()` 命中 Layer 1 缓存拿到 `session_factory`, 然后创建轻量 DAO 壳. **DAO 壳不缓存** (无状态), **engine 缓存** (重量级).

### 唯一的例外: VectorService

VectorService 的 `LangChainVectorStore` 持有 ChromaDB PersistentClient (文件句柄) + ThreadPoolExecutor (OS 线程), 这些资源不在 `_db_manager_cache` 中, 因此 VectorService 需要独立缓存 (Layer 2).

---

## Layer 2: 重量级资源单例 (LifecycleRegistry)

**管理机制**: `src/core/lifecycle.py` → `LifecycleRegistry`

具体注册资源、持有 OS 资源、关闭方式见源码:
- 全局单例: 各 `get_*()` / `create_*()` 函数中的 `register_resource()` 调用
- 按维度资源: `src/api/fastapi_app.py` lifespan shutdown 中的集中注册

### 注册模式

- **全局单例** (HttpPool / OpenClaw / Browser / ToolsManager 等): 在各自的 `get/create` 函数中自注册.
- **按维度资源** (ScheduledMessageService / SplittableMemoryCache 等): 在 lifespan shutdown 中集中注册, 因为 close 方式是全局函数.

### 关闭行为

`close_all()` 按注册**逆序**关闭, 单个资源异常不阻断后续关闭.

---

## Layer 0: 业务缓存 (有界 LRU/TTL)

### SplittableMemoryCache (记忆组装核心)

**文件**: `src/agent/memory/local_memory/cache.py`

缓存格式化后的置顶记忆、裁剪后的对话历史窗口、TODO 列表. 三维 key 为 `user_id:thread_id:agent_id`. 具体 maxsize、失效机制见源码.

### ExpertCache (外部 API 缓存)

**文件**: `src/tools/shared/cache.py`

按工具/场景分桶缓存搜索结果、网页抓取、地理信息, 避免重复外部调用. TTL 与容量见源码.

### SemanticCache (语义缓存)

**文件**: `src/tools/shared/semantic_cache.py`

基于 ChromaDB 的 embedding 语义缓存, 仅用于 `WebResearchTool` deep 模式. 相似度阈值、TTL、清理周期见源码.

### SimpleMemoryCache

**文件**: `src/core/cache/simple_cache.py`

- **client 池**: 缓存 LangChain LLM / Embedding 客户端实例, 避免重复构造.
- **token 池**: 缓存 token 估算结果, 避免重复计算.

maxsize 与 key 生成策略见源码.

---

## 配置缓存

**文件**: `src/config/{module}_config.py`

每个子模块的 `get_config()` 使用模块级 `_cached` 变量缓存 Pydantic 实例. 具体子模块与重置逻辑见 `src/config/config_loader.py` 的 `reset_config_cache()`.

### 历史背景

此前有 `config_facade.py` 试图在调用端统一缓存, 但大量调用方绕过 (直连子模块). v1.8.3 移除了 facade, 改为子模块自带缓存, 消除了缓存绕过问题.

### 测试隔离

`reset_config_cache()` 重置全部子模块的 `_cached`. 单元测试与集成测试的 autouse fixture 自动调用, 具体见 `tests/unit/config/conftest.py` 与 `tests/integration/conftest.py`.

---

## 记忆系统模块级状态

这些不是传统意义的"缓存", 而是记忆系统的运行时状态, 按 `user_id:thread_id:agent_id` 三维 key 累积, 进程级管理. 涉及 `index_run_service.py` / `pinned_memory_service.py` / `health_data_audit.py` 等, 具体变量与清理函数见源码.

**注意**: 个人助手场景用户数有限 (个位数), 三维 key 组合数极少, 不存在实际泄漏风险.

---

## Shutdown 流程

**文件**: `src/api/fastapi_app.py` → `lifespan()`

```
lifespan shutdown
  │
  ├─ 1. cancel SemanticCache 周期清理 task (需 await cancel)
  │
  ├─ 2. 注册按维度资源到 LifecycleRegistry
  │     register_resource("scheduled_messages", shutdown_all_scheduled_services)
  │     register_resource("vector_cache", clear_vector_cache)
  │
  ├─ 3. await lifecycle.close_all()
  │     → 按注册逆序关闭全部已注册资源 (异常隔离)
  │     → 全局单例已在启动期自注册
  │
  └─ 4. await close_all_db_managers()  ← DB 最后关
```

**DB 不纳入 LifecycleRegistry**, 单独保证最后关闭. 原因: DB engine 是所有 Service 的底层依赖, 必须在全部上层消费者关闭后才安全释放.

---

## 缓存设计决策记录

### 已移除的设计 (不可回退)

| 组件 | 移除原因 |
|------|---------|
| `config_facade.py` | 缓存层被大量调用方绕过, 设计方向错误. 改为子模块自带缓存. |
| Layer 2 无状态 Service 缓存 (11 个) | 无状态薄壳, 底层 engine 已由 Layer 1 覆盖. 移除后消灭一整类 shutdown 清理问题. |
| `simple_cache()` / `ttl_cache()` 装饰器 | 零生产调用, 死代码. |
| `get_tool_cache()` / `get_llm_cache()` / `get_path_cache()` | 零生产调用, 死代码. |

### 不缓存的 (有意设计)

| 组件 | 不缓存原因 |
|------|-----------|
| Agent 实例 | 无状态处理架构, 每次请求创建新实例, 状态由外部管理 (DB + Layer 0) |
| LangChain `create_agent()` | 绑定动态 system_prompt (含记忆上下文), 天然需要每次重建 |
| Middleware 对象 | 持有运行时状态 (重试计数器), 必须每次新建 |
| 无状态 Service 壳 | 底层 engine 已由 Layer 1 覆盖, 重建只是创建几个 Python 对象 (微秒级) |

---

## 测试中的缓存管理

- **单元测试**: autouse fixture 调用 `reset_config_cache()`, 见 `tests/unit/config/conftest.py`.
- **集成测试**: `_reset_db_and_service_state` fixture 重置 DB cache lock / vector cache / memory cache / YAML dict 缓存, 并 teardown 关闭全部 DB managers. 见 `tests/integration/conftest.py`.

---

## 新增缓存的检查清单

- [ ] 是否持有 OS 级资源? (如果不是, 不需要缓存)
- [ ] 是否注册到 LifecycleRegistry? (如果是全局单例)
- [ ] close 方法是否幂等? (未启用时是 no-op)
- [ ] 是否有界? (LRU/TTL/固定数量)
- [ ] 测试中是否有隔离机制? (reset/clear fixture)
