# 项目变更日志

**版本**: v1.9.0 | **更新**: 2026-07-01

## 股票监控架构重构 + 统一通知基础设施 (2026-07-01)
- **容器瘦身 + 重命名**: market-monitor 容器瘦身为纯行情查询服务并重命名为 `quote-service`, 只保留 TDX 连接 + `/quote`(单只) + `/quotes`(批量) + `/health`; 删除容器内规则存储/轮询引擎/派发逻辑 (`store.py`/`monitor.py`)
- **价格监控业务内化到 app**: 规则存储/一次性轮询/派发全部移入 app, 对齐定时消息范式 (per-agent `price_alert.db` 物理隔离, `LifecycleRegistry` 管理, 常驻轮询 task 仿语义缓存清理). 新增 `src/storage/service/price_alert_service.py` (`PriceAlertEngine` 全局单例) + `src/core/market_hours.py` (交易时段)
- **一次性语义**: 触发即提醒一次并自动结束 (规则转 disabled), 无长期监控/穿越状态机/日限; 创建时已穿越阈值则下次轮询立即触发
- **新增 `/quotes` 批量端点**: quote-service 暴露批量行情查询 (复用 `TdxClient.get_quotes` 批量能力), app 轮询引擎跨用户去重 code 后一次批量取价, 保留原容器内取价去重优化
- **统一通知基础设施**: 新增 `src/core/notification.py` (`NotificationService` + `DeliverySpec` + `resolve_delivery`) 与 `src/core/email_client.py` (`EmailClient`), 收敛原散落在 `scheduled_message_service` / `openclaw_message_splitter` / `price_alert_base` 三处的渠道配置解析与派发重复; `openclaw_defaults` 统一到 `openclaw.notification_defaults`
- **环境变量**: `MARKET_MONITOR_BASE_URL` → `QUOTE_SERVICE_BASE_URL`; 删除 `MAX_ALERTS_PER_RULE_PER_DAY`
- **删除**: `src/tools/internal/price_alert_base.py` (工具 HTTP 基类, 逻辑内联到 `query_stock_price_tool`); 容器 `aiosmtplib` 依赖

## python-executor 容器合并到 tool-runtime (2026-06-30)
- **废弃 python-executor 独立容器**: `python_executor` 工具改为调用 tool-runtime `/execute`(与 `skill_executor` 共用同一运行时), 删除 `docker/python-executor/` 目录及 docker-compose 中的服务定义
- **配置统一**: `PythonExecutorTool.base_url` 改由 `TOOL_RUNTIME_BASE_URL` 环境变量配置(与 `SkillExecutorTool` 一致), 移除 config.yaml 中的 `base_url` 项; 生产环境零手动配置(Docker 已注入该 env)
- **scipy 移除**: tool-runtime 仅预装 numpy/pandas; 科学计算/曲线拟合等需求应做成 skill 注入领域知识
- **契约不变**: `python_executor` 仍为 search 发现的纯计算入口(stdout 文本, `collect_outputs=False`), `skill_executor` 仍为 load_skill 激活的文件生成入口
- **收益**: 少一个镜像(构建/部署/资源隔离单元), 两个代码执行入口共用同一运行时与配置源

## v1.8.5 A股价格监控服务 (2026-06-29)
- **新增 market-monitor 服务**: 独立常驻容器 (`docker/market-monitor/`), 单 TDX 连接统一轮询所有用户监控规则, 价格穿越阈值时经 OpenClaw 推送微信告警
- **pytdx 行情接入**: best-IP 并发测速 + heartbeat 保活 + 运行时动态故障转移; 不依赖 mootdx, 纯 Python 轻量依赖
- **穿越状态机**: 价格突破阈值告警一次, 回归后重新装填, 避免刷屏; 每规则每天告警上限 (默认 10 次); 仅交易时段轮询
- **自包含架构**: 规则存储 (SQLite) + 轮询 + 状态机 + 派发全在监控服务侧; app 侧 3 个内部工具为超薄 HTTP 客户端
- **内部工具 stock_watch_group**: `create_price_alert` / `list_price_alerts` / `cancel_price_alert`; `is_available()` 检查当前用户微信凭证; 市场按代码前缀自动推断
- **部署**: 生产挂 `default` 网络 (需外网访问通达信服务器); 开发/生产各起独立容器, 与生产隔离 (套用 python-executor/tool-runtime 范式)
- **测试**: 42 项单元测试覆盖状态机/日限/tick 编排/工具 HTTP 契约

