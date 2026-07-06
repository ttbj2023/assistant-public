# 记忆系统双路检索架构设计

## 概述

Personal Agent Assistant v1.9.0 采用**双路检索架构**，实现"SQL为主，向量为辅"的记忆系统设计原则，确保数据一致性和检索效率的最佳平衡。

## 🏗️ 架构设计原则

### 核心理念
1. **SQL数据库为主**：所有记忆内容的组装和获取完全从SQL数据库读取
2. **向量库为辅**：向量库仅用于语义匹配，存储完整对话内容用于检索
3. **双路检索机制**：向量语义搜索获取轮次号 + SQL精确查询获取完整内容
4. **自然轮次排序**：对话历史按自然轮次排序，保持连贯性

### 架构优势
- **数据一致性**：SQL数据库作为唯一真实来源
- **性能优化**：避免不必要的向量库初始化和完整内容传输
- **检索精确性**：语义搜索 + 精确查询的双重保障
- **用户体验**：保持对话的自然顺序和连续性

## 🔧 技术实现

### 双路检索流程

```
检索请求 → AsyncMemoryRetrievalTool → DualStageRetrievalService
                                           ↓
                              ┌─────────────────┐
                              │   第一阶段      │
                              │                 │
                              │ ┌─────────────┐ │
                              │ │ 向量语义搜索 │ │ → 轮次号列表
                              │ └─────────────┘ │
                              │                 │
                              │ ┌─────────────┐ │
                              │ │ SQL精准检索 │ │ → 轮次号列表
                              │ └─────────────┘ │
                              └─────────────────┘
                                           ↓
                              ┌─────────────────┐
                              │   智能去重合并   │ → 候选轮次号
                              └─────────────────┘
                                           ↓
                              ┌─────────────────┐
                              │   第二阶段      │
                              │                 │
                              │ ┌─────────────┐ │
                              │ │ SQL精确查询  │ │ → 完整对话内容
                              │ └─────────────┘ │
                              │                 │
                              │ ┌─────────────┐ │
                              │ │ BGE重排序    │ │ → 最终结果
                              │ └─────────────┘ │
                              └─────────────────┘
```

### 核心组件

#### 1. AsyncMemoryRetrievalTool
**位置**: `src/tools/internal/async_memory_retrieval_tool.py`

**功能**:
- 集成DualStageRetrievalService，实现真正的双路检索
- 智能降级：向量存储不可用时使用纯SQL检索
- 健康检查和错误处理

**关键特性**:
```python
class AsyncMemoryRetrievalTool(BaseTool):
    # 双路检索器
    _retriever: DualStageRetrievalService | None = None

    # 数据管理器（已改为Service层架构）
    _data_services: dict[str, Any] | None = None

    # 向量存储（懒加载）
    _vector_store: Any | None = None
```

#### 2. DualStageRetrievalService
**位置**: `src/storage/service/retrieval_service.py`

**功能**:
- 实现双路检索的核心逻辑
- 智能去重和候选轮次合并
- 基于BGE的重排序

**核心方法**:
```python
def get_relevant_documents(self, query: str) -> list[Document]:
    # 第一阶段：候选轮次筛选
    sql_rounds = self._sql_search_rounds(filters)
    vector_rounds = self._vector_search_rounds(query, filters)
    candidate_rounds = smart_deduplication(sql_rounds, vector_rounds)

    # 第二阶段：内容获取与重排序
    final_documents = self._get_final_documents(query, candidate_rounds)
    return final_documents
```

#### 3. MemoryAssembler
**位置**: `src/agent/memory/local_memory/assembler.py`

**功能**:
- 4部分记忆内容组装为原生 messages 数组 (置顶记忆/索引区/对话历史/TODO)
- 完全基于SQL数据库 (ConversationService) 获取数据
- 主历史与索引区**独立预算** (默认 20000/10000, 不再按比例切分)

**输出结构 (MemoryContext)**:
```python
@dataclass
class MemoryContext:
    history_messages: list[BaseMessage]   # 索引区伪对话轮 + 主历史真实轮次 (时间正序)
    system_prompt_extension: str          # 置顶记忆+用户要求 (含引导语前缀), 拼到 system_prompt 尾部
    todo_list: str                        # 格式化 TODO markdown, 由 processor 注入 current_content
```

