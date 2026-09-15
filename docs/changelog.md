# 项目变更日志

**版本**: v1.11.0 | **更新**: 2026-09-16

## 日程-提醒所有权设计 + 领域工具全折叠 + 0.8B 过滤层加固 (2026-09-16)

- **所有权不变式**: 系统能动的提醒 = 定时消息 (schedule_message_*); 日历只记录不主动推送 — "我会到点提醒你"这句承诺背后必须站着一条定时消息, 手机日历闹钟是用户侧订阅的副产品而非 Agent 承诺. 周期性提醒走重复日程由手机日历承担 (messenger 不加 recurrence, 避免制造真同位替代)
- **影子关联**: `schedule_message_*` 增可选 `related_event_id` (工具层硬校验日程存在性, 幻觉 ID 报错引导); `list_scheduled_messages` 展示关联日程; `create_calendar_event` 响应携带 reminder_hint 引导下一步关联
- **影子跟随本体 (服务层不变式, 不依赖模型编排 — 工具轨迹不跨轮)**: 删日程自动取消关联 pending 消息; 改开始时间按偏移顺延 (保持"提前N分钟"相对关系); 仅改非时间字段不级联但报告关联数. 级联枚举用户全部 scheduled_message 分库 (库仍按 thread/agent 物理隔离, 用户级迁移留作后续); 取消以 DB 状态为准, 顺延以 DB send_time 为准 (`_send_message` 到点复查: 未到点重挂定时器)
- **全折叠**: personal_assistant 核心工具收敛为纯机制层 (memory_recall_group + search_available_tools + load_skill), todo/calendar 移入休眠; core 判定标准 = "该 agent 几乎每轮都需要" (通用助手纯机制, 领域助手机制+主域)
- **keyword 承重墙加固**: todo 删"提醒事项" (子串匹配把一切"提醒"query 拉进 todo 候选, 所有权违规) 补"备忘/记一下"; calendar 补"开会" (原打不穿任何召回信号); messenger 补"叫醒/闹钟"
- **0.8B 过滤层三件套 (零误杀结构化)**: ① none 哨兵翻转 — 字面 none/合法空被尊重 (返回空列表), 仅解析失败与编号全越界降级全保留; ② 高置信豁免 — 名称命中或多信号高分(≥8.0)候选结构性不可杀不送审, 0.8B 只审词域重叠的泥泞中间带; ③ 同轮重试免过滤 — 第二次 search 自动跳过降噪全量返回 (降级阶梯: 精确→召回)
- **prompt_hint 可用性过滤**: 渠道全未配置的休眠组不再注入 hint (此前配置原始名直收, 指挥模型用不存在的工具)
- **benchmark 定标**: tool_filter — case2 (开会+提醒) 转双 gold, 单次提醒类 calendar 转排除, 新增改期/取消/复合用例; fuzzy 仅保留真可选工具 (note_taker 类) 与上下文依赖操作 (取消类); **gold 误杀率 (gold_kills) 成为一等门禁指标** (easy/medium 须为 0) — 训练迭代以 FN 为准; agent_retrieval 补隐式意图/口语变体 query, catalog_snapshot 补 calendar_manager_group
- **训练侧待办**: 按 `scripts/benchmarks/tool_filter/test_cases.py` 新定标重生成训练数据 → 微调 sft08b → 以 gold_kills 门禁验收

## MSA Graph 同步子系统落地 (切片⑤-⑦) + 授权通道切换自建 app (2026-09-15)

