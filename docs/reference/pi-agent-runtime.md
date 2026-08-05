# Pi Agent 运行时参考

> 来源: https://github.com/earendil-works/pi (`packages/agent`)
>
> npm 包: `@earendil-works/pi-agent-core`
>
> 完整源码副本: [pi-agent-src/](pi-agent-src/) (`.ref` 后缀, 3 个文件共 1802 行)
>
> Pi 是 OpenClaw (原 OpenCode) 的底层 agent 运行时. 多家企业用其构建 coding agent,
> 替代 Codex / Claude Code 等重型 harness.

---

## 架构总览: 两层设计

```
┌─────────────────────────────────────────────────┐
│  Agent (agent.ts) — 有状态运行时                  │
│  · 持有 transcript / tools / model              │
│  · 生命周期管理 (abort / waitForIdle / reset)    │
│  · 事件分发 (subscribe → listeners)             │
│  · 消息队列 (steering / follow-up)              │
│  · 每次 prompt/continue 调一次低层循环            │
└──────────────────────┬──────────────────────────┘
                       │ runAgentLoop(context, config, emit, signal)
┌──────────────────────▼──────────────────────────┐
│  runLoop (agent-loop.ts) — 无状态纯函数           │
│  · while 循环: LLM → tools → LLM → ...         │
│  · 不持有任何状态, 所有状态由参数传入              │
│  · 通过 emit 回调向外报告事件                     │
└─────────────────────────────────────────────────┘
```

**与 LangChain 的根本区别**: LangGraph 把控制流编码为数据 (图拓扑), 由运行时解释执行.
Pi 把控制流写成代码 (while 循环), 没有解释器, 没有图, 没有 channel/reducer.

---

## 核心循环 (agent-loop.ref, runLoop 函数)

```
outer while(true)                          ← follow-up 消息驱动
│
├─ inner while(hasMoreToolCalls || pending)  ← 工具调用 + steering 驱动
│   │
│   ├─ 1. 注入 pending steering messages
│   ├─ 2. streamAssistantResponse()         ← 调 LLM, 流式返回
│   ├─ 3. 提取 tool_calls
│   │      ├─ stopReason="length" → failToolCallsFromTruncatedMessage()
│   │      └─ 正常 → executeToolCalls()
│   ├─ 4. emit turn_end
│   ├─ 5. prepareNextTurn()                 ← 可换 model/context/thinking
│   ├─ 6. shouldStopAfterTurn()             ← 可提前终止
│   └─ 7. getSteeringMessages()             ← 有则继续内循环
│
├─ getFollowUpMessages()                    ← 有则继续外循环
└─ break                                    ← 无消息, 退出
```

### 退出条件

agent 停止 = 以下全部满足:
- AIMessage 无 tool_calls
- 无 pending steering messages
- 无 follow-up messages
- `shouldStopAfterTurn()` 未返回 true
- 工具批次未全部设置 `terminate: true`

### 工具执行 (executeToolCalls)

两种模式, per-tool 可覆盖:

```typescript
// parallel (默认): 准备阶段顺序, 执行阶段并发
const orderedResults = await Promise.all(
  finalizedCalls.map(entry => typeof entry === "function" ? entry() : entry)
);

// sequential: 逐个执行, 一个完成再下一个
for (const toolCall of toolCalls) { ... }
```

触发 sequential 的条件:
- `config.toolExecution === "sequential"`
- 或任一工具的 `executionMode === "sequential"`

### 工具执行管线 (prepare → execute → finalize)

```
prepareToolCall()
  ├─ 查找工具 (找不到 → immediate error)
  ├─ prepareArguments()          ← 参数兼容 shim
  ├─ validateToolArguments()     ← schema 验证
  └─ beforeToolCall()            ← 可 block

executePreparedToolCall()
  └─ tool.execute(id, args, signal, onUpdate)

finalizeExecutedToolCall()
  └─ afterToolCall()             ← 可改 content/details/terminate/isError
```

---

## 扩展点: AgentLoopConfig (types.ref)