## v1.8.4 移除 LLM 模型切换 fallback (2026-06-24)
- **移除换模型重试**: 删除 `ContentAnalyzerConfig` / `ImageDescriptionConfig` / `HealthDataExtractionConfig` 的 `fallback_model` / `fallback_model_params` 字段及对应 YAML 示例
- **简化推理代码**: `SimpleContentAnalyzer`、记忆索引生成、`UnifiedHealthExtractor`、图片描述、`ReadImageTool` 均改为单次调用主模型
- **保留业务兜底**: 健康提取失败返回 `[]`、图片描述失败返回默认描述、置顶记忆失败返回空操作、索引失败不影响主流程
- **保留非 LLM fallback**: 同模型重试、向量→SQL、地图供应商降级、MCP 格式化降级、工具筛选器优雅降级等不受影响
- **测试同步更新**: 删除/改写相关 fallback 单元测试

## v1.8.3 记忆系统深度重构 (2026-06-23)
- **PinnedMemoryService拆分**: 置顶记忆服务从core.py独立, 核心文件减负33%
- **存储层消除DAO穿透**: 补齐Service层接口, 所有外部访问统一走Service层
- **原生messages记忆类型**: 新增native_messages记忆类型, 历史对话走原生数组
- **Inference清理**: 移除生产代码Mock泄露(USE_MOCK_LLM), 提取JSON mode config公共函数
- **记忆系统清理**: 三批次清理Mock残留/死代码/僵尸配置, 激活缓存配置联动
- **测试覆盖提升**: 单元测试2705→2855(+150), 覆盖率72.36%→75.71%
- **Worktree工具链**: 新增setup_worktree.py初始化脚本, 软链gitignored配置
- **单元测试规范重写**: 删除UTMS死代码, 重写单元测试设计规范

## v1.8.2 健康数据系统完善 (2026-06-07)
- **集成测试清理**: 删除6个冗余集成测试文件(13→7), 重写轮次号分配集成测试
- **单元测试扩展**: 新增ConversationService/扩展HealthDataService/TodoService/ScheduledMessageService测试
- **E2E测试标记完整覆盖**: 集成测试通过率100%

## v1.8.1 Expert体系重构 (2026-05-24)
- **旧Expert体系废弃**: 移除DocumentProcessingExpertTool、ExpertPackageManager、Git Submodule架构和ClaudeCLIWrapper
- **新Expert工具体系重写**: 基于LangChain BaseTool + 自主Agent编排的轻量级专家工具架构
- **WebResearchTool**: 内部启动独立LangChain Agent自主编排多源搜索, 支持quick/deep两档深度
- **GeoResearchTool**: 地理出行研究专家, 封装百度地图API + Gemini搜索
- **McpBridge**: 重写MCP工具桥接器, Session复用、内置重试、限流感知
- **Response Formatters**: MCP响应后处理器, 自动解码双JSON/单JSON搜索结果
- **三层工具架构**: ToolsManager统一调度内部工具 + 专家工具 + MCP工具
- **ExpertCache**: TTL缓存机制, 减少重复外部调用
- **ExpertModelFactory**: 专家Agent独立模型工厂, 与主对话模型解耦

### v1.8.0 专家工具系统实施 (2026-02-14)
- 专家工具子模块架构 (Git Submodule), 文档处理专家, Claude CLI包装器
- **v1.8.1已整体废弃**, 由新Expert工具体系替代

### v1.7.1 健康检查系统重构 (2025-12-21)
- 移除AsyncUnifiedDataManager (~1,800行) + Repository层 (~500行) + 其他冗余 (~2,700行), 总计移除约6,800行
- 引入专业化Service层架构 (ConversationService, TodoService, MemoryService, VectorService)
- Factory Pattern实现 (移除BaseAgentImpl ~600行)

### v1.6.1 智能与存储分离 (2025-12-16)
- 存储接口重构: 拆分store_conversation_round()为store_conversation_content()和store_conversation_index()
- 四个并行操作: 对话内容存储、向量存储、置顶记忆更新、索引生成

### v1.6 统一数据源架构 (2025-12-16)
- ConversationData统一数据结构, 四个并行操作数据一致性

### v1.5 双路检索+配置重构 (2025-12-14)
- 双路检索架构: SQL为主向量为辅
- 配置职责分离: 模型选择 vs 参数配置
- 三层配置优先级: 环境变量 > config.yaml > 默认值

### v1.4 存储架构简化
- 移除Repository适配器层, DAO直接访问

### v1.3 置顶记忆重构
- 移除PersonalMemoryTool, 集成到记忆体系

---

*详细重构报告和技术决策请参考各版本对应提交*