- **授权通道切换**: 主通道改为自建 app「JFT Assistant」(client_id 经 `MS_GRAPH_CLIENT_ID` env 注入, 与 homelab 多账户通道共用注册但**各自独立授权不共享 token** — refresh token 每次刷新轮换, 共享会互相打断); 代码默认回落第一方公共客户端作应急 fallback; scope 对齐 homelab 四件套 (加 `Contacts.ReadWrite`, 授权面预留)
- **日历单向 push (切片⑤)**: `calendar_sync` 纯函数 — 全天 inclusive→exclusive 换算 (与 ics_builder 同构), 镜像修复语义 (本地软取消/硬删→远端删除, 远端被编辑→本地内容 revert, 远端被删→重新 POST, 容器内非映射条目跳过告警, 重复事件暂不同步)
- **同步引擎 (切片⑤/⑥)**: `graph_sync_engine` — PriceAlertEngine 同款骨架 (单例/lifespan 启停/周期 tick), 扫描 `data/*/credentials/` 发现已授权用户, 逐用户串行 (分页取数跟随 nextLink); 授权失效暂停 + token 文件替换自动恢复, 可选 `notify_delivery` 设置经 NotificationService 提醒; 消费 `graph_sync_map`/`graph_sync_settings` 两张闲置表
- **写后即时信号 (切片⑥)**: TodoService/CalendarService 写成功后 `notify_local_write` 投递 per-user 信号, 引擎 wake 提前同步 (只加速本地→远端方向, 远端感知仍靠轮询)
- **配置 (切片⑥)**: `config.yaml` 新增 `calendar_sync` 段 (enabled/interval_seconds/容器名/时区, `CalendarSyncConfig` 入根 schema); per-user 覆盖经 `GET/PUT /v1/sync/msgraph/settings` (null 清除回退全局默认)
- **internal 工具 (切片⑦)**: `msgraph_sync_group` (休眠, 挂 optional_tools) — `msgraph_connect` (对话内引导 device flow 授权, 幂等; 透传对话上下文) / `msgraph_sync_status` (授权状态 + 引擎最近结果)
- **授权完成回调**: token 落盘即 `notify_local_write` 唤醒引擎首轮同步 (消除 tick 延迟) + 向发起对话的微信渠道配置推送回执 (经渠道自动发现机制; 无配置/失败仅日志); `msgraph_connect` 经 `is_available` 在已授权态隐藏 (认证一次性, 解绑后自动复现)
- **循环导入修复**: storage services 改惰性 import `notify_local_write` (模块级导入依赖加载顺序碰巧成立, 任何入口顺序均安全)
- **设计文档**: `docs/development/msgraph-sync-design.md` 授权通道章节按新前提重写, 切片 1-7 全部 ✅

## 指令文档统一单 SSOT, CLAUDE.md 转 symlink (2026-09-15)

- **决策**: 操作指令与约束仅以 [AGENTS.md](../AGENTS.md) 为 SSOT, CLAUDE.md 不再独立维护, 转为指向 AGENTS.md 的 symlink (git mode 120000, 与 `.claude/skills/*` 同模式). 动因: Claude Code 截至 2026-09 仍无 AGENTS.md 原生支持, symlink 为零漂移的兼容手段; 双文件版本行等同步点随之消除
- **Claude 专属资产描述迁移**: 原 CLAUDE.md 的斜杠命令清单迁至 `.claude/commands/*.md` 的 YAML frontmatter `description` (描述随资产走, Claude Code `/` 列表原生显示); `.claude/agents/unit-test-analyzer.md` 自带 description, 无需迁移


## 微信公众号 skill 化 + 发布管道去 LLM 化 (2026-09-16)

- **wechat_official_account skill**: 新增 `skills/wechat_official_account/` (prompt_only), 内置公众号文风约束 (负向平行结构/黑话/书面腔等禁令 + 守恒/出稿自查) 与写作发布流程; `associated_tools: ["wechat_publish"]` — 首个关联 **internal tool** 的 skill (此前仅 external/skill_executor), 经 `tool_manager.create_tools` 的 internal 分支创建并随 load_skill 注入
- **工具可见性**: wechat_publish 从 thought-assistant 常驻 tools 移除, 改为 load_skill 后动态注入 (未配置 wechat_mp 凭证时 is_available 过滤, 工具不注入); 授权提示移入 SKILL.md
- **发布管道去 LLM 化 (行为变更)**: 移除 `_refine_content` 校对排版环节 — 文风/格式责任上移主 agent (经 skill 约束), 终稿原样进草稿, 管道内仅保留 1 次 LLM (摘要+封面提示词); `WechatPublishConfig` 删 `refine_model`/`refine_model_params`

## 日历服务 (ICS 订阅) + TODO 用户级统一视图 (2026-09-15)