```typescript
interface AgentLoopConfig extends SimpleStreamOptions {
  model: Model;

  // === 消息转换 ===
  convertToLlm: (msgs: AgentMessage[]) => Message[];
  // AgentMessage → LLM Message, 过滤自定义消息类型

  transformContext?: (msgs: AgentMessage[], signal?) => Promise<AgentMessage[]>;
  // 调 LLM 前改 messages (上下文窗口裁剪/注入)

  // === 工具拦截 (单次, 非链式) ===
  beforeToolCall?: (ctx: BeforeToolCallContext, signal?) => Promise<BeforeToolCallResult | undefined>;
  // 返回 { block: true, reason } 阻止执行

  afterToolCall?: (ctx: AfterToolCallContext, signal?) => Promise<AfterToolCallResult | undefined>;
  // 返回 { content?, details?, isError?, terminate? } 覆盖结果

  // === 轮次控制 ===
  shouldStopAfterTurn?: (ctx: ShouldStopAfterTurnContext) => boolean | Promise<boolean>;
  // 每轮结束后: 是否提前终止

  prepareNextTurn?: (ctx: PrepareNextTurnContext) => AgentLoopTurnUpdate | undefined;
  // 每轮结束后: 返回 { context?, model?, thinkingLevel? } 替换下轮状态

  // === 消息队列 ===
  getSteeringMessages?: () => Promise<AgentMessage[]>;
  // 运行中注入 (用户中途插话)

  getFollowUpMessages?: () => Promise<AgentMessage[]>;
  // agent 停止后追加

  // === 其他 ===
  toolExecution?: "sequential" | "parallel";  // 默认 parallel
  getApiKey?: (provider: string) => Promise<string | undefined>;
  // 动态 API key (OAuth 过期场景)
}
```

**与 LangChain 中间件的对比**:

| LangChain | Pi | 区别 |
|-----------|-----|------|
| `wrap_model_call` (洋葱链) | 无对应 | Pi 不提供模型调用拦截, 只有 `transformContext` 改输入 |
| `wrap_tool_call` (洋葱链) | `beforeToolCall` + `afterToolCall` | 单次钩子, 非链式组合 |
| `before_model` / `after_model` (图节点) | `shouldStopAfterTurn` / `prepareNextTurn` | 回调 vs 图节点 |
| `before_agent` / `after_agent` (图节点) | `Agent.prompt()` 的调用方自行处理 | 不在循环内 |
| `jump_to` (state 字段) | `AgentToolResult.terminate` | 声明式 vs 路由式 |

---

## 工具定义 (types.ref, AgentTool)

```typescript
interface AgentTool extends Tool {
  label: string;                    // UI 显示名
  prepareArguments?: (args) => params;  // 参数兼容 shim (schema 验证前)
  execute: (
    toolCallId: string,
    params: TParameters,
    signal?: AbortSignal,
    onUpdate?: AgentToolUpdateCallback,  // 流式进度更新
  ) => Promise<AgentToolResult>;
  executionMode?: "sequential" | "parallel";  // per-tool 覆盖
}

interface AgentToolResult {
  content: (TextContent | ImageContent)[];  // 返回给模型的内容
  details: T;                               // 结构化数据 (日志/UI)
  addedToolNames?: string[];                // 声明式动态工具注册
  terminate?: boolean;                      // 提前终止提示
}
```

**动态工具注册**: 工具执行结果通过 `addedToolNames` 声明引入新工具.
对比 LangChain: 需要中间件同时实现 `awrap_model_call` (注入工具列表) +
`awrap_tool_call` (路由工具实例), 因为 factory 内部会验证工具名.

---

## 消息系统 (types.ref)

```typescript
// AgentMessage = LLM 标准消息 + 应用自定义消息
type AgentMessage = Message | CustomAgentMessages[keyof CustomAgentMessages];

// 应用通过 declaration merging 扩展:
declare module "@earendil-works/pi-agent-core" {
  interface CustomAgentMessages {
    artifact: ArtifactMessage;
    notification: NotificationMessage;
  }
}

// convertToLlm 负责过滤: 只保留 user/assistant/toolResult
```

**与 LangChain 的对比**: LangChain 的 `content: str | list[str|dict]` 多态 +
`content_blocks` 归一化 + provider translator 链. Pi 把这个问题推给
`convertToLlm` 一个函数, 应用层自己决定怎么转.

---

## 事件系统 (types.ref, AgentEvent)

```typescript
type AgentEvent =
  | { type: "agent_start" }
  | { type: "agent_end"; messages: AgentMessage[] }
  | { type: "turn_start" }
  | { type: "turn_end"; message; toolResults }
  | { type: "message_start"; message }
  | { type: "message_update"; message; assistantMessageEvent }  // 流式
  | { type: "message_end"; message }
  | { type: "tool_execution_start"; toolCallId; toolName; args }
  | { type: "tool_execution_update"; toolCallId; toolName; args; partialResult }
  | { type: "tool_execution_end"; toolCallId; toolName; result; isError };
```

`Agent.subscribe(listener)` 按订阅顺序 await 每个 listener.
`agent_end` 是最后一个事件, 但 run 要等所有 `agent_end` listener settle 后才算 idle.

---