**输出 messages 形态**:
```
[HumanMessage("[过往对话回顾]"), AIMessage("<conversation_index>...")]  # 索引区伪对话轮 (主历史未覆盖早期轮次时)
[HumanMessage(轮N原文), AIMessage(轮N回复), ...]                       # 主历史真实轮次
+ system_prompt_extension (置顶记忆/用户要求)
+ todo_list
```

**预算机制 (主历史/索引区独立解耦)**:
- **主历史预算**: `total_char_budget` (默认 20000) → `_resolve_total_char_budget`
- **索引区预算**: `index_char_budget` (默认 10000) → `_resolve_index_char_budget`
- 两区各自独立, 不再按比例切分 total. 配置见 `agent.yaml` 的 `memory:` 段.

**记忆组装流程**:
```python
async def assemble_memory_context(self, user_id, thread_id, total_budget=None, agent_id=None) -> MemoryContext:
    # 1. 解析主历史预算
    budget = self._resolve_total_char_budget(total_budget)
    # 2. 获取置顶记忆/用户要求/TODO (缓存优先, DB 回退)
    # 3. _build_history_messages 组装历史:
    #    - ConversationService 全量获取 ConversationIndex (缓存优先, 命中则增量追加新轮次)
    #    - _select_main_history_suffix: 前缀和 + 二分 (O(log N)) 选 content 和 <= budget 的最大主历史后缀
    #    - 主历史未覆盖到第 1 轮时, _fetch_index_in_budget 构建索引区伪对话轮
    #      (倍增+二分, 预算充足时覆盖到 round 1, 零丢弃)
    # 4. 返回 MemoryContext
```

**核心方法**:
- `_build_history_messages`: 组装历史 messages (索引区伪对话轮 + 主历史真实轮次)
- `_select_main_history_suffix`: 内存二分查找, 选 content 长度和 <= budget 的最大后缀
- `_fetch_index_in_budget` + `_find_latest_formatted_suffix_start`: 二分查找最大索引后缀, 预算充足覆盖到 round 1
- `_get_conversations_with_cache`: 缓存优先全量获取, 命中则增量追加新轮次

**缓存机制**:
- **置顶记忆/TODO 缓存**: 全局缓存优先, DB 回退
- **对话内容缓存**: SplittableMemoryCache (用户-Agent 隔离), 增量追加新轮次

#### 4. LangChainVectorStore
**位置**: `src/storage/langchain_vector_store.py`

**功能**:
- 存储完整对话内容用于语义匹配
- 提供专用的轮次号检索接口

**关键方法**:
```python
# 正确使用方式：仅返回轮次号
async def search_rounds_only(self, query: str, k: int = 10) -> list[int]

# 已弃用的方式：返回完整内容（违反架构原则）
async def similarity_search(self, query: str, max_results: int = 4) -> list[Document]  # 已弃用
```

## 📊 数据流设计

### 记忆组装流程 (MemoryAssembler)
```
记忆请求 → MemoryAssembler.assemble_memory_context()
    ↓
1. 解析主历史预算 (_resolve_total_char_budget)
    ↓
2. 获取置顶记忆/用户要求/TODO (缓存优先, DB 回退)
    ↓
3. _build_history_messages 组装历史
    ↓                            ↓
  ConversationService 全量获取    索引区独立预算 (_resolve_index_char_budget)
  (缓存优先, 增量追加)            ↓
  _select_main_history_suffix      _fetch_index_in_budget
  (前缀和二分, 选主历史后缀)       (二分查找最大索引后缀, 预算充足覆盖到 round 1)
    ↓
4. 返回 MemoryContext (history_messages + system_prompt_extension + todo_list)
```

### 数据获取策略
```
置顶记忆: 全局缓存 + MemoryService
    ↓
索引区 + 对话历史: ConversationService (get_conversations_in_range / get_formatted_index_range)
    ↓
TODO列表: 全局缓存 + TodoService
```