- **日历子系统**: 新增 `src/calendar/` 包 — `ics_builder` (RFC 5545 手写生成: 转义/75 octets 折行/UTC 定时与 DATE 全天/RRULE 透传)、`rrule_utils` (dateutil 服务端展开, 供"今天日程"查询, 500 次上限防失控)、`subscription` (持久 token, sha256 落库); 新增 `python-dateutil` 显式依赖 (原为 pandas 传递依赖)
- **日程存储**: 用户级 `data/{user_id}/database/calendar.db` (`CalendarEvent` + `CalendarSubscription` 两表); aware UTC 存储, 全天事件 end_time 为 inclusive 结束日 (ICS 生成时转 exclusive); thread/agent 降级为行级溯源字段
- **ICS 订阅**: 公开端点 `GET /v1/calendar/ics/{user_id}/{token}/calendar.ics` (中间件 bypass, token 即凭证; 双装饰器支持 env_prefix); 无过期机制 (HMAC 过期会静默断流手机订阅), 撤销=显式 API; feed 窗口 过去 30 天 + 未来 180 天
- **REST API**: `/v1/calendar/events` (CRUD + `expand=true` 展开重复) 与 `/v1/calendar/subscription` (创建返回完整 URL/token 明文仅一次; 列表不泄露 token; 撤销即失效)
- **Agent 工具组**: `calendar_manager_group` 四工具 (create/list/update/delete_calendar_event); naive 时间按用户时区转 UTC (全天事件跳过时区转换保 DATE 语义); 中英文频率别名 (每天/每周/每月/每年/每两周); 挂载 personal_assistant
- **TODO 用户级迁移**: todo.db 从三级隔离迁至 `data/{user_id}/database/`; `list_todos`/`get_fresh_todolist` 去除 thread_id 过滤 (统一视图); 一次性脚本 `scripts/migrate_todo_to_user_level.py` (源文件改名 `.migrated` 幂等, 支持 `--dry-run`)
- **TODO REST**: `/v1/todos` CRUD (用户级统一视图, status/priority 过滤)

## DeepSeek V4.1 Flash 上线, V4-Flash/Vision-Exp 退役 (2026-09-10)
- **官方**: 2026-09-10 发布 `deepseek-flash` (= V4.1-Flash, 552B MoE 非对称 Causal Encoder-Decoder 架构, 原生视觉), `deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` 已退役 (旧名暂时路由至新模型); 官方节点 `deepseek-v4-pro` 2026-09-14 起同样路由至 V4.1-Flash (退役过渡)
- **内置清单**: 新增 `deepseek:deepseek-flash`, 移除 `deepseek:deepseek-v4-flash` / `deepseek:deepseek-v4-flash-vision-exp` (项目无兼容层); 定价对齐新峰谷价 (高峰档 $0.30/$1.20/$0.006); `reasoning_effort` 增 low 档; 默认模型引用 (experts 默认/微信发布/fallback) 同步切至新 ID

## 知识库图片可检索与按需读图 (2026-09-04)
- **索引侧**: chunker 提取整行图片引用为节级锚点 (`metadata.images`), 不再剥离; indexer 经 `KnowledgeImageDescriber` (vision 题注, sidecar 按 content hash 幂等) 生成图片独立 chunk (`chunk_type=image`); retriever 图片条目输出完整 `image_ref` (`kb:语料相对路径`) 与读图引导; build 脚本默认启用图片题注 (`--skip-image-describe` 可关) 并落 `kb_meta.json`
- **运行时**: 新增 `kb_read_image` 外部工具 (`image_ref`+`prompt`, 语料根白名单解析); **companion 机制**: 宿主工具 (tea_knowledge) 激活时伴随激活, 不进 search catalog 不可独立发现 (tools_config 新增 `companions` 字段)
- **多模态直注**: `KbImageInjectMiddleware` 在多模态主模型下短路 `kb_read_image` 直注原图 (HumanMessage image_url 块), 非多模态降级为视觉转述; 上传图工具体系 (analyze_image/read_file) 零改动

## 新增 deepseek-v4-flash-vision-exp 内置模型 + V4 定价对齐 (2026-09-04)
- 内置模型清单新增 `deepseek:deepseek-v4-flash-vision-exp` (vision); DeepSeek V4 系列定价对齐官方峰谷分时 (取高峰档)

