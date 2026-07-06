# 工具系统设计规范

> 本文档定义工具元数据字段的设计契约与写作规范.
> 工具的 `name` / `description` / `args_schema` 等具体 schema 以源码为单一信源, 本文档不重复清单.
> 新增或修改工具描述前, 请先理解本文档的字段语义, 避免写成"无法被筛选消费"的死描述.

> 最后更新: 2026-06-30

---

## 目录

- [1. 字段语义契约](#1-字段语义契约)
- [2. 工具发现与消费链路](#2-工具发现与消费链路)
- [3. 写作规范](#3-写作规范)
- [4. 工具类型与分配机制](#4-工具类型与分配机制)

---

## 1. 字段语义契约

工具元数据并非"写给人读的注释", 而是被筛选系统按字段拆解消费的结构化信号. 每个字段都有明确的消费方与匹配权重, **写错字段 = 信号失效**. 具体权重数值见 `src/tools/internal/search_available_tools.py` 的 `_score`.

> **工具组成员约定**: 被编入 `tool_groups` 的子工具应**不定义 `summary`**; 其检索信号由工具组的 `summary` / `description` / `keywords` 统一提供. 子工具的 `description` 仅在工具被实际注入后, 作为主模型调用该工具时的能力说明.

### 1.1 工具字段

独立工具(继承 `BaseInternalTool` / `BaseExternalTool` / `BaseExpertTool`)暴露的字段:

| 字段 | 类型 | 消费方 | 用途 |
|------|------|--------|------|
| `name` | `str` | 全链路标识 | 工具唯一标识与名称匹配 |
| `summary` | `str` | `catalog.description` | 召回信号 + LLM 门面(主模型唯一可见的简短描述) |
| `description` | `str` | `catalog.full_description` + LLM 调用工具时 | 初筛子串信号 + 工具被注入后的完整说明 |
| `args_schema` | Pydantic `BaseModel` | 编译为 JSON Schema | 参数契约, 字段 `description` 直接进 Schema 喂给 LLM |
| `search_keywords` | `ClassVar[list[str]]` | `catalog.keywords` | 高频召回词, 覆盖同义词/中英文/动作词 |

### 1.2 工具组字段

工具组(`tool_groups` 配置)字段:

| 字段 | 消费方 | 用途 |
|------|--------|------|
| `summary` | `catalog.description` + search 工具描述注入 | **LLM 主模型唯一直接可见的组级文本**(门面) |
| `description` | `catalog.full_description` + 降噪 LLM | 降噪时仅前 3 行非空行被消费(见 §2.3) |
| `keywords` | `catalog.keywords` | 高频召回词 |
| `members` | 命中后整组注入 | LLM 只感知被注入的子工具, 组本身对主模型透明; 子工具不单独参与搜索评分 |
| `prompt_hint` | 注入系统提示词 | 规范写工具完成后的汇报行为(如"以 DB 真实状态为准") |

---

## 2. 工具发现与消费链路

休眠工具不进 LLM 初始工具列表, 由 `search_available_tools` 按需发现, `ToolDiscoveryMiddleware` 动态注入. 完整链路实现见源码, 本节只说明关键语义约束.

### 2.1 catalog 构建

`src/agent/processors/inference_coordinator.py`(组装备阶段)将休眠工具清单注入 `src/tools/internal/search_available_tools.py` 的检索目录:

- 工具组成员**跳过独立条目**, 改由组条目代表检索(一个组 = 一条 catalog 条目)
- **子工具的 `summary` / `description` 不进入 catalog**, 仅组级 `summary` / `description` / `keywords` 参与评分
- 字段映射:
  - **工具组**: `summary` → `catalog.description`, `description` → `catalog.full_description`
  - **独立工具**: `summary`(或 description 首行)→ `catalog.description`, `description` → `catalog.full_description`
- search 工具自身描述被覆盖: 追加 "当前可发现的工具:" 清单, 每项格式 `- {name}: {summary}`

### 2.2 评分机制

`search_available_tools.py` 的 `_score` 对每个查询 token 按优先级取最高信号(同 token 多信号**不叠加**, 取 max). 分值表、同义词扩展(`BUILTIN_SYNONYMS`)、token 命中率过滤阈值以源码为准.

### 2.3 LLM 降噪

`src/tools/internal/_llm_tool_filter.py`: 初筛命中 **≥2 候选**时, 调本地小模型去除无关工具. 降噪输入仅取 `description` 的前 3 行非空行.

**关键含义**: `description` 的**前 3 行是降噪判断的全部输入**, 第 4 行起对降噪 LLM 不可见(仅参与 §2.2 的初筛子串匹配, 权重较低).

### 2.4 LLM 门面

主对话模型通过 `search_available_tools` 的描述看到休眠工具/组, 该描述里每个条目只有 `- {name}: {summary}`.

- **主模型看不到组/工具的 `description`**, 只看 `summary`
- → `summary` 承担**双重职责**: 召回信号 + 门面(LLM 决策判断的唯一文本)
- 这要求 `summary` 既要高区分度词(召回), 又要传达核心定位(门面)

---

## 3. 写作规范

### 3.1 通用(所有工具)

**`summary`**:
- 一句话, 用户视角的能力词, 高区分度
- 避免 CRUD 泛称("支持创建/查看/更新/删除"对任何管理工具都成立, 零区分度)
- 反例: `TODO任务管理, 支持创建/查看/更新/删除`
- 正例: `待办任务管理, 记录并跟踪用户的事项进度`

**`description`**:
- 结构: 首句定位 → 参数说明(每行一个, 标必填/可选, 给示例值)→ 行为约束/示例
- 揭示易踩坑契约(交易时段、穿越去重、双线规则、凭证前置等), 减少 LLM 误导用户

**`args_schema` 字段描述**:
- 含示例值(如 `6位A股代码, 如 600519(贵州茅台)`)
- 标必填/可选及联动条件(如"email 投递时必填")

**`search_keywords`**:
- 覆盖用户可能说的同义词、中英文、动作词
- 高频召回词放这里, `summary` 留给能力定位

### 3.2 工具组专属(关键约束)

组描述只承担"筛选/唤醒"定位, 运行限制属子工具范畴.

- **`description` ≤ 3 行**: 降噪硬约束(§2.3), 第 4 行起对降噪不可见
- **前 2 行放最高区分度能力词**: 降噪 LLM 主要靠前 2 行判断相关性
- **`summary` 与 `description` 互补不冗余**: `description` 行 1 应补充 `summary` 没有的语义
- **只讲能力 + 子工具清单**: 不暴露内部实现(交易时段轮询、告警日限、刷屏防护等留给予子工具 `description`)
- **不刻意强调投递渠道**: 渠道是子工具的投递细节, 组级用"消息提醒"即可, 具体渠道(微信/邮件)放子工具描述, 并靠 `keywords` 兜底召回
- **`summary` 明确归属/定位**: 如待办工具标"用户的事项"(区分 agent 内部任务规划), 避免主模型误判用途

### 3.3 子工具专属

- **不定义 `summary`**: 子工具由工具组统一唤醒, 其检索信号来自组级字段, 自身 `summary` 不会进入 `search_available_tools`
- `description` 仅在工具被注入后, 作为主模型调用该子工具时的能力说明, **不参与 `search_available_tools` 评分**
- 副作用大的工具(create/update/delete 类)`description` 详尽: 参数 + 调用约束 + 易踩坑契约
- 只读工具(list/query 类)从简: 一句话能力 + 参数即可
- 描述复杂度应与副作用成正比

---

## 4. 工具类型与分配机制

> 本节只讲机制. 各 Agent 的具体工具分配(核心/休眠清单)以 `config.yaml` / `tools_config.py` 为单一信源.

### 4.1 三层架构

详见 `AGENTS.md`「工具系统」章节:

- **内部工具**: 配置化, 用户-线程-Agent 三级隔离
- **外部工具**: 无状态全局共享
- **专家工具**: 内部启动独立 Agent 编排多源工具
- **MCP 工具**: `McpBridge` 集成, 支持 streamable_http/sse/stdio

### 4.2 核心工具 vs 休眠工具

- **核心工具**: Agent 启动时直接加载, 出现在 LLM 初始工具列表
- **休眠工具**: 不在初始列表, 经 `search_available_tools` 发现后由 `ToolDiscoveryMiddleware` 动态注入

### 4.3 工具组

- **检索/激活单元**: 一个组在 catalog 里是一条条目, 命中后整组子工具注入
- **组对主模型透明**: LLM 实际调用的是组内子工具, 组本身只是唤醒入口
- **配置字段**: `summary` / `description` / `keywords` / `members` / `prompt_hint`(详见 §1.2)