### 缓存架构
```
全局缓存系统 (SplittableMemoryCache, 用户-Agent 隔离)
    ↓
├── 置顶记忆缓存 (get_pinned_memory/set_pinned_memory)
├── 对话内容缓存 (get_conversation/set_conversation) - 命中时增量追加新轮次
└── TODO列表缓存 (get_todolist/set_todolist)
```

### 检索查询流程
```
检索请求 → AsyncMemoryRetrievalTool → 双路检索 → 结果返回
    ↓              ↓                     ↓
  参数验证   DualStageRetrievalService协调    JSON格式输出
    ↓              ↓                     ↓
  初始化检查  向量+SQL双重检索     相关性排序
```

## 🎯 使用指南

### 正确的使用方式

#### 1. 记忆组装
```python
# 正确：完全基于SQL数据库, 传入 agent_id/agent_config
assembler = MemoryAssembler(agent_id="personal-assistant", agent_config=agent_config)
ctx = await assembler.assemble_memory_context(user_id, thread_id)

# ✅ ctx.history_messages → 注入对话; ctx.system_prompt_extension → 拼到 system_prompt; ctx.todo_list → current_content
```

#### 2. 记忆检索
```python
# 正确：使用双路检索
tool = AsyncMemoryRetrievalTool(user_id, thread_id)
results = await tool._arun("查询内容")

# ✅ 这会执行向量语义搜索+SQL精确查询
```

#### 3. 向量库使用
```python
# 正确：仅获取轮次号
round_numbers = await vector_store.search_rounds_only(query, k=5)
conversations = await data_manager.get_conversations_by_rounds(round_numbers)

# ✅ 这符合"SQL为主，向量为辅"的原则
```

### 错误的使用方式（避免）

```python
# ❌ 违反架构原则: 在记忆组装中直接用向量库获取完整内容
# documents = await vector_store.similarity_search(query)
# 应使用 MemoryAssembler 从 SQL 数据库获取

# ❌ 已弃用: 检索中直接获取完整内容
# documents = await vector_store.similarity_search(query, max_results=10)
# 应使用 search_rounds_only() + SQL 查询
```

## 📈 性能优化

### 缓存策略
1. **MemoryAssembler实例缓存**：跨请求复用MemoryAssembler实例
2. **SplittableMemoryCache**：三部分独立LRU缓存
3. **精确缓存失效**：每个组件可独立失效

### 懒加载机制
1. **向量存储懒加载**：仅在需要时初始化
2. **嵌入模型按需加载**：避免启动时性能开销
3. **数据管理器复用**：按用户-线程对复用

### 智能降级
```python
if self._retriever:
    # 双路检索：向量语义搜索 + SQL精确查询
    documents = await self._run_retriever_sync(query)
elif self._data_manager:
    # 降级：纯SQL检索
    results = await self._fallback_sql_search(query)
```

## 🔍 监控和调试

```python
health = await memory_tool.ahealth_check()
# 返回组件状态: data_manager / vector_store / retriever / retrieval_type (dual_stage / sql_fallback)
```

日志监控双路检索各阶段、缓存命中、向量库不可用时的降级情况.

## 🚀 版本演进

- **v1.5 (2025-12-07)**: 双路检索架构确立 (SQL 为主向量为辅, 记忆组装完全基于 SQL), 集成 DualStageRetrievalService, 自然轮次排序.
- **v1.8 (2026-05-24)**: 统一配置架构, ToolCallTracker + PromptCapture 双层调试, Expert 体系重写 (WebResearch/GeoResearch).
- **v1.8.3 (2026-06-25)**: 索引区独立预算, 废弃 primary_memory_ratio 按比例拆分, 主历史/索引区各自独立预算 (默认 20000/10000).

## 📚 相关文档

- [开发指南](../development/) - 记忆系统开发实践
- [测试指南](../development/testing.md) - 记忆系统测试策略
- [配置系统文档](../configuration.md) - v2 配置系统概览
- [config.yaml 参考](../config-yaml-reference.md) - YAML 字段完整参考

---

**更新日期**: 2026-07-02
**适用版本**: v1.9.0+
**架构核心理念**：向量库存储完整对话用于语义匹配，记忆内容组装完全从SQL数据库读取，通过双路检索机制实现最佳的性能和功能平衡。