## RERANKER_BASE_URL env 覆盖链路接通 (2026-08-30)
- **问题**: provider_registry 已定义 `local-reranker.base_url_env` 但 RerankClient 构造链从未消费, 容器内 config.yaml 的 `localhost:8768` 无法覆盖, rerank 恒降级
- **修复**: 新增 `resolve_rerank_base_url` (registry 优先, config.yaml fallback) 并接入 tea_knowledge 工具的 `_build_reranker`; 生产 compose 为 app 容器注入 `RERANKER_BASE_URL=http://host.docker.internal:8768` (同 `LOCAL_EMBEDDING_BASE_URL` 模式)

## 移除 --sandbox 沙盒模式, TMPDIR 注入无条件化 (2026-09-04)
- **背景**: `--sandbox` 为受限沙盒 (DSH Landlock/bwrap) 设计的环境适配层 (TMPDIR 注入/跳过 SimpleCleaner 归档/工具路径与项目根定位特判), 任务面与 quick 门禁完全一致, 长期双通路维护冗余
- **触发验证**: strace 实证当前沙盒中 SQLite 3.50.4 选临时目录前 `access(dir, W_OK|X_OK)` 探测 `/var/tmp` 返回 EROFS → 自动回落 `/tmp`, 常规 `--quick` 在同等受限策略下 7/7 全绿; 8/15 的 code 14 根因是旧 LD_PRELOAD 拦截层令 access() 探测说谎 (返回可写) 而 O_CREAT 被拒
- **改动**: 删除两脚本的 `--sandbox`/`sandbox_mode` 全部分支 (`run_test_suite.py` 的 `run_sandbox_tasks` + `static_analysis.py` 的 `run_sandbox_static_analysis` 等); 单元/集成测试子进程改为 `TMPDIR` 缺省时无条件 `setdefault("TMPDIR", "/tmp")` — 保险不依赖沙盒 access() 行为, 旧式拦截层环境亦不复现 code 14; 清理归档恢复全模式执行
- **破坏性变更**: `--sandbox` CLI 参数移除, 受限沙盒环境直接使用标准命令 (`--quick` / `static_analysis.py`)

## 二进制文档解析管线 (docx/pdf/pptx) + 统一文档 AI 概要 (2026-08-24)
- **背景**: 微信渠道二进制文档 (docx/pdf/pptx) 此前降级为"该类型暂不支持内容提取"提示行; 上传文档 brief 为确定性首行提取, 质量弱且无 AI 概要
- **解析引擎**: doc2md 独立仓库 (HTTP 服务, 本地 Mac 主机裸 venv + launchd :8769) — 数字 docx (python-docx+OMML)/数字 pdf (PyMuPDF 文本层) 秒级直转; 扫描件 PaddleOCR-VL (paddle cpu + MLX-VLM Metal 加速, 实测 ~6s/页); Apple Silicon 适配 (平台条件依赖 + mlx-vlm-server 后端); defer_scan 探测模式 (扫描件秒级返回页数不跑 OCR)
- **入口**: `FileData` 增 `content_b64` (与 content 二选一, 验证层强制); gateway 放行 BINARY_DOC_EXTS ≤20MB (statSync 预检); `prepare_document_attachments` 按 content_b64 分派
- **编排分流**: 数字版同步转换当轮讨论; 扫描件立即应答 (brief 含页数+预计时长) → 后台 OCR (页数上限 30) → 概要完成后 desc 重组 + wechat 渠道 push; 超限/服务不可用降级仅存原件 (brief 说明); 内嵌图全部入库 (md 引用替换 `[图 file:id]`, 完整保留文档内容)
- **统一文档概要**: `DocDescriber` (CodeDescriber 同构) — 上传文档 (md/txt/csv/转换后 md) 一律后台 AI 概要, desc 更新为 概要+原文; 配置 `inference.document_description`
- **export 修复**: 后台 LLM 摘要完成后同步更新 DB brief (此前只写 document_meta.summary, brief 停留首段提取)
- **配置**: `DOC2MD_BASE_URL` (runtime_env) / `DOC2MD_TOKEN` (credentials_registry); store_document 重构共享 _store_document_entry 骨架
- **生产验证**: 五类文档全通 — 数字 docx 0.1s / 数字 pdf 秒级 / 扫描 pdf ~6s/页 (MLX) / pptx 11.8s / 扫描 docx 9.7s (soffice→PDF→OCR)。注: macOS 26.5 beta 上 LibreOffice 首启需 GUI 手动放行一次 Gatekeeper (System Settings → Privacy & Security → Allow), 之后 headless 正常