## Agent 类 (agent.ref) — 运行时封装

```typescript
class Agent {
  // 状态
  get state(): AgentState;  // systemPrompt, model, tools, messages, isStreaming, ...

  // 生命周期
  prompt(input): Promise<AgentMessage[]>;   // 新对话
  continue(): Promise<AgentMessage[]>;      // 从当前 transcript 继续
  abort(): void;                            // 中止当前 run
  waitForIdle(): Promise<void>;             // 等待 run + listeners 完成
  reset(): void;                            // 清空一切

  // 消息队列
  steer(message): void;       // 运行中插话 (内循环注入)
  followUp(message): void;    // 停止后追加 (外循环注入)

  // 事件
  subscribe(listener): () => void;
}
```

**关键设计**: `Agent` 每次 `prompt()` 创建 context snapshot (浅拷贝 messages/tools),
传给无状态的 `runAgentLoop`. 循环内的修改不回写到 Agent 状态——
状态更新通过 `processEvents` 的事件 reduce 完成 (`message_end` → push 到 messages).

---

## 与 LangGraph 的系统对比

| 维度 | LangGraph (create_agent) | Pi (agentLoop) |
|------|--------------------------|----------------|
| **控制流** | 数据 (图拓扑), 运行时解释 | 代码 (while 循环), 直接执行 |
| **代码量** | factory.py 2007 行 + types.py 2161 行 | agent-loop.ts 791 行 + types.ts 437 行 |
| **扩展模型** | 中间件洋葱链 (7 钩子) | 扁平回调 (config 字段) |
| **组合能力** | 多中间件自动链式组合 | 无组合, 应用层自行编排 |
| **持久化** | 原生 checkpoint (任意节点间) | 应用层实现 (session 层) |
| **人工介入** | `interrupt_before/after` (图原生) | steering queue (应用层) |
| **多 Agent** | 子图嵌套 / Send() / Command(goto) | 手动 wire |
| **可观测性** | LangSmith 图可视化 | 事件流 (应用层接) |
| **依赖** | langchain + langgraph + langsmith + provider SDKs | pi-ai (自研 LLM 抽象) |
| **版本风险** | 高 (1.0 彻底重写) | 低 (代码即接口) |
| **调试** | 图 + channel + reducer + 中间件链 | 一个 while 循环 |
| **抽象泄漏** | 有 (动态工具需同时实现两个 wrap) | 无 (addedToolNames 声明式) |

### LangGraph 真正赢的场景

- 需要 checkpoint/恢复 (长时任务, 断点续跑)
- 需要 human-in-the-loop (审批门)
- 需要多 agent 编排 (子图, 并行分发)
- 需要中间件自动组合 (retry + cache + auth 各自独立)

### Pi 真正赢的场景

- 控制流简单 (ReAct 循环, 无分支)
- 需要运行时交互 (steering, 用户中途插话)
- 需要极简依赖 (coding agent, 嵌入式)
- 需要完全理解运行时 (无黑箱)
- 工具实现是主要复杂度来源 (不是控制流拓扑)

---

## 对本项目的启示

本项目用 `create_agent` 实际只使用了:
- 基础 ReAct 循环 (model → tools → model)
- `awrap_model_call` (动态工具注入 + 重试)
- `aafter_model` (工具调用计数限制)
- `awrap_tool_call` (动态工具路由)

未使用: checkpoint, interrupt, 子图, structured output, Send() 并行.

即 LangGraph 2007 行 factory 中约 20% 的能力, 承担 100% 的依赖和抽象成本.

如果未来需要降低此成本, Pi 模式的迁移路径:

```
ModelRetryMiddleware     → 外层 try/catch + exponential backoff (~30 行)
ToolCallLimitMiddleware  → 循环内计数器 + break (~10 行)
ToolDiscoveryMiddleware  → beforeToolCall + 工具列表动态更新 (~40 行)
SkillLoadMiddleware      → 同上, per-skill 隔离 (~40 行)
create_agent + 图编译    → 一个 ~200 行的 runLoop 函数
```

这不是"现在必须做"的事. 当前 LangChain 稳定工作. 这是"如果 LangChain 维护
成本超过其价值"时的退路, 也是理解 agent 循环本质 (去掉所有框架后它到底是什么)
的最佳参考.

---

## 速查: .ref 文件对照

| 文件 | 对应源码 | 行数 |
|------|---------|------|
| `agent-loop.ref` | `packages/agent/src/agent-loop.ts` | 791 |
| `types.ref` | `packages/agent/src/types.ts` | 437 |
| `agent.ref` | `packages/agent/src/agent.ts` | 574 |
