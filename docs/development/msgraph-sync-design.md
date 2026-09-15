# Microsoft Graph 同步子系统设计 (TODO + 日历)

> 状态: 设计已定稿, 分切片实施中. 授权通道背景见 homelab 仓库
> `docs/graph-msa.md` 与 `scripts/graph_*.py` (逻辑移植来源).

## 目标与非目标

把 assistant 内的用户级数据 (todo.db / calendar.db) 与用户**本人的** Microsoft
个人账户 (MSA) 的 To Do / Outlook 日历自动同步, 让手机端原生应用可见、可改
(TODO) 与只读镜像 (日历).

非目标: 联系人同步; Teams/共享日历; 组织账户 (Azure AD) 支持.

## 授权通道 (为什么这样做)

- 主通道为**自建 app registration「JFT Assistant」**(个人 Azure 目录, 与
  homelab 通道共用注册, 见 homelab `docs/graph-msa.md`) 的 device code flow,
  delegated `/me` 视角, 只能操作本人数据. client_id 属部署配置, 经
  `MS_GRAPH_CLIENT_ID` (runtime_env) 注入, **不硬编码进源码** (与 homelab
  「config 不进仓库」一致); 缺省回退微软第一方公共客户端
  `Microsoft Graph Command Line Tools` (client `14d82eec-...`, 非官方通道
  理论上可能被微软收紧) 作应急 fallback. 切换 client 后所有用户必须重新授权
  (token 绑定 client, 不可迁移).
- **与 homelab CLI 通道不共享 token**: 两条通道各自独立授权同一自建 app,
  token 文件各自持有 (`data/{user_id}/credentials/` vs mac `~/.graph-msa/`).
  refresh_token 每次刷新轮换, 两处共享同一 token 会互相打断 (homelab 明令
  禁止复制 token 文件).
- scope 首次授权即定全: `offline_access Tasks.ReadWrite Calendars.ReadWrite
  Contacts.ReadWrite` (delegated refresh **不能升级 scope**, 后加必须重走
  授权; 联系人同步仍是非目标, scope 仅授权面预留, 与 homelab 四件套对齐).
- MSA 无 application permission: 不能后台免登录, **不能建 change notification
  webhook** (需要 application permission), 远端变更感知只能轮询.
- refresh_token 90 天滑动有效且每次刷新轮换 → 引擎持续运行即自动续期,
  不需要独立 cron; 原子回写 (tmp + replace) 是硬要求, 半写即失效.

## 分用户隔离

- **每个用户独立走一次 device code flow**, 独立 token 文件:
  `data/{user_id}/credentials/graph_msa_tokens.json` (0600, 目录 0700).
- 不进 `credentials_registry` (那是进程级全局密钥的环境变量体系; 本件是
  用户级凭证, 随用户数据目录隔离与备份).
- 不进 SQLite (避免随 db 备份/导出扩散, 文件权限控制更直接).
- 解绑 = 删 token 文件 + 清映射表; 引擎扫描 `data/*/credentials/` 发现已
  授权用户 (同 `discover_price_alert_dbs` 模式).

## 同步语义

容器: 首次同步在远端创建**专用 To Do 清单**与**专用子日历** (默认名
`Assistant`, 可配), 之后只读写这两个容器, 不碰用户既有数据.
同步范围: 本地全量 (TODO 含各状态; 日历取 active 全量, 不设时间窗) —
个人数据量级下最简且映射稳定, 不做窗口边界 churn.

### 日历: 单向 push + 镜像修复 (本地是唯一权威)

决策: Outlook 侧**不允许**编辑 agent 维护的日历 —— 远端只是镜像.

| 变更 | 行为 |
|---|---|
| 本地新建/更新 | POST / PATCH 远端 (按映射表) |
| 本地软取消 (cancelled) / 删除 | 远端 DELETE |
| 远端被编辑 (lastModifiedDateTime 晚于上次推送) | PATCH 回本地内容 (revert) |
| 远端被删除 | 重新 POST (保持镜像) |
| 容器内出现非映射条目 | 跳过 + 告警日志 (不动用户手建数据) |
| 重复事件 (recurrence_rule 非空) | 暂不同步 (RRULE ↔ recurrence pattern 映射成本高, 后续单独切片) |

单向带来的简化: 无冲突合并 (本地写是唯一写源), 无远端→本地字段回映,
映射表只需 local→remote 单向 + 上次推送时间.

### TODO: 双向