## desc 统一结构 + read_file 双模式 + 代码文件摘要 (2026-08-17)
- **背景**: desc 语义此前四种并存 (图片=画面描述/文档=原文/chart-skill=源码/export=摘要), 生成图片/视频的 detail 不落 desc (read_file 只见一句话); read_file 的 max_chars 调大重读导致前缀内容在上下文重复累积
- **desc 统一**: `compose_desc/split_desc` (desc_writer) 单一分隔符来源; 全部 8 类文件 desc = `摘要\n\n---\n\n原文/源码` — 上传文件原文区=原文 (图片=画面描述), 生成文件源码区=源码/生成参数; `register_tool_output` 新增 `source` 参数统一写入通道, chart/skill 删除各自 write_desc 覆盖, image/video 的 detail 经 source 落盘 (修复不对称), export_document 追加 GFM 原文且后台 LLM 摘要完成后重组
- **read_file 双模式**: 默认仅返回摘要 + `original_start_line` 原文入口; 传 `start_line` 按行窗口读取, `next_start_line`/`has_more` 续读 (schema 纯行级, 模型视野无字符预算参数); 内部 50000 字符安全阀透明化于 note, 病态超长单行硬截断; 旧格式 desc 兼容回退 entry.brief; 删除 max_chars/truncated
- **代码文件摘要**: `code_extensions` 白名单 (约45种源码扩展名) 识别 → store_document 后 `background_generate_code_summary` 后台生成 (CodeDescriber, 配置 `inference.code_description`), desc = 结构化摘要(语言/功能/结构)+代码原文, DB brief = AI 一句话; `update_description` 新增 `desc_summary` 参数并修复空 brief 兜底按 file_type 生成

## 移除 OpenClaw 兼容层, 渠道对接切换为 X-Channel 请求头 + /channel/send (2026-08-16)
- **背景**: 微信接入已切换为自制 weixin-gateway (独立仓库), assistant 定位回归纯 agent 后端; openclaw 宿主及其入站伪装协议不再需要, 渠道胶水 (markdown 过滤/分块/文件转换) 职责移交 gateway
- **删除**: OpenClawFilterMiddleware / core.openclaw_filter / openclaw_client / openclaw_message_splitter (含 2000 字拆分补发 + 3s 延迟机制) / stream_openclaw_response 心跳路径 / is_openclaw 全链路 / build_media_lines (MEDIA: 行)
- **新增**: `_extract_channel_config` (X-Channel / X-Channel-Account / X-Chat-Id 请求头) + `_provision_channel_config` 渠道配置自动发现 (user_channel_config 字段改为 `target`/`account_id`, 字段变化自愈更新); `channel_push_client` (POST /channel/send: `{channel:"weixin", account_id, to, text}`) + `channel_push_config`
- **配置改名 (破坏性)**: `OPENCLAW_GATEWAY_URL` → `CHANNEL_GATEWAY_URL`, `openclaw_gateway_token` → `channel_gateway_token` (env `CHANNEL_GATEWAY_TOKEN`), config.yaml `openclaw.*` 段 → `channel_push.gateway.url` (`notification_defaults` 废弃); config_doctor 自动迁移旧段并报错旧死配置
- **联动**: notification/resolve_delivery 简化 (去渠道名映射层), price_alert 删 `openclaw_channel` 字段, exported_files 恒定单一 markdown 链接格式 (gateway 解析尾部链接段下载转发)
- **部署时序**: 与 weixin-gateway 新版同窗口切换 (契约双侧破坏性变更)

