# Skills 接入设计文档

> **状态**: 已实施, 持续演进
> **版本**: v2.1 (复用 tool-runtime 作为 skill 执行运行时)
> **日期**: 2026-07-14

## 1. 背景与动机

### 1.1 工具无法封装"工作流 + 领域知识"

现有工具系统(内部工具 / 外部工具 / MCP / 专家工具)能封装**能力**(调用 API、生成文件、检索记忆), 但无法封装**领域知识 + 工作流**. 例如"生成专业 Excel 报表"不只是"调用 openpyxl", 还包括: 金融模型的颜色规范、数字格式约定、公式构造规则(用公式而非硬编码值)、交付前的零错误验证清单——这些知识无法塞进单个工具的 `description`.

### 1.2 为每个领域新建 agent 导致 agent 爆炸

领域知识的另一个解法是为每个领域新建 `agent.yaml` + 新 agentID. 但这导致:

- 用户被迫在多个 agent 间切换
- 当前**无跨 agent 知识共享**的计划
- 冗余低效

### 1.3 解法: skill = 在同一 agent 内注入"领域知识 + 定向能力"

skill 让同一个 agent 按需获得某领域的**知识(skills 段提示词)+ 定向能力(系统目前没有的新工具)**, 避免 agent 碎片化. 这是引入 skill 的根本动机.

## 2. 核心概念

### 2.1 skill 定义

一个 skill 是一个自包含的能力包, 经**审核适配**后接入. 落地为以下声明:

| 维度 | 内容 |
|------|------|
| 领域知识 | L1 清单(系统提示词) + L2 总览(load_skill 返回) + L3 引用(references/ 按需) |
| 关联工具(可选) | `associated_tools` 配置, load_skill 激活时注入的已有工具(executor 或外部工具) |
| 执行后端 | `prompt_only`(纯知识) 或 `executable`(需运行时), 仅元信息 |
| 注入策略 | 三级渐进式披露: L1 始终可见 → L2 按需 → L3 按需; 关联工具随 L2 注入 |

### 2.2 与 MCP 的关系: 平级外部能力源

skill 与 MCP **不是包含关系**, 而是**平级的外部能力源**, 各自适配后接入 `agent.yaml`:

```
外部能力源
├── MCP 服务 (mcp_servers 配置) → McpBridge 桥接 → 被动协议调用
└── Skills (skills 配置)       → SkillBridge 桥接 → 知识注入 + 关联工具注入
```

SkillBridge 与 McpBridge 平级, 复用其管理框架(配置驱动注册 / 懒加载), 但执行模型不同(MCP 是被动协议调用, skill 是知识注入 + 关联工具).

### 2.3 后端类型

- **`prompt_only`**: 纯知识注入, 可选携带关联工具(如 chart_maker 携带 3 个渲染工具). 零运行时基础设施.
- **`executable`**: 带代码执行能力, 需 tool-runtime 作为运行时. 通过 `associated_tools: ["skill_executor"]` 声明.

> 后端类型仅作元信息, 不决定工具注入逻辑. 所有 skill 统一通过 `associated_tools` 声明关联工具, SkillLoadMiddleware 按 per-skill 映射注入.

### 2.4 三级渐进式披露 (对齐 Anthropic Agent Skills)

| 层级 | 何时加载 | 内容 | 注入路径 |
|------|---------|------|---------|
| L1 | 启动时常驻 | frontmatter name + description (~100 tokens) | 系统提示词 skills 段 |
| L2 | load_skill 触发 | SKILL.md 正文: 概览 + 选型 + 引用索引 (<5k tokens) | load_skill(skill_name) 返回 |
| L3 | 按需 | references/xxx.md: 单引擎/子主题完整知识 | load_skill(skill_name, reference="xxx") 返回 |

关联工具注入: load_skill(L2) 首次调用触发 SkillLoadMiddleware 注入该 skill 的 associated_tools. L3 调用不重复注入.

