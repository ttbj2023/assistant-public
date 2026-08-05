# LangChain-Core 数据结构阅读指南

> 版本: langchain-core==1.4.8 | 仅覆盖本项目 (`src/`) 实际 import 的类型
>
> 完整源码副本: [langchain-core-src/](langchain-core-src/) (`.ref` 后缀, 避免被代码扫描纳入)

---

## 阅读顺序

按认知成本分三档. 第 1 档 5 分钟扫完; 第 2 档是核心, 需要细读; 第 3 档按需查.

```
第 1 档 (纯数据容器, 无逻辑)     → Document, Embeddings, RunnableConfig
第 2 档 (消息系统, 全链路流转)    → BaseMessage → AIMessage → ToolMessage → AIMessageChunk
第 3 档 (工具系统, 有 schema 生成) → BaseTool, StructuredTool
第 4 档 (回调/输出, 仅 usage.py)  → AsyncCallbackHandler, LLMResult
```

---

## 第 1 档: 纯数据容器

这三个类型**无内部逻辑**, 知道字段形状即可, 不用逐行读源码.

### Document

**源码**: `documents_base.ref` L288-347

```python
class Document(BaseMedia):
    page_content: str
    type: Literal["Document"] = "Document"
    # 继承自 BaseMedia:
    #   id: str | None = None
    #   metadata: dict[Any, Any] = {}
```

**项目用法**: 向量存储 (`langchain_vector_store.py`) 和知识库 (`knowledge_base/store.py`) 的文档表示. 本质就是 `page_content + metadata` 二元组.

**读源码时跳过**: `Blob` 类 (L59-285, 文件加载器用, 项目不用), `__str__` 重写.

### Embeddings

**源码**: `embeddings.ref` (全文仅 78 行)

```python
class Embeddings(ABC):
    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await run_in_executor(None, self.embed_documents, texts)
    async def aembed_query(self, text: str) -> list[float]:
        return await run_in_executor(None, self.embed_query, text)
```

**项目用法**: `src/inference/embeddings/` 工厂返回此类型; 向量存储调 `aembed_documents`/`aembed_query`.

**要点**: async 版默认走线程池包装同步版. 项目自己的 embedding 实现 (`formats.py`) 直接实现了 async 方法.

### RunnableConfig

**源码**: `runnables_config.ref` L57-129

```python
class RunnableConfig(TypedDict, total=False):
    tags: list[str]
    metadata: dict[str, Any]
    callbacks: Callbacks
    run_name: str
    max_concurrency: int | None
    recursion_limit: int
    configurable: dict[str, Any]
    run_id: uuid.UUID | None
```

**项目用法**: `inference_coordinator.py` 构建后传给 `agent.invoke(input, config=runnable_config)`. 项目实际只填 `callbacks` 和 `configurable` 两个键, 当普通 dict 用.

**读源码时跳过**: `ensure_config` / `patch_config` / `merge_configs` 等辅助函数 (框架内部用).

---

## 第 2 档: 消息系统 (核心)

消息类型在 processor → assembler → builder → history 全链路流转, 是理解项目数据流的关键.

### 继承关系

```
Serializable (langchain_core.load)
  └── BaseMessage          ← messages/base.py:93
        ├── HumanMessage   ← messages/human.py:9   (仅重写 type="human")
        ├── SystemMessage  ← messages/system.py:9   (仅重写 type="system")
        ├── AIMessage      ← messages/ai.py:160     (+tool_calls, +usage_metadata)
        │     └── AIMessageChunk  ← messages/ai.py:418  (+tool_call_chunks, 流式合并)
        └── ToolMessage    ← messages/tool.py:26    (+tool_call_id, +artifact, +status)

BaseMessageChunk           ← messages/base.py (Mixin, 提供 __add__ 合并逻辑)
```

### BaseMessage

**源码**: `messages_base.ref` L93-320 (核心部分)

