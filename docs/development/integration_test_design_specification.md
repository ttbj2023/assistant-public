# 集成测试设计规范

集成测试的**唯一权威设计指南**. 实践案例参考 `tests/integration/` 目录的实际测试实现.

## 1. 核心定位

**集成测试是灰盒测试**, 在单元测试与 E2E 之间填补不可替代的 gap:

- **单元测试** mock 了所有外部依赖, 无法验证真实组件接口契约
- **E2E** 启动完整 ASGI, 太重且只验端到端
- **集成测试** 用真实组件协作 + 仅 Mock 外部服务, 验证组件间协作的真实行为

**核心价值**(不可替代):
1. **验证真实组件协作**: 接口契约 / 数据流 / 跨组件业务流程完整性
2. **承接单元测试豁免盲区**: LangChain Agent 编排等难 mock 的行为, 单元测试豁免后由集成测试承接(见 §5.4)
3. **可控的真实性**: 比 E2E 轻, 比单元测试真实

> 三层测试的 Mock 边界对比见 [testing.md 三层架构表](./testing.md). 集成测试不得验证单组件内部业务逻辑(属单元测试)或进行性能/负载测试; Mock 边界见 §2.

## 2. Mock 边界

**可以 Mock (外部服务)**: LLM API / 外部数据库 / 第三方 API / 外部文件存储.

**不能 Mock (内部组件)**: Agent 组件 (AgentFactory/AgentManager) / 记忆系统 (MemoryProcessor/MemoryRetrievalTool) / 工具系统 (TodoTool/ToolsManager) / 存储层 (DAO/数据库连接管理器).

> 项目 Service 层 Mock 用 `ServiceMockFactory`, 但集成测试中应优先用**真实 Service** 验证协作, 仅 Mock 真正的外部依赖 (LLM/第三方API). 详见 [单元测试规范 §7 Mock 体系](./unit_test_design_specification.md#7-mock-体系).

## 3. 集成测试场景类型

> 以下为场景**类别与测试重点**, 具体写法以 `tests/integration/` 下已通过 CI 的真实测试为基准. 不绑定具体类名/签名.

| 场景类别 | 测试重点 | 参考入口 |
|---------|---------|---------|
| Agent 工厂与系统装配 | AgentFactory + AgentManager + 配置系统协作, Agent 创建与执行 | `tests/integration/test_agent_system_integration.py` |
| 记忆系统协作 | 记忆组装、索引生成与检索、置顶记忆管道、处理器编排 | `tests/integration/memory/` |
| DAO 存储与事务 | 多 DAO 协作、事务一致性、轮次号分配、定时消息生命周期 | `tests/integration/storage/` |
| 配置与认证系统 | 静态用户管理 + API 密钥系统 + 配置加载协作 | `tests/integration/auth/` |
| 工具系统协作 | 工具装配、参数传递、专家工具与 MCP 子工具协作 | `tests/integration/tools/` |
| API 与流式 | FastAPI 装配、流式响应链路 | `tests/integration/test_streaming_flow.py` |
| 双路检索 | DualStageRetrievalService SQL+向量双阶段检索联动 | `tests/integration/test_retrieval_two_stage_integration.py` |

## 4. 测试维度与检查清单

集成测试除正向协作外, 应覆盖以下维度(同时作为合规检查要点):

| 维度 | 检查要点 |
|------|---------|
| **错误传播** | 外部服务错误如何在组件间传播, 错误后系统状态 (降级/恢复) |
| **并发安全** | 多 Agent/多请求并发访问的数据隔离与一致性 (`asyncio.gather` + 验证) |
| **业务流程完整性** | 多轮用户场景 (创建任务→记录记忆→查询→搜索), 验证跨组件数据关联 |
| **数据一致性恢复** | 部分数据库操作失败时的回滚与清理机制 |

**合规检查清单**:
- [ ] 命名遵循 `test_integration_*` 格式
- [ ] docstring 含协作场景 / Mock 边界 / 验证重点 / 业务价值
- [ ] 使用 `test_user` / `test_thread_id` fixture, 标 `@pytest.mark.integration`
- [ ] 内部组件真实实例, 仅 Mock 外部服务 (边界见 §2)

## 5. 测试质量标准

### 5.1 覆盖率

集成测试不单独考核覆盖率(避免为凑指标产生冗余低价值测试). 集成测试贡献于合并覆盖率 CI 门禁(见 testing.md), 重点验证组件协作、错误传播、数据一致性等维度而非行覆盖数字.

### 5.2 命名规范

格式: `test_integration_[协作组件A]_[协作动作]_[协作组件B]`

```python
# ✅ test_integration_todo_tool_memory_retrieval_collaboration
# ✅ test_integration_agent_factory_database_error_propagation
# ❌ test_integration_tools  # 不够具体
```

### 5.3 文档要求

在 [单元测试规范 §5.2 docstring](./unit_test_design_specification.md#52-文档字符串规范) 通用要求(测试目的/业务价值/验证要点)基础上, 集成测试 docstring 额外要求:

- **协作场景说明**: 哪些组件协作, 验证什么协作关系
- **Mock 边界声明**: Mock 了哪些外部服务, 保留了哪些真实组件

### 5.4 承接单元测试豁免项

部分单元测试因难以干净 mock 复杂框架依赖(如专家工具 deep 模式内部启动的 LangChain Agent 编排)而被豁免. 详见 [单元测试规范 §3.5](./unit_test_design_specification.md#35-单元测试豁免情形).

设计集成测试时应查阅源码中 `UNIT_TEST_EXEMPT` 注释 (`grep -r UNIT_TEST_EXEMPT src/`), 确保每个豁免项有对应集成测试覆盖其协作行为. `tests/integration/tools/` 下的 web_research / geo_research 集成测试即为承接豁免项的实例.

## 6. 常见错误

| 错误 | 说明 |
|------|------|
| **降级为单元测试** | 只测单个组件 (`TodoTool().add_task(...)`), 无≥2真实组件协作 → 应属单元测试 |
| **Mock 边界错误** | Mock 了内部组件或边界不清 (正确边界见 §2) |

---

**最后更新**: 2026-07-02 | **架构版本**: v1.9.0 | **适用范围**: 所有集成测试