### 2.5 审核适配原则

每个 skill 接入前经人工审核, 甚至重写. 因此:

- 运行时**不需要通用 harness**(不需要支持任意野生 skill)
- 依赖**离线人工准备**(评估依赖 → 改镜像 → 重建), 非运行时动态安装
- SKILL.md 解析器可**宽松**(只处理审核过的, 多余字段忽略)
- **不开放用户自定义 skill**(初期), skill 是开发者层面的配置

## 3. 运行时架构

### 3.1 复用 tool-runtime, 不新建独立镜像

`executable` skill 需要一个"跑 LLM 代码 + 有文件区 + 重依赖(LibreOffice/openpyxl)"的运行时. 本设计**不新建独立镜像**, 而是**复用现有 tool-runtime**:

- tool-runtime 本就是为"工具的重依赖"建的: 已装 pandoc + Playwright Chromium + LibreOffice + 中文字体, 有 `/workspace` 可写卷, internal 网络 + read_only + 非 root
- skill 代码执行(如 openpyxl 生成 xlsx + 调 recalc.py 重算)与 tool-runtime 已有的渲染任务(pandoc/Chromium)**同性质**(都是"LLM/工具代码 + 重依赖 + 产物")
- 只需在 tool-runtime 加一个 `/execute` 端点 + `COPY skills/` + 装 openpyxl, **零新镜像**

**收益**: 少一个镜像(构建/部署/网络/资源隔离单元)、运行时统一(工具的重依赖集中)、架构更简.

### 3.2 python_executor 已合并到 tool-runtime

`python_executor` 工具原指向独立的 `python-executor` 容器(纯计算严格沙箱). 评估后认定: tool-runtime 的 `/execute` 端点已预装 numpy/pandas, 响应结构与 python-executor 完全兼容, 可覆盖纯计算场景, 故**废弃 python-executor 独立容器**, `python_executor` 工具改为调用 tool-runtime `/execute`(与 `skill_executor` 共用同一运行时, `base_url` 统一由 `TOOL_RUNTIME_BASE_URL` 环境变量配置).

两个工具指向同一后端, 但契约与可见性不同, 各司其职:

| 工具 | 用途 | 可见性 | 产物 |
|------|------|--------|------|
| `python_executor` | 纯计算(数值/统计/数据处理), `collect_outputs=False` 不回收文件 | **search 发现**(optional_tools) | 仅 stdout 文本 |
| `skill_executor` | skill 代码 + 文件生成(LibreOffice/openpyxl 重算) | **load_skill 激活后动态注入** | `/workspace/output/` 回收为 file_id |

收益: 少一个镜像(构建/部署/资源隔离单元), 两个代码执行入口共用同一运行时与配置源. 代价: 纯计算也走 4g/2cpu 的 tool-runtime(容器闲时几乎不耗资源, 内存按需分配), 且丢失 scipy(科学计算/曲线拟合需求应做成 skill 注入领域知识).

## 4. 工具模型与可见性(关键设计)

两个代码执行入口对应两个工具, **可见性不同**, 因此不冲突:

| 执行器 | 对应工具 | 可见性 | 能力 |
|--------|---------|--------|------|
| tool-runtime | `python_executor` | **search 发现**(optional_tools + ToolDiscoveryMiddleware), 通用 | 纯计算, 无文件(`collect_outputs=False`) |
| tool-runtime | `skill_executor` | **LLM 调用 load_skill(xxx) 后, SkillLoadMiddleware 动态注入** | 跑代码 + 文件区 + 重依赖 + `/skills/` 脚本 |

### 4.1 可见性区分(渐进式披露)

skill 执行器**不是配了 skill 就始终可见**, 而是按需加载:

- **构建期**: 只注入 L1 清单(skill 名称 + 一句话描述)到 skills 段, 让 LLM 知道有哪些 skill 可用
- **运行期**: LLM 判断需要某 skill 时, 调用常驻的 `load_skill` 工具
- **加载后**: 中间件动态注入 L2 正文(作为 load_skill 返回值)+ skill 执行器工具

