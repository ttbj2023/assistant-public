# 测试设计规范

本文档是测试设计的**总览规范**, 定义三层测试架构的职责边界与统一原则. 各层的详细规范见对应子文档.

## 三层测试架构

```
测试体系
├── 单元测试 (基础层)  ← 白盒, 单一模块业务逻辑验证
├── 集成测试 (中间层)  ← 灰盒, 多组件协作验证
└── E2E测试 (验证层)   ← 灰盒, 完整功能流程验证 (API/数据/记忆/工具)
```

| 层 | 性质 | Mock 边界 | 详细规范 |
|----|------|----------|---------|
| **单元** | 白盒 | Mock 所有外部依赖 (DB/网络/文件/时间/随机性) | [unit_test_design_specification.md](./unit_test_design_specification.md) (含 Mock 体系) |
| **集成** | 灰盒 | 只 Mock 真正的外部服务 (LLM/第三方API), 内部组件真实实例 | [integration_test_design_specification.md](./integration_test_design_specification.md) |
| **E2E** | 灰盒 | 仅 E2EMockLLM 脚本化 LLM, 其余真实 (DB/向量/文件系统), 可访问 DB 验证状态 | [tests/e2e/README.md](../../tests/e2e/README.md) |

**禁止越界**: 单元测试不得验证模块间协作; 单元测试不得依赖外部资源; 集成测试不得 Mock 内部组件.

## 测试数据隔离

通过 `tests/conftest.py` 统一管理测试身份, **禁止硬编码身份字面量**:

| Fixture | 说明 |
|---------|------|
| `test_user` | 并发安全用户ID (自带进程后缀, pytest-xdist 安全) |
| `test_thread_id` | `{category}_{function_name}_{random_suffix}` 自动生成 |
| `thread_id_factory` | 同一测试内模拟多个隔离线程 |

测试环境 (`ENVIRONMENT=testing`) 数据自动落到 `./test_data/{user_id}/{thread_id}/`, 会话结束自动清理. 详见 [路径管理](../path-management.md) 测试环境章节.

## 合规检查清单

**单元测试**:
- [ ] 只 Mock 外部依赖, 保留被测业务逻辑真实
- [ ] 测试独立, 不依赖执行顺序
- [ ] 命名 `test_[功能]_[场景]_[期望结果]`, AAA 结构 (Arrange/Act/Assert)
- [ ] 使用 `test_user` / `test_thread_id` fixture, 异步方法用 `AsyncMock`

**集成测试**:
- [ ] 内部组件真实实例, 只 Mock 真正的外部服务
- [ ] 验证组件间接口与数据流, 含错误传播
- [ ] 验证并发访问安全

**E2E测试**:
- [ ] pytest 标准框架 + fixtures 管理生命周期
- [ ] 独立 thread_id 隔离, 测试后自动清理
- [ ] 验证请求-响应-持久化完整链路, 允许 DB 状态验证

## 质量门禁

| 标准 | 要求 |
|------|------|
| 单元测试通过率 | 100% (CI 硬性门禁) |
| 集成测试通过率 | 100% (CI 硬性门禁) |
| 静态分析 | Ruff/MyPy/Bandit/配置治理 全部通过 (quick门禁, core精选规则) |
| 代码覆盖率(CI门禁) | 见 `pyproject.toml [tool.coverage.fail_under]` |
| 代码覆盖率(设计目标) | 核心模块追求高覆盖, 非CI门禁 |
| 执行时间 | 见 CI 配置与 `scripts/run_test_suite.py` 实际输出 |

## 运行命令

> 完整命令见 [AGENTS.md](../../AGENTS.md) “开发命令速查” 和 [tests/README.md](../../tests/README.md).

```bash
python scripts/run_test_suite.py --quick     # 快速验证
python scripts/run_test_suite.py             # 完整验证
pytest tests/unit/ -n 6                      # 并发单元
pytest tests/e2e/ -n 0                       # E2E 串行
```

## pytest 标记约定

自定义标记统一在 `pyproject.toml` 注册和维护，禁止在各级 `conftest.py` / `pytest.ini` 中重复注册.

| 标记 | 含义 |
|------|------|
| `unit` | 单元测试 |
| `integration` | 集成测试 |
| `e2e` | 端到端测试 |
| `serial` | 串行测试 |

`scripts/run_test_suite.py` 对单元测试使用兜底表达式，避免漏跑无标记单元测试:

```text
-m "unit or (not integration and not e2e)"
```

集成测试由 `tests/integration/conftest.py` 自动补标 `integration`，因此可直接通过 `-m integration` 全量收集；E2E 测试直接通过 `-m e2e` 精确筛选. 标记注册细节见 `pyproject.toml`.

## 参考资源

- [单元测试设计规范](./unit_test_design_specification.md) - 单元测试详细标准 (含 Mock 体系)
- [集成测试设计规范](./integration_test_design_specification.md) - 集成测试权威标准
- [E2E 测试](../../tests/e2e/README.md) - ASGI TestClient 架构与 E2EMockLLM 用法
- [测试运行手册](../../tests/README.md) - 标记系统与身份 fixture
- [静态分析](./static-analysis.md) - V2.0 并行架构
