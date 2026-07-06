# 测试体系

Personal Agent Assistant v1.9.0 三层测试体系. 设计规范总览见 [docs/development/testing.md](../docs/development/testing.md).

## 当前状态 (2026-07-02)

| 类型 | 测试数 | 文件数 | 通过率 | 性质 |
|------|--------|--------|--------|------|
| 单元 | 2933 | 189 | 100% | 白盒, Mock 外部依赖 |
| 集成 | 107 | 27 | 100% | 灰盒, 真实组件协作 |
| E2E | 13 | 6 | 100% | 灰盒, ASGI TestClient 进程内 |

综合行覆盖率(unit+integration) 82.62%, CI 门禁 80% (pyproject.toml fail_under). 静态分析双模式: 核心模式=CI门禁(阻断), 完整模式=探索工具(改进信号, 非阻断). Mock 工厂统一可用.

## 目录结构

```
tests/
├── conftest.py                  # 全局配置 + 测试身份 fixture
├── unit/                        # 单元测试 (按架构层分子目录: agent/api/auth/config/core/inference/memory/storage/tools/utils)
├── integration/                 # 集成测试 (100% 标记 @pytest.mark.integration)
├── e2e/                         # E2E 测试 (ASGI TestClient 进程内, 详见 e2e/README.md)
├── mocks/
│   ├── unified_factory.py       # LLM/Embedding/VectorStore Mock
│   └── service_mock_factory.py  # Service 层 Mock
└── utils/                       # test_id_generator.py
```

## 运行命令

> 完整命令与 Codex 沙盒变体见 [AGENTS.md](../AGENTS.md) "开发命令速查".

```bash
python scripts/run_test_suite.py --quick     # 快速验证 (~14秒)
python scripts/run_test_suite.py             # 完整验证 (~16秒)
pytest tests/unit/                           # 直接 pytest
pytest -n 6 tests/unit/                      # 并发单元 (~9秒)
pytest tests/integration/                    # 集成 (100% 标记覆盖)
pytest tests/e2e/                            # E2E (进程内, 无需启动服务, 串行 -n 0)
```

## pytest 标记系统

自定义标记统一在 `pyproject.toml` 维护，当前核心标记为: `unit` / `integration` / `e2e` / `serial`。

| 标记 | 用途 |
|------|------|
| `unit` | 单元测试，也可作为“兜底表达式”中的优先项 |
| `integration` | 集成测试 | 由 `tests/integration/conftest.py` 自动添加；显式携带可选 |
| `e2e` | 端到端测试 | `tests/e2e/` 下所有测试均应显式携带 |
| `serial` | 必须串行执行的测试，避免并发状态干扰 |

**智能标记策略** (关键): 单元测试标记覆盖率很低，直接 `pytest -m unit` 会漏跑大量无标记测试。`scripts/run_test_suite.py` 使用以下表达式兜底:

```
-m "unit or (not integration and not e2e)"
```

效果: 优先跑带 `unit` 标记的，兜底跑所有非集成/非 E2E 测试; 集成/E2E 不跑 (实际 60 有标记 + 2845 无标记 = 2905 全覆盖)。集成测试由 `tests/integration/conftest.py` 自动补标 `integration`，可直接 `pytest -m integration` 全量收集；E2E 目录要求 100% 显式标记覆盖，可直接 `pytest -m e2e`。

```python
# 类级别标记 (推荐, 减少重复)
@pytest.mark.integration
class TestMemoryRetrievalIntegration:
    def test_retrieval_with_sql_database(self): ...
```

## 测试身份 fixture

通过 conftest 注入, **禁止硬编码身份字面量** (破坏并发隔离):

| Fixture | 说明 |
|---------|------|
| `test_user` | 并发安全用户ID, 自带进程后缀 |
| `test_thread_id` | 基于 `{test_category}_{function_name}_{random_suffix}` 生成 |
| `thread_id_factory` | 同一测试内模拟多个隔离线程 |

测试环境路径自动隔离: `ENVIRONMENT=testing` 时数据落到 `./test_data/{user_id}/{thread_id}/`, 会话结束 `auto_test_cleanup` fixture 自动清理.

## 三层测试定位

| 层 | Mock 边界 | 详细规范 |
|----|----------|---------|
| **单元** (白盒) | Mock 所有外部依赖 (DB/网络/文件/时间/随机性) | [unit_test_design_specification.md](../docs/development/unit_test_design_specification.md) (含 Mock 体系章节) |
| **集成** (灰盒) | 只 Mock 真正的外部服务 (LLM/第三方API) | [integration_test_design_specification.md](../docs/development/integration_test_design_specification.md) |
| **E2E** (灰盒) | 仅 E2EMockLLM 脚本化, 其余真实组件, 可访问 DB 验证状态 | [tests/e2e/README.md](./e2e/README.md) |

## 数据库架构 (测试环境)

- `conversation_history.db`: `conversation_index` + `simple_pinned_memory` 表
- `pinned_memory.db`: 独立置顶记忆数据库
- 路径: `./test_data/{user_id}/{thread_id}/database/`
- 集成测试通过 `db_session` / `pinned_memory_db` fixture 初始化表结构, fixture 与 Service 层共用路径实现跨组件数据共享

## 相关文档

- [测试设计规范 (总览)](../docs/development/testing.md)
- [单元测试规范](../docs/development/unit_test_design_specification.md)
- [集成测试规范](../docs/development/integration_test_design_specification.md)
- [E2E 测试](./e2e/README.md)
- [静态分析](../docs/development/static-analysis.md)