### 4.2 skill 加载机制(load_skill + SkillLoadMiddleware)

复刻现有 `ToolDiscoveryMiddleware`(`src/tools/middleware/_tool_discovery.py`)的同构模式——search_available_tools 发现休眠工具 ↔ load_skill 加载 skill:

| ToolDiscoveryMiddleware | SkillLoadMiddleware |
|---|---|
| dormant_tools(休眠工具池) | skill 关联工具池(per-skill 映射) |
| search_available_tools(常驻) | load_skill(常驻) |
| 返回 matched_tools 描述 | 返回 L2 正文(完整领域知识) |
| awrap_model_call 扫描 ToolMessage 激活 | 同 |
| awrap_tool_call 路由动态工具 | 同 |

`SkillLoadMiddleware`(`src/tools/middleware/_skill_load.py`)复刻 `ToolDiscoveryMiddleware` 结构:

1. `awrap_model_call`: 扫描消息历史中 `load_skill` 的 ToolMessage, 激活对应 skill, 通过 `request.override(tools=...)` 注入 skill 执行器工具
2. `awrap_tool_call`: 路由 skill 执行器工具调用到正确实例(LangChain factory 要求中间件实现此方法才能合法注入动态工具)
3. `load_skill` 工具返回 L2 正文 → LLM 在 ToolMessage 读到完整领域知识

这实现了真正的渐进式披露: L1 清单常驻(轻量, 不撑爆提示词)→ load_skill 按需 → L2 知识 + 执行工具加载.

## 5. SkillBridge 框架

### 5.1 配置驱动(仿 McpBridge)

```yaml
# config.yaml
skills:                          # 平行于 mcp_servers
  xlsx:
    name: "xlsx"
    source: ./skills/xlsx/       # SKILL.md + scripts 目录
    backend: executable          # prompt_only | executable
    associated_tools: ["skill_executor"]
    enabled: true
  chart_maker:
    name: "chart_maker"
    source: ./skills/chart_maker/
    backend: prompt_only
    associated_tools: ["mermaid_chart", "vega_chart", "markmap_chart"]
    enabled: true
```

```yaml
# agent.yaml (每个 agent 引用要启用哪些)
skills: [xlsx, chart_maker]      # 仿 tools/optional_tools 模式
```

### 5.2 组件结构

```
src/tools/skills/
  skill_bridge.py        # 主桥接器(仿 McpBridge): 懒加载 + L1 清单贡献 + skill 池管理
  skill_parser.py        # SKILL.md 解析(L1 frontmatter + L2 正文 + L3 references/扫描)
  load_skill_tool.py     # 常驻工具: LLM 调用激活 skill, 返回 L2 正文 / L3 引用
  skill_executor_tool.py # executable skill 的代码执行工具(指向 tool-runtime /execute)

src/tools/middleware/
  _skill_load.py         # SkillLoadMiddleware(仿 ToolDiscoveryMiddleware): 动态注入关联工具
```

### 5.3 运行时职责(渐进式披露)

SkillBridge 运行时职责:

1. **构建期解析 SKILL.md** → 提取 L1(name + description)+ L2(完整正文)
2. **贡献 skills 段(L1 清单)** → 拼入系统提示词, 让 LLM 知道有哪些 skill 可用
3. **提供 `load_skill` 工具(常驻)**: LLM 调用激活某 skill, 返回其 L2 正文或 L3 引用
4. **`SkillLoadMiddleware` 动态注入**: load_skill 调用后, 注入对应 skill 的关联工具(executable 类型)

`load_skill` 调用后: LLM 在 ToolMessage 读到 L2 完整知识 + 中间件注入关联工具.

### 5.4 skills 段注入路径(L1 清单)