- 映射表驱动 (不做标题模糊匹配), 冲突以 `lastModifiedTime` 新者为准.
- 状态映射: completed ↔ completed; 本地 cancelled 视为远端删除.
- `due_date` ↔ `dueDateTime` (时区取 `UserContext.timezone`).

### 字段换算约定

- 时间: 本地一律 aware UTC 存储; Graph 请求体带 `{dateTime, timeZone: 用户时区}`,
  响应用 `Prefer: outlook.timezone="{用户时区}"`.
- 全天事件: 本地 end 为 inclusive 结束日 → Graph `isAllDay` 语义为 start 当天
  00:00 (用户时区) / end 次日 00:00 (+1 天, 与 ics_builder 的 RFC 转换同构).
- 远端 id 是超长 base64, **只落映射表, 绝不手抄** (homelab 血泪教训).

## 引擎与触发

- `PriceAlertEngine` 同款: 全局单例, FastAPI lifespan 启停,
  `register_resource` 逆序关闭; 每 tick 扫描已授权用户, **逐用户串行**同步
  (Graph 限流友好, 429 指数退避).
- 触发 = 周期兜底 (默认 600s, 可配) + 本地写操作后投递 per-user 即时信号
  (只加速本地→远端方向; 远端→本地感知延迟 = 轮询间隔).
- 授权失效 (改密码/撤销授权): 该用户同步置 `auth_expired` 暂停, 经
  NotificationService 提醒重新授权, 不影响其他用户.

## 对外接口

- REST (受全局认证中间件保护, 身份取 request.state):
  - `POST /v1/sync/msgraph/authorize` — 发起 device flow, 返回
    verification_uri + user_code, 后台轮询授权结果; 已授权返回 409
  - `GET /v1/sync/msgraph/status` — 状态 none/pending/failed/authorized
    (pending 附验证信息, failed 附原因; 兼作授权结果轮询端点)
  - `DELETE /v1/sync/msgraph/authorize` — 解绑
  - `GET /v1/sync/msgraph/settings` — 查询 per-user 覆盖
    (todo_list_name / calendar_name / timezone / notify_delivery;
    未覆盖为 null, 生效值回退全局默认)
  - `PUT /v1/sync/msgraph/settings` — 更新覆盖; 字段缺省不动, 显式 null
    清除回退全局默认; timezone 做 IANA 校验
- Agent internal 工具 (`msgraph_sync_group`, 挂 personal_assistant
  optional_tools 休眠): `msgraph_connect` (在对话里引导授权, 幂等:
  已授权/进行中直接返回现状), `msgraph_sync_status` (授权状态 + 引擎最近
  一轮结果). 不走 MCP (MCP 工具全局共享, per-user token 注入在
  internal 体系是现成的).

## 配置

```yaml
calendar_sync:            # 全局默认, per-user 可覆盖
  enabled: true
  interval_seconds: 600
  todo_list_name: "Assistant"
  calendar_name: "Assistant"
```

client_id 为公共值 (非密钥), 部署配置: env `MS_GRAPH_CLIENT_ID` 为主通道
自建 app, 代码内默认值为第一方应急 fallback. 新增配置需过
`config_doctor --strict --check-env`.

## 实施切片 (TDD)

1. `msgraph_client` (device flow / refresh / 401 重试, httpx 异步) +
   `token_store` (原子写 0600) — ✅
2. 授权 REST + 状态机 (none → pending → authorized/failed, 后台轮询) — ✅
   (`POST/DELETE /v1/sync/msgraph/authorize`, `GET /v1/sync/msgraph/status`;
   per-user config 端点并入切片 ⑥)
3. 专用清单/日历 ensure + `sync_map` 映射表 (用户级 graph_sync.db: graph_sync_map + graph_sync_settings) — ✅
4. TODO 双向 diff 纯函数 (todo_sync: 换算/哈希/计划, cancelled→远端删除, 冲突以更新时间新者胜) — ✅; 引擎接线并入切片 5
5. 日历单向 push + 镜像修复 (calendar_sync 纯函数) + 引擎接线
   (`graph_sync_engine`: 单例/周期 tick/逐用户串行/分页取数/授权暂停恢复) — ✅
6. 写后即时信号 (服务层 notify_local_write 钩子) + per-user config
   (settings REST + 容器名/时区覆盖) + `config.yaml calendar_sync` 段 — ✅
7. internal 工具 (`msgraph_sync_group`: msgraph_connect / msgraph_sync_status) + 文档更新 — ✅