```python
class BaseMessage(Serializable):
    content: str | list[str | dict[Any, Any]]   # ← 多态! 见下方说明
    additional_kwargs: dict[Any, Any] = {}       # provider 原始附加数据
    response_metadata: dict[Any, Any] = {}       # 响应头/logprobs/token counts/model name
    type: str                                     # 序列化标记 ("human"/"ai"/"tool"/"system")
    name: str | None = None                       # 可选名称
    id: str | None = None                         # 可选唯一标识
```

**关键: `content` 的多态性**

`content` 是项目中最容易踩坑的字段. 它有两种形态:

```python
# 形态 1: 纯文本 (最常见)
HumanMessage(content="你好")

# 形态 2: content blocks 列表 (多模态 / 工具调用场景)
HumanMessage(content=[
    {"type": "text", "text": "描述这张图片"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
])
```

项目中的处理:
- `inference_coordinator.py` 的 `content_to_text()` 工具函数负责从两种形态中提取纯文本
- `_history_has_image_blocks()` 检测历史消息是否含 image_url 块
- `processor_orchestrator.py` 构建 `HumanMessage`/`SystemMessage` 时用纯文本形态

**`.text` 属性** (L262-292): 从 `content` 中提取纯文本. 如果 content 是 list, 只拼接 `type="text"` 的块. 项目不直接用此属性, 但理解它有助于理解框架行为.

**读源码时跳过**:
- `content_blocks` 属性 (L199-260): 多 provider 格式归一化, 项目不直接用
- `pretty_repr` / `__add__` (L294-518): prompt 模板拼接, 项目不用

### HumanMessage / SystemMessage

**源码**: `messages_human.ref` / `messages_system.ref` (各 70 行)

```python
class HumanMessage(BaseMessage):
    type: Literal["human"] = "human"
    # 无其他字段, 无其他逻辑
```

**跳过不读**. 仅重写 `type` 做序列化标记.

### AIMessage

**源码**: `messages_ai.ref` L160-417 (核心部分)

```python
class AIMessage(BaseMessage):
    tool_calls: list[ToolCall] = []              # ← 模型请求调用的工具列表
    invalid_tool_calls: list[InvalidToolCall] = [] # 解析失败的工具调用
    usage_metadata: UsageMetadata | None = None   # ← token 用量
    type: Literal["ai"] = "ai"
```

**ToolCall 结构** (`messages_tool.ref` L206-239):

```python
class ToolCall(TypedDict):
    name: str              # 工具名
    args: dict[str, Any]   # 参数字典 (已解析的 JSON)
    id: str | None         # 关联 ToolMessage 的标识
    type: NotRequired[Literal["tool_call"]]
```

**项目用法**:
- `inference_coordinator.py:1818` — 遍历 `message.tool_calls` 追踪工具调用状态
- `inference_coordinator.py:1449` — 从最后一条 AIMessage 提取 tool_calls 做结果配对
- `agent_utils.py:18` — 专家工具错误处理时遍历 `msg.tool_calls`
- `_skill_load.py:180` — 中间件扫描 `prev.tool_calls` 检测 load_skill 调用

**UsageMetadata 结构** (`messages_ai.ref` L104-157):

```python
class UsageMetadata(TypedDict):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_token_details: NotRequired[InputTokenDetails]   # audio/cache_creation/cache_read
    output_token_details: NotRequired[OutputTokenDetails]  # audio/reasoning
```

**项目用法**: `usage.py:368-370` — 从 `message.usage_metadata` 提取 token 用量.

**读源码时跳过**:
- `content_blocks` 属性 (L242-304): provider translator 逻辑
- `_backwards_compat_tool_calls` (L306-): 向后兼容, 项目不涉及
- `lc_attributes` / `__add__`: 序列化与合并, 非流式路径不用

### ToolMessage

**源码**: `messages_tool.ref` L26-205

```python
class ToolMessage(BaseMessage, ToolOutputMixin):
    tool_call_id: str                    # ← 关联 AIMessage.tool_calls[i].id
    type: Literal["tool"] = "tool"
    artifact: Any = None                 # 不发给模型的工具执行产物
    status: Literal["success", "error"] = "success"
```

**核心机制**: `tool_call_id` 把工具执行结果和 AIMessage 中的工具调用请求配对. 多个工具并行调用时靠此字段区分.

