# config.yaml 参考

**版本**: v2 | **更新**: 2026-09-16

`config.yaml` 只保存非敏感应用配置。未知字段会被 `config_doctor --strict` 拒绝。密钥不要写入本文件。

## logging

```yaml
logging:
  level: "info"
  file_max_bytes: 20971520
  backup_count: 5
```

## core

```yaml
core:
  cache:
    pinned_memory_cache_size: 50
    conversation_cache_size: 100
    enable_cache_stats: true
```

## api

```yaml
api:
  host: "127.0.0.1"
  port: 8000
  file_server_base_url: null
  file_url_ttl_days: 30
  cors_origins: ["*"]
  cors_methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
  cors_headers: ["*"]
  cors_allow_credentials: true
  docs:
    enable_swagger_ui: true
    enable_redoc: true
    docs_url: "/docs"
    redoc_url: "/redoc"
    openapi_url: "/openapi.json"
  tool_call_display:
    enable: true
```

运行时可由 `API_PORT`, `FILE_SERVER_BASE_URL`, `FILE_URL_TTL_DAYS`, `ENABLE_TOOL_CALL_DISPLAY` 覆盖。`FILE_SIGNING_SECRET` 只允许在 `.env`。

## auth

```yaml
auth:
  user_management:
    enable_static_user_management: true
```

运行时可由 `ENABLE_STATIC_USER_MANAGEMENT` 覆盖。

## storage

```yaml
storage:
  file_store:
    max_user_storage_mb: 500
    cleanup_target_mb: 400
    deduplication_enabled: true
    quota_check_enabled: true
```

数据库和向量路径由 `path_resolver` 管理, 不在 `config.yaml` 配置。

## retry

```yaml
retry:
  expert_agent:
    max_retries: 1
    initial_delay: 2.0
    max_delay: 20.0
  http_api:
    max_retries: 2
    base_delay: 1.0
    rate_limit_delay: 3.0
    retryable_status: [429, 500, 502, 503, 504]
  mcp:
    max_retries: 1
    base_delay: 1.0
    rate_limit_delay: 3.0
  grounding:
    max_retries: 0
```

## inference

```yaml
inference:
  embeddings:
    enabled: true
    model: "local-embedding:bge-m3"
  reranker:
    enabled: false
    base_url: "http://localhost:8768"
    timeout: 30.0
    instruction: ""
  content_analyzer:
    model: "ark-agent-plan:doubao-seed-2.0-mini"
    model_params:
      max_tokens: 2048
    arc_model: "ark-agent-plan:doubao-seed-2.0-mini"
    arc_model_params: {}
    fallback_model_params: null
  image_description:
    model: "ark-agent-plan:doubao-seed-2.0-mini"
    model_params: {}
    read_image_model: ""
    read_image_model_params: {}
  code_description:
    model: "ark-agent-plan:doubao-seed-2.0-mini"
    model_params:
      max_tokens: 2048
    max_input_chars: 50000
  document_description:
    model: "ark-agent-plan:doubao-seed-2.0-mini"
    model_params:
      max_tokens: 2048
    max_input_chars: 50000
  health_data_extraction:
    model: "ark-agent-plan:doubao-seed-2.0-mini"
    model_params: {}
    timeout: 60.0
    audit_model: "ark-agent-plan:doubao-seed-2.0-pro"
    audit_model_params: {}
  tool_filter:
    model: "local:qwen3:4b-instruct"
    model_params:
      format: "json"
      temperature: 0.0
      num_predict: 256
      num_ctx: 4096
    timeout: 5.0
    min_tools_for_filter: 2
  experts:
    default_model: "deepseek:deepseek-flash"
    default_model_params: {}
    web_research_model: ""
    web_research_model_params: {}
    web_research_synthesis_model: ""
    web_research_synthesis_model_params: {}
    geo_research_model: ""
    geo_research_model_params: {}
    datapro_model: ""
    datapro_model_params: {}
    grounding_model: "gemini:gemini-2.5-flash-lite"
    grounding_model_params: {}
    grounding_timeout: 90.0
    url_context_enabled: true
    url_context_model: "gemini:gemini-3.1-flash-lite-preview"
    url_context_quick_timeout: 15.0
    url_context_deep_timeout: 20.0
    url_context_max_urls: 4
    maps_grounding_model: "gemini:gemini-3.1-flash-lite-preview"
    maps_grounding_model_params:
      temperature: 0.1
      max_output_tokens: 4096
    maps_grounding_timeout: 30.0
    grounding_fallback_enabled: true
  image_generation:
    model_id: "ark-agent-plan:doubao-seedream-5.0-lite"
  wechat_publish:
    model: "deepseek:deepseek-flash"
    model_params:
      max_tokens: 4096
  retry:
    max_retries: 1
    total_timeout: 900.0
    initial_delay: 2.0
    max_delay: 30.0
  fallback:
    text_model: "deepseek:deepseek-flash"
    text_model_params: {}
    vision_model: "ark-agent-plan:doubao-seed-2.0-mini"
    vision_model_params: {}
```