skills 段只承载 **L1 清单**(skill 名称 + 一句话描述), **不含 L2 正文**(正文走 load_skill 按需返回). 这保持 skills 段轻量, 避免所有 skill 全量正文撑爆系统提示词, 也保证 skills 段可作为稳定的缓存前缀.

`SystemPromptAssembler.SYSTEM_PROMPT_SECTION_ORDER = ("base", "tools", "skills", "memory")`(`src/agent/processors/system_prompt_assembler.py:10-15`).

注入点: `InferenceCoordinator.process_with_agent()` 的 `sections` dict 加入 `"skills"` 键(`src/agent/processors/inference_coordinator.py:675`). skills 段排在 memory 段之前, 不影响 memory 段的动态数据.

## 6. 运行时: 复用 tool-runtime

### 6.1 tool-runtime 的 /execute 端点

tool-runtime(`docker/tool-runtime/app.py`)提供五个 HTTP 端点, 其中 `/execute` 服务于 skill 代码执行:

| 端点 | 用途 |
|------|------|
| `/health` | 健康检查 |
| `/execute` | **skill 代码执行**(工作目录 `/workspace`, 可调 `/skills/` 预装脚本, 扫描 `/workspace/output/` 回收产物) |
| `/render/pdf` `/render/png` | HTML → PDF/PNG(Chromium) |
| `/render/chart` | 图表渲染(内部构建 HTML + vendored JS) |
| `/convert/pandoc` | Markdown → DOCX |

`/execute` 请求/响应(`docker/tool-runtime/app.py:46-75`):

```python
class ExecuteRequest(BaseModel):
    code: str                    # LLM 临场代码(如 openpyxl 生成 xlsx)
    stdin: str = ""
    timeout_seconds: float = 30.0
    collect_outputs: bool = True # 是否扫描 /workspace/output/ 回收产物

class CreatedFile(BaseModel):
    filename: str
    relative_path: str           # 相对 /workspace/output/ 的路径
    size_bytes: int
    content_b64: str             # base64 编码的文件内容(app 侧解码后注册为 file_id)

class ExecuteResponse(BaseModel):
    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    created_files: list[CreatedFile] = []
```

执行流程(`app.py:144-197`):

1. 执行前 `_clean_output_dir()` 重置产物区(无状态)
2. 代码写入 `/workspace/.exec_<pid>_<ts>.py`
3. `subprocess.Popen(["python", "-I", script])`, 工作目录 `/workspace`
4. 代码可写 `/workspace/output/`(如 `wb.save("/workspace/output/sales.xlsx")`)
5. 代码可调 `/skills/` 下预装脚本(如 `subprocess.run(["python", "/skills/xlsx/scripts/recalc.py", ...])`)
6. 执行后 `_collect_outputs()` 扫描 `/workspace/output/`, 产物 base64 编码回传
7. finally 清理临时脚本

单文件上限 `MAX_FILE_BYTES = 50MB`(`app.py:40`).

### 6.2 skill bundle 预装

tool-runtime Dockerfile(`docker/tool-runtime/Dockerfile`)在镜像构建期预装 skill 依赖 + bundle:

```dockerfile
RUN pip install "openpyxl>=3.1,<4" "numpy>=2.0,<3" "pandas>=2.2,<3"   # skill 代码依赖
RUN apt-get install ... libreoffice-calc fonts-noto-cjk ...            # 公式重算 + 中文字体

COPY skills/ /skills/          # skill bundle(SKILL.md + scripts/)预装到 /skills/
RUN mkdir -p /workspace/output && chown -R toolrt:toolrt /app /workspace /skills
```

审核新 skill 时按需扩展 Dockerfile 的依赖层(装包), 无运行时动态安装.

### 6.3 产物回收 → file_id(app 侧)

tool-runtime **不碰数据库**(internal 网络, 无 DB 访问). 产物回收闭环在 app 侧(`src/tools/skills/skill_executor_tool.py:158-213`):