**项目用法**:
- `inference_coordinator.py:1446` — `tool_results[msg.tool_call_id] = msg.content` 收集结果
- `inference_coordinator.py:1757-1758` — 流式路径中按 `tool_call_id` 匹配 pending_tool_calls 状态
- `_skill_load.py` / `_tool_discovery.py` — 中间件检测 ToolMessage 判断工具调用结果

**读源码时跳过**: `coerce_args` (L90-120, content 类型强制转换), `ToolOutputMixin` (L16-23, 仅标记).

### AIMessageChunk (流式)

**源码**: `messages_ai.ref` L418-854

```python
class AIMessageChunk(AIMessage, BaseMessageChunk):
    tool_call_chunks: list[ToolCallChunk] = []   # 流式工具调用碎片
    chunk_position: Literal["last"] | None = None
    type: Literal["AIMessageChunk"] = "AIMessageChunk"
```

**ToolCallChunk 结构** (`messages_tool.ref` L261-301):

```python
class ToolCallChunk(TypedDict):
    name: str | None     # 可能跨 chunk 拼接
    args: str | None     # JSON 字符串碎片, 需累积后 parse
    id: str | None
    index: int | None    # 同一 index 的 chunk 合并
```

**流式合并机制**: `AIMessageChunk.__add__` (继承自 `BaseMessageChunk`) 将两个 chunk 合并:
- `content` 字符串拼接
- `tool_call_chunks` 按 `index` 配对, 字符串字段拼接
- `usage_metadata` 数值相加

**项目用法**: `inference_coordinator.py` 的 `_process_stream_chunk` 方法处理每个 chunk:
- 累积 `tool_call_chunks` 的 args 碎片
- 检测 `chunk_position="last"` 判断工具调用完成
- 用 `_StreamState` 跟踪跨 chunk 状态

**读源码时跳过**:
- `content_blocks` 属性 (L445-506): provider translator
- `init_tool_calls` (L508-): 从 chunks 初始化 tool_calls 的 validator

---

## 第 3 档: 工具系统

### BaseTool

**源码**: `tools_base.ref` L427-700 (核心部分, 全文 1711 行)

```python
class BaseTool(RunnableSerializable[str | dict[str, Any] | ToolCall, Any]):
    name: str                              # 工具唯一名称
    description: str                       # 告诉模型何时/如何使用
    args_schema: type[BaseModel] | None    # Pydantic 输入 schema
    return_direct: bool = False            # True 时工具结果直接返回, 不再过 LLM
    handle_tool_error: bool | str | Callable = False  # 错误处理策略
    response_format: Literal["content", "content_and_artifact"] = "content"
```

**项目用法**: 所有 30+ 工具继承此类, 典型模式:

```python
# src/tools/internal/create_todo_tool.py (典型示例)
class CreateTodoTool(BaseTool):
    name: str = "create_todo"
    description: str = "创建待办事项..."
    args_schema: type[CreateTodoRequest] = CreateTodoRequest  # Pydantic 模型

    async def _arun(self, title: str, ...) -> str:
        # 实际业务逻辑
        ...
```

**Schema 生成** (L264-320): `create_schema_from_function` 从函数签名自动生成 Pydantic schema. 项目工具显式声明 `args_schema`, 不走自动推断, 但 MCP 工具桥接 (`mcp_tool_manager.py:356`) 用 `_schema_to_pydantic` 从 JSON Schema 动态生成.

**invoke/ainvoke 调用链** (L731-900):
```
tool.invoke(input) → _parse_input → _run / _arun → 包装为 ToolMessage
```

**读源码时**:
- **必读** L427-576: 字段定义和语义
- **必读** L731-900: invoke/ainvoke 如何调 _run/_arun, 输入验证流程
- **跳过** L94-320: Pydantic v1/v2 兼容桥, docstring 解析辅助函数
- **跳过** L900+: `convert_to_tool` 工厂, `RunnableSerializable` 继承链

### StructuredTool

**源码**: `tools_structured.ref` (271 行)

```python
class StructuredTool(BaseTool):
    func: Callable | None = None
    coroutine: Callable | None = None

    @classmethod
    def from_function(cls, func, name, description, args_schema, ...) -> StructuredTool: ...
```