## 纯文本文件 (md/txt) 纳入文件管理体系 (2026-08-16)
- **背景**: 此前 gateway 嵌入的文件文本会原样进入 user_input——全文写入对话持久化污染检索/索引, 且超过 total_char_budget(20000) 时下一轮主历史被整体清空
- **入口**: ContentBlock 新增 `file` 类型 (`{"type":"file","file":{"filename","content"}}`), chat 路由提取为 document_datas (与 image_datas 平行, 队列合并累加); 视频暂不支持, pdf/docx 后续预处理为 md 复用同一入口
- **入库**: FileRepository.store_document — SHA-256 用户级去重 → `files/documents/` (首次启用) → `FileEntry(file_type="document")` → `.desc.md`=原文 (read_file 可读全文) → 配额检查
- **当轮注入**: `_build_human_message` 文档分支 — ≤4000 字符 (与 read_file 默认 max_chars 对齐) 内联为 `<document>` 块, 超出仅 `[file: id]` 标记 + read_file 提示; 持久化仅存标记, 不再污染主历史

## 沙盒模式任务面与 quick 门禁对齐: +配置治理 +E2E +任务级并行 (2026-08-15)
- **背景**: 沙盒模式目标为兼容 DSH/Codex 受限环境, 门禁与常规模式最大程度一致; 原跳过配置治理/E2E 且任务级串行为继承自 codex 时代的保守排除, 无失败实证
- **实测全部可行**: 配置治理沙盒内 0 警告; E2E 13/13 全绿 (本机 docker 容器可用, 旧"E2E 沙盒不可行"结论过时); mypy --num-workers=8 正常; 任务级并行 (静态分析+单元+集成同时跑) 无资源冲突
- **改动**: run_sandbox_tasks 任务清单 +配置治理 (硬门禁) +E2E, 顺序执行改 asyncio.gather 并行 (7 任务); 单元测试/静态分析子进程统一走线程池 (避免并行时阻塞 event loop); mypy 并行沙盒启用
- **现状**: 沙盒门禁 = 静态分析 (Ruff/MyPy/Bandit/配置治理) + 单元 3102 + 集成 103 + E2E 13 全部并行, 与 quick 门禁仅差 SimpleCleaner 归档与 TMPDIR 注入 (环境适配)
- **验证**: 沙盒内 --sandbox 7/7 全绿, 门禁总时长约 17s

## 沙盒模式恢复并行测试 (2026-08-15)
- **背景**: 沙盒模式沿用 codex 时代"沙盒资源隔离, 串行保稳定"的保守假设 (禁 pytest-xdist), 无并行失败实证
- **实测**: 沙盒内单元 `-n 6` 两次全绿 (3102/3102, 10.4s/9.3s vs 串行 18.7s), 集成 `-n 8` 全绿 (103/103, 8.9s vs 串行 14.6s); xdist worker 为 pytest 子进程, 天然继承 Landlock 限制, 机制上无冲突
- **改动**: 单元/集成并行度与常规模式一致 (6/8 worker); 集成测试沙盒分支并入统一 exit-3 崩溃重试逻辑 (并行后同样需要保护)
- **收益**: 沙盒门禁总时长约 36s → 约 26s, 与常规模式行为差异进一步缩小

## 沙盒测试失败根因定位: SQLite 临时目录 /var/tmp × Landlock 写白名单 (2026-08-15)
- **问题**: 沙盒 `workspace-write` 下 chroma 持久化测试必败, 报 `InternalError: error returned from database: (code: 14) unable to open database file`; 但 python sqlite3 打开同一文件正常, 一度被定性为"文件拦截层拒绝 rust sqlite"
- **根因链** (LD_PRELOAD 拦截 open 系统调用实证): ① chroma 1.5.9 默认 RustBindingsAPI (sqlx-sqlite) 启动 migration 触发 SQLite 内部临时文件 (`etilqs_*`, 排序/建索引用); ② SQLite 临时目录搜索顺序为 `SQLITE_TMPDIR → TMPDIR → /var/tmp → /usr/tmp → /tmp`, 会话 TMPDIR 未设置时首选 `/var/tmp`; ③ DSH Landlock profile 写白名单仅 `/dev/null` + `/tmp` + workspaceRoot, `/var/tmp` 的 `O_CREAT` 被拒 (EACCES); ④ SQLite 将临时文件创建失败映射为 `SQLITE_CANTOPEN (14)` 冒泡给调用方
- **普适性验证**: python sqlite3 (3.50.4) 大表 CREATE INDEX 触发临时文件时同样失败 (`OperationalError: unable to open database file`), 证明与 rust/python 无关, 任何 SQLite 触发临时文件即撞 `/var/tmp`; 之前"python 正常"只是未触发该路径
- **修复**: `--sandbox` 模式自动注入 `TMPDIR=/tmp` (SQLite 尊重该 env 跳过 `/var/tmp`), 单元/集成测试子进程均注入; 沙盒单元测试恢复**全量 3102** (移除 knowledge_base + semantic_cache 排除), 沙盒内实测全绿
- **DSH 沙盒机制备忘**: Linux 下 bwrap 优先、Landlock 兜底 (本环境); Landlock 为纯 allow-list, 只拦文件系统, 网络/进程/IPC 不受限; workspace-write = 全盘只读 + `/tmp`/workspace 可写; 升级需 approval 审批