```
app 收到 ExecuteResponse.created_files
  → 解码 base64 → 落盘到 {data}/{user_id}/{thread_id}/shared/files/exports/
  → register_tool_output() (src/tools/shared/file_output.py)
  → 写 FileRegistry DB + 生成 HMAC 签名 URL
  → 追加到 ctx.exported_files
  → 用户收到 [附件: file_id] + 下载链接
```

**完全复用现有真相源**, 零新增存储架构.

### 6.4 可写工作区隔离

tool-runtime 根系统 `read_only: true`, 仅放开:

- `tool-runtime-workspace` 卷 → `/workspace`(可写, 产物区 + 临场脚本)
- tmpfs `/tmp` 512m noexec(LibreOffice/Chromium 配置区 + 运行临时文件)

每次 `/execute` 前后清理产物区, 无状态.

## 7. 配置与网络

### 7.1 开发环境(localhost 调用)

开发环境**直接用 python 启动主服务**(非容器), tool-runtime 容器映射端口到 localhost:

```yaml
# 开发环境: python_executor 与 skill_executor 均通过环境变量 TOOL_RUNTIME_BASE_URL 解析(默认 http://127.0.0.1:8766)
```

```yaml
# docker/tool-runtime/docker-compose.yml (开发用)
services:
  tool-runtime:
    ports:
      - "127.0.0.1:8766:8766"   # 仅本地访问
```

### 7.2 生产环境(容器间通信)

生产环境 app 与 tool-runtime 同处 `executor-internal` 内部网络, app 通过容器名访问:

```yaml
# docker/docker-compose.production.yml - app 服务的 environment
- TOOL_RUNTIME_BASE_URL=http://tool-runtime:8766

# tool-runtime 服务
tool-runtime:
  build:
    context: ..
    dockerfile: docker/tool-runtime/Dockerfile
  image: assistant-tool-runtime:${VERSION:-latest}
  user: "10002:10002"
  read_only: true
  cap_drop: [ALL]
  security_opt: [no-new-privileges:true]
  pids_limit: 384
  mem_limit: 4g
  cpus: 2.0
  networks:
    - executor-internal
  volumes:
    - tool-runtime-workspace:/workspace
  tmpfs:
    - /tmp:rw,noexec,nosuid,nodev,size=512m
```

### 7.3 配置模式对齐

`SkillExecutorTool` 的 `base_url` 通过环境变量 `TOOL_RUNTIME_BASE_URL` 配置(默认 `http://127.0.0.1:8766`), 与渲染工具共用同一 tool-runtime 服务, 开发/生产各自维护.

## 8. SKILL.md 规范(对齐 Anthropic Agent Skills)