Provider API Key 和 `*_BASE_URL` 不写在此处, 由 `.env` + `provider_registry.py` 管理。

> 注: `inference.reranker.base_url` 可被 `.env` 的 `RERANKER_BASE_URL` 覆盖 (registry 优先, 见 [reranker-service.md](./reranker-service.md)); `code_description` / `document_description` 分别控制上传代码文件 / 文档的后台 AI 摘要 (desc = 摘要+原文/源码 结构)。

> 注: `content_analyzer.fallback_model_params` 不是独立的 fallback 模型(v1.8.4 已移除模型切换 fallback), 而是判断类任务(置顶记忆更新 / `SimpleMemory`)对全局 `inference.fallback.text_model_params` 的额外 bind 参数覆盖, 例如需要启用思考时显式配置; 索引生成 / 弧蒸馏仍走全局默认。

## tools

内置工具定义在 `src/config/tool_catalog.py`。`config.yaml` 只覆盖差异:

```yaml
tools:
  tool_groups:
    todo_manager_group:
      keywords: ["待办", "任务", "todo"]
      prompt_hint: "..."
  internal_tools:
    search_memories:
      enabled: true
      timeout: 45.0
      config:
        max_results: 20
        enable_vector_search: true
  external_tools:
    python_executor:
      enabled: true
      config:
        default_timeout_seconds: 5.0
        max_timeout_seconds: 30.0
  mcp_servers:
    baidu_search:
      name: "baidu_search"
      transport: "sse"
      url: "http://appbuilder.baidu.com/v2/ai_search/mcp/sse?api_key=${BAIDU_API_KEY}"
      headers:
        Authorization: "Bearer ${BAIDU_API_KEY}"
      enabled: true
      timeout: 60.0
      tool_names:
        AIsearch: "baidu_search"
  skills:
    xlsx:
      name: "xlsx"
      source: "./skills/xlsx/"
      backend: "executable"
      associated_tools: ["skill_executor"]
      enabled: true
```

旧工具名 `examine_attachment` 和 `read_image` 已废弃, 分别使用 `read_file` 和 `analyze_image`。

## channel_push

渠道推送网关连接配置 (定时消息/价格提醒/通知经渠道网关主动推送, 如 weixin-gateway):

```yaml
channel_push:
  gateway:
    url: "http://127.0.0.1:18789"
```

`CHANNEL_GATEWAY_URL` 可覆盖 gateway URL。`CHANNEL_GATEWAY_TOKEN` 只允许在 `.env`。
**漏配会导致微信定时消息/价格提醒/通知静默失败**(日志告警但消息不发出); `config_doctor --strict` 自检。
渠道网关侧对接契约见 weixin-gateway 仓库 `docs/assistant-integration.md`。

## smtp

系统级邮件发送配置, app 进程内所有发邮件工具(scheduled_messenger 等)共用。

```yaml
smtp:
  host: ""            # 留空时使用各工具自身默认值(如 smtp.qq.com); 建议显式配置
  port: 465
  use_tls: true
  from_address: ""    # 留空回退 .env SMTP_FROM_ADDRESS, 再回退 username
  username: ""        # 留空回退 .env SMTP_USERNAME
  password: ""        # 留空回退 .env SMTP_PASSWORD (密钥不进 config.yaml)
```

`SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM_ADDRESS` 是凭据, 留空时由
`credentials_registry` 从 `.env` 回退。此段供 app 进程内所有发邮件组件
(scheduled_messenger / 价格监控 / NotificationService) 共用。

## calendar_sync

Graph 同步引擎全局默认配置 (MSA 个人账户: TODO 双向 + 日历双向添加/编辑,
删除单向自愈, 设计见 `docs/development/msgraph-sync-design.md`):
```yaml
calendar_sync:
  enabled: true            # false 时引擎不启动
  interval_seconds: 600    # 周期兜底间隔 (下限 30)
  todo_list_name: "Assistant"   # 远端专用 To Do 清单名
  calendar_name: "Assistant"    # 远端专用 Outlook 子日历名
  timezone: "Asia/Shanghai"     # Graph 请求体时间换算默认时区 (IANA)
```

per-user 覆盖经 `PUT /v1/sync/msgraph/settings` (存用户级 graph_sync.db 的
graph_sync_settings 表, 键同名; `null` 清除覆盖回退全局默认)。授权与同步
设计见 `docs/development/msgraph-sync-design.md`。