## CI 沙盒模式统一为 --sandbox, 移除 --codex (2026-08-15)
- **背景**: DSH 沙盒 `workspace-write` 权限下 chromadb rust sqlite 可写打开被文件拦截层拒绝 (full-access 二分实验实证), 全量门禁无法在沙盒内跑绿
- **新增 `--sandbox`**: 串行/禁 xdist/跳过 Safety 与清理; 单元测试注入 `TMPDIR=/tmp` 后跑**全量 3102** (见上方根因条目), 沙盒内实测全绿
- **集成测试纳入沙盒**: 沙盒模式新增集成测试任务 (串行 -n 0), 实测 103/103 通过; 常规模式保持 -n 8 并行不变
- **移除 `--codex`**: 原 Codex 专用 smoke 子集 (单文件 13 用例) 覆盖过窄, 语义并入 `--sandbox`; 破坏性变更, Codex 环境改用 `--sandbox`
- **辅助加固**: scripts 增加 venv 守卫 (错误解释器自动切换) + 工具解析兜底 (PATH 缺失回退 `sys.executable -m`) + 命令失败响亮化 (mypy/bandit/vulture 不再假通过)
- **权限放宽实证**: 会话 full-access 下全量 `--quick` 门禁全绿 (3102/3102 + 集成 103 + E2E + 静态分析)

## 集成测试 xdist worker 崩溃加固 (2026-08-12)
- **问题**: quick 门禁偶发集成测试 `exit_code=3` (xdist worker 崩溃), 7 用例丢失且报告只落盘计数, 事后无法定位堆栈; 10 轮复现未重现, 判定为并发争抢下的基础设施级偶发故障
- **诊断改进**: 集成测试 pytest 完整输出落盘 `reports/current/integration_pytest_full.log` (仿 unit/e2e 已有机制)
- **流程加固**: `exit_code=3` (基础设施故障) 保留崩溃现场 (`integration_pytest_crash.log`) 后自动重试一次; 真实测试失败 (exit 1) 不重试, 重试后仍崩溃则 CI 继续阻断 (确定性崩溃不掩盖)

## CI quick 模式纳入 E2E 阻断门禁 (2026-07-17)
- **问题**: 7/2 重构 `export_document` 移除 `summary` 参数后, `tests/e2e/test_tool_runtime_container_e2e.py` 未同步更新, 导致 full 模式 E2E 失败; 因 quick 模式跳过 E2E, 回归被遗漏约两周
- **修复**: 同步更新两处 E2E 调用, 移除 `summary=None` 参数
- **流程加固**: quick 模式不再跳过 E2E, `run_test_suite.py` 默认任务列表加入 `run_e2e_tests`, 结果纳入 `ci_passed` 阻断门禁
- **数据隔离**: E2E 子进程注入 `TEST_PROCESS_PREFIX=e2e`, 避免与 unit/integration 共享 `test_data` 目录
- **诊断改进**: `_extract_error_type` 优先读取 `test_details["timeout"]` 显式标记, 避免 pytest-timeout 插件 header 里的 "timeout" 字样导致所有失败被误报为 `TIMEOUT_ERROR`; E2E pytest 完整输出落盘 `reports/current/e2e_pytest_full.log`

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