参考 [Anthropic Agent Skills 规范](https://agentskills.io/specification), SKILL.md 极简:

```yaml
---
name: xlsx                      # 必需, 唯一标识(小写 + 连字符, 须与父目录名一致)
description: "何时触发..."       # 必需, 触发条件描述
license: ...                    # 可选
---

# markdown 正文(领域知识 + 代码示例 + 脚本调用说明)
```

目录结构(规范规定的可选目录, 项目均使用):

```
skill-name/
├── SKILL.md          # 必需: 元数据 + 正文
├── references/       # 可选: L3 引用文档(单引擎/子主题详细知识)
├── scripts/          # 可选: 可执行代码
└── assets/           # 可选: 模板/资源
```

### 8.1 skill_parser.py 职责

- 解析 YAML frontmatter(`name` / `description`)
- 提取 markdown 正文(L2 领域知识)
- 扫描 `references/` 目录, 收集可用 L3 引用文档名列表
- **容错**: 多余字段忽略, 格式异常降级处理(审核过的 skill, 不需要鲁棒解析)

### 8.2 现有 skill 目录结构

```
skills/xlsx/                 # executable
  SKILL.md                   # 适配版领域知识(金融模型规范 + 公式规则)
  scripts/
    recalc.py                # 公式重算 + 零错误验证(用 LibreOffice)
    office/soffice.py        # recalc.py 的依赖

skills/chart_maker/          # prompt_only
  SKILL.md                   # 引擎选型 + 通用参数 + 陷阱
  references/
    mermaid.md               # L3: mermaid 完整语法
    vega_lite.md             # L3: Vega-Lite 完整语法
    markmap.md               # L3: markmap 完整语法
```

## 9. 案例: xlsx

### 9.1 为什么选 xlsx

- **领域知识最丰富**: ~200 行 Excel 专业规范(金融模型颜色 / 数字格式 / 公式规则 / 验证清单), 最能验证 skills 段注入价值
- **系统目前没有的能力**: export_document 只导出 PDF/DOCX, 无 Excel 生成
- **生成型, 适配网关**: 用户文本描述 → 生成 .xlsx → file_id 下载(网关无文档上传, 纯生成场景)
- **典型"领域知识 + python 脚本"**: 知识(openpyxl 用法) + 脚本(recalc.py 重算验证)

### 9.2 适配方案(审核重写)

- **保留**: 输出规范(字体 / 零公式错误)、公式构造规则(用公式不要硬编码值)、openpyxl 代码示例、创建新文件工作流
- **砍掉**: 读取/编辑已有文件(网关不支持文档上传)、pandas 读取已有文件
- **脚本**: 用原版 `scripts/recalc.py` + `scripts/office/soffice.py`(装了 LibreOffice, 完整零错误验证), 不重写

### 9.3 完整数据流

```
用户(微信/OpenClaw): "帮我做个Q3销售数据表, 含同比公式"
  ↓
personal-assistant (agent.yaml 配了 skills: [xlsx])
  ↓ [构建期: skills 段注入 xlsx 的 L1 清单(名称+描述); load_skill 工具常驻]
LLM 读 L1 清单, 判断需要 xlsx → 调用 load_skill("xlsx")
  ↓ [load_skill 返回 xlsx 的 L2 正文(公式规则/格式规范/openpyxl 示例)]
  ↓ [SkillLoadMiddleware 检测到 load_skill 调用 → 动态注入 skill_executor 工具]
LLM 读 L2 知识 → 调 skill_executor 写 openpyxl 代码
  ↓
tool-runtime /execute: 跑 openpyxl 代码 → /workspace/output/sales_q3.xlsx
  ↓ (代码内调 /skills/xlsx/scripts/recalc.py 重算 + 验证零错误)
  ↓ [执行结束, _collect_outputs() 扫描 /workspace/output/]
ExecuteResponse.created_files = [sales_q3.xlsx]
  ↓
app (SkillExecutorTool._register_outputs): 解码 base64 → register_tool_output() → file_id → HMAC URL
  ↓
用户: 收到 [附件: a1b2c3d4] + 下载链接
```

一次验证: 渐进式披露(L1→load_skill→L2+执行器)✓ | tool-runtime 代码执行 + 文件 + 依赖 ✓ | 预装脚本调用 ✓ | 附件体系复用 ✓

## 10. 已落地状态

### 线 A — tool-runtime 扩展(复用, 不新建镜像)

| # | 工作项 | 落点 |
|---|--------|------|
| A1 | tool-runtime Dockerfile 加 openpyxl + libreoffice-calc + `COPY skills/ /skills/` | `docker/tool-runtime/Dockerfile` |
| A2 | tool-runtime app.py 加 `/execute` 端点(工作目录 /workspace + 产物回收扫描) | `docker/tool-runtime/app.py:144` |
| A3 | production compose 的 tool-runtime 服务挂 `/workspace` 可写卷 + healthcheck | `docker/docker-compose.production.yml:109` |
| A4 | SkillExecutorTool: load_skill 后动态注入, 产物 → `register_tool_output` → file_id | `src/tools/skills/skill_executor_tool.py` |

### 线 B — SkillBridge 框架

| # | 工作项 | 落点 |
|---|--------|------|
| B1 | `SkillConfig` 模型(prompt_only / executable / associated_tools) | `src/config/tools_config.py` |
| B2 | `config.yaml` 加 `skills` 段 + `agent.yaml` 加 `skills: [...]` 引用 | `config.yaml:436` + 各 agent.yaml |
| B3 | `skill_parser.py`(L1 frontmatter + L2 正文 + L3 references 扫描, 容错) | `src/tools/skills/skill_parser.py` |
| B4 | `skill_bridge.py`: 解析 + L1 清单贡献 + L2/L3 返回 + skill 池管理 | `src/tools/skills/skill_bridge.py` |
| B5 | `load_skill_tool.py`: 常驻工具, 返回 L2 正文 / L3 引用 | `src/tools/skills/load_skill_tool.py` |
| B6 | `_skill_load.py`(SkillLoadMiddleware, 仿 ToolDiscoveryMiddleware): 动态注入关联工具 | `src/tools/middleware/_skill_load.py` |
| B7 | skills 段(L1)注入 + SkillLoadMiddleware 装配 | `inference_coordinator.py:244,254,675` |

### 线 C — skill 内容

| # | 工作项 |
|---|--------|
| C1 | xlsx(executable): 适配精简(聚焦生成, 砍读取已有文件) + 完整 scripts/(recalc.py + office/) |
| C2 | chart_maker(prompt_only): 引擎选型指南 + 3 份 L3 references(mermaid/vega_lite/markmap) |

### 线 D — python-executor: 已合并到 tool-runtime(见 §3.2)

`python_executor` 工具改调 tool-runtime `/execute`, 废弃独立 python-executor 容器. **全部已落地**, 单元测试覆盖 5 个测试文件(parser / bridge / load_tool / executor / middleware), 55 passed.

## 11. 安全边界

### 11.1 tool-runtime 安全模型

| 维度 | 决定 | 理由 |
|------|------|------|
| 网络 | **保留** `internal: true`(executor-internal) | skill 代码/LLM 代码/渲染引擎不外联 |
| 用户 | **保留** 非 root(toolrt, uid 10002) | |
| 根系统 | **保留** read_only, **只放开** `tool-runtime-workspace` 可写卷 | 可写面收窄到 `/workspace` |
| noexec | **保留** tmpfs noexec | 允许 `python script.py`(解释器执行), 禁跑下载的二进制 |
| 内存 | 4g | LibreOffice + Chromium + 生成任务 |
| tmpfs | 512m | LibreOffice/Chromium 配置 + 运行临时文件 |
| pids | 384 | LibreOffice/Chromium 可能开多进程 |

### 11.2 LLM 临场代码的风险

tool-runtime 跑 LLM 临场代码(skill_executor 的 openpyxl 生成等, 以及 python_executor 的纯计算), 有文件区 + 重依赖. 风险缓解:

- **无外网**(internal 网络): LLM 代码无法外发数据
- **非 root + read_only**: 无法破坏宿主
- **资源限制**: 4g/2cpu/384 pids, 防资源耗尽
- **产物回收受控**: 只有 `/workspace/output/` 被回收注册, LLM 无法直接写宿主其他位置

## 12. 风险与边界

### 12.1 已知限制

- **文档摄入类 skill 受限**: OpenClaw 网关只支持图文输入, 无文档上传通道. 因此"读取/编辑用户上传的 PDF/Excel"类 skill 无法工作. skill 的输入只能是命令参数(文本/数据), **纯生成型**.
- **图片输入待定**: 用户能传图(file_id 体系内), 但当前 xlsx/chart_maker 案例不涉及. 图片处理类 skill 的 file_id → 执行器传递留作后续.
- **tmpfs 512m 须实测**: LibreOffice headless + Chromium 同时启动可能吃紧, 不够再扩.

### 12.2 LibreOffice 包选择

tool-runtime 当前装 `libreoffice-calc`(精简, 满足 xlsx recalc). 若预期接入 docx(Writer)/pptx(Impress) skill, 一步到位换完整 `libreoffice`, Dockerfile 改一行即可.

## 13. 后续演进

### 13.1 tool-runtime 已统一承载渲染 + skill 执行

原 v2.0 设想的"渲染工具迁移到独立镜像"已通过另一路径实现: tool-runtime 现同时承载图表渲染(`chart_maker_group` 工具的 `/render/chart`)、文档导出(pandoc `/convert`)与 skill 代码执行(`/execute`). 工具的重依赖集中在一个运行时, app 容器保持轻量.

### 13.2 skill 能力扩展

- **专用 named tool**: 复杂 skill 可声明专用工具(借 `McpBridge._schema_to_pydantic`), 在 L2 正文里告知 LLM, 通过 SkillLoadMiddleware 随执行器一起注入. 当前 xlsx/chart_maker 不需要(直接用 skill_executor 写代码 / 用已有渲染工具).
- **docx / pptx skill**: 复用现有基础设施, 装对应 LibreOffice 组件 + 适配 SKILL.md.
- **多 skill 组合**: 同一 agent 配多个 executable skill, 各自 load_skill 激活, 多个执行器工具注入.

### 13.3 python-executor 已合并(已完成)

python-executor 独立容器已废弃, `python_executor` 工具改调 tool-runtime `/execute`(见 §3.2). 评估结论: tool-runtime `/execute` 响应结构兼容、已预装 numpy/pandas, 覆盖纯计算场景; scipy 随之移除(科学计算需求应做成 skill 注入领域知识). 少一个镜像, 两个执行入口共用同一运行时.

---

## 附录 A: 关键代码位置索引

| 文件 | 角色 |
|------|------|
| `src/agent/processors/system_prompt_assembler.py:10-15` | skills 段顺序登记(SYSTEM_PROMPT_SECTION_ORDER) |
| `src/agent/processors/inference_coordinator.py:182,244` | skills 段 L1 清单 + SkillLoadMiddleware 装配 |
| `src/agent/processors/inference_coordinator.py:675` | skills 段注入点(sections dict) |
| `src/agent/processors/inference_coordinator.py:457-475` | per-skill 关联工具映射构建 |
| `src/tools/mcp/mcp_tool_manager.py` | McpBridge(管理框架借鉴源) |
| `src/tools/middleware/_tool_discovery.py` | ToolDiscoveryMiddleware(SkillLoadMiddleware 核心参考, 同构模式) |
| `src/tools/middleware/_skill_load.py` | SkillLoadMiddleware(动态注入关联工具) |
| `src/tools/skills/skill_bridge.py` | SkillBridge(L1/L2/L3 + 关联工具名查询) |
| `src/tools/skills/skill_parser.py` | SKILL.md 解析(frontmatter + 正文 + references 扫描) |
| `src/tools/skills/load_skill_tool.py` | LoadSkillTool(常驻, L2/L3 返回) |
| `src/tools/skills/skill_executor_tool.py` | SkillExecutorTool(executable, 调 tool-runtime /execute + 产物回收) |
| `src/tools/shared/file_output.py` | `register_tool_output`(产物回收复用) |
| `src/core/context.py` | UserContext(exported_files 透传) |
| `src/config/tools_config.py` | SkillConfig |
| `src/config/agent_config.py` | AgentConfig(skills 字段) |
| `docker/tool-runtime/app.py:144` | `/execute` 端点(skill 代码执行) |
| `docker/tool-runtime/Dockerfile` | tool-runtime 镜像(openpyxl + libreoffice-calc + COPY skills/) |
| `docker/docker-compose.production.yml:109` | tool-runtime 生产服务(网络/资源/卷) |
| `config.yaml:436` | skills 配置段 |
| `skills/xlsx/` | xlsx skill(executable) |
| `skills/chart_maker/` | chart_maker skill(prompt_only + 3 references) |