**项目用法**: `mcp_tool_manager.py:390` — 将 MCP 工具包装为 LangChain 工具:

```python
return StructuredTool(
    name=tool_name,
    description=description,
    args_schema=args_schema,       # 从 JSON Schema 动态生成的 Pydantic 模型
    coroutine=coroutine,
    response_format="content",
)
```

与 `BaseTool` 子类化的区别: `StructuredTool` 不需要定义新类, 直接传函数引用. 适合动态创建工具的场景.

---

## 第 4 档: 回调与输出 (仅 usage.py)

### AsyncCallbackHandler

**源码**: `callbacks/base.py` L548+ (项目只用 3 个方法)

```python
class AsyncCallbackHandler(BaseCallbackHandler):
    async def on_chat_model_start(self, serialized, messages, *, run_id, ...): ...
    async def on_llm_start(self, serialized, prompts, *, run_id, ...): ...
    async def on_llm_end(self, response: LLMResult, *, run_id, ...): ...
    # 还有 on_llm_error / on_llm_new_token 等, 项目未用
```

**项目用法**: `usage.py:245` — `UsageTrackingCallback(AsyncCallbackHandler)`:
- `on_chat_model_start` / `on_llm_start`: 记录开始时刻 (按 run_id)
- `on_llm_end`: 计算延迟, 从 `LLMResult` 提取 token 用量

### LLMResult

**源码**: `outputs/llm_result.ref` (112 行)

```python
class LLMResult(BaseModel):
    generations: list[list[Generation | ChatGeneration | ...]]
    llm_output: dict[str, Any] | None = None   # provider 原始输出 (含 token_usage)
    run: list[RunInfo] | None = None
```

**项目用法**: `usage.py:349-375` — 从 `response.generations[0][0].message.usage_metadata` 和 `response.llm_output` 两个路径提取 token 用量 (不同 provider 放在不同位置).

---

## 速查: 项目 import 映射

| 项目文件 | 导入的 langchain-core 类型 |
|---------|--------------------------|
| `inference_coordinator.py` | AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage, RunnableConfig |
| `assembler.py` | AIMessage, BaseMessage, HumanMessage |
| `conversation_messages_builder.py` | AIMessage, BaseMessage, HumanMessage |
| `simple_memory_processor.py` | AIMessage, BaseMessage, HumanMessage |
| `base_processor.py` | BaseMessage |
| `processor_orchestrator.py` | HumanMessage, SystemMessage |
| `langchain_vector_store.py` | Document, Embeddings |
| `knowledge_base/store.py` | Document, Embeddings |
| `usage.py` | AsyncCallbackHandler, LLMResult |
| `llm_factory.py` | BaseChatModel (类型标注) |
| 所有 `src/tools/` | BaseTool, StructuredTool |
| `_skill_load.py` / `_tool_discovery.py` | AIMessage, ToolMessage, BaseTool |

## 速查: .ref 文件对照

| 文件 | 对应源码 | 行数 |
|------|---------|------|
| `messages_base.ref` | `langchain_core/messages/base.py` | 518 |
| `messages_ai.ref` | `langchain_core/messages/ai.py` | 854 |
| `messages_human.ref` | `langchain_core/messages/human.py` | 70 |
| `messages_system.ref` | `langchain_core/messages/system.py` | 70 |
| `messages_tool.ref` | `langchain_core/messages/tool.py` | 418 |
| `messages_content.ref` | `langchain_core/messages/content.py` | 1488 |
| `tools_base.ref` | `langchain_core/tools/base.py` | 1711 |
| `tools_structured.ref` | `langchain_core/tools/structured.py` | 271 |
| `documents_base.ref` | `langchain_core/documents/base.py` | 347 |
| `embeddings.ref` | `langchain_core/embeddings/embeddings.py` | 78 |
| `runnables_config.ref` | `langchain_core/runnables/config.py` | 712 |
| `chat_models.ref` | `langchain_core/language_models/chat_models.py` | 2711 |
| `language_models_base.ref` | `langchain_core/language_models/base.py` | 484 |
