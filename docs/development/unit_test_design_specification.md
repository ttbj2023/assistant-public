# 单元测试设计规范

## 1. 单元测试核心定位

### 1.1 权威定义

**单元测试是白盒测试**, 专注于验证**单一功能模块的业务逻辑正确性**. 测试者了解内部实现, 通过保留真实业务逻辑、Mock外部依赖的方式, 快速验证功能是否符合预期.

**核心价值**:
- **验证业务逻辑**: 确保功能模块的算法、规则、状态管理正确性
- **快速反馈**: 毫秒级执行, 提供即时的问题发现机制
- **完全隔离**: 不依赖外部资源, 确保测试的稳定性和可重复性
- **回归保护**: 为代码重构提供安全网, 防止功能倒退

### 1.2 与其他测试类型的边界

单元测试 (白盒) / 集成测试 (灰盒) / E2E (灰盒) 的职责划分见 [测试设计规范总览](./testing.md). 单元测试**不得**验证模块间协作、不得包含性能测试、不得依赖外部资源 (详见 §3.4).

## 2. Mock边界权威定义

### 2.1 "外部依赖"精确定义

**外部依赖**指功能模块之外的所有系统组件, 包括:

- **数据库操作**: 连接、查询、事务等所有数据库交互
- **网络API调用**: HTTP请求、第三方服务调用、外部API
- **文件系统操作**: 文件读写、目录操作、配置文件访问
- **外部服务**: 消息队列、缓存服务、认证服务等
- **时间相关**: 系统时间、定时器、延迟操作等
- **随机性操作**: 随机数生成、UUID生成等

### 2.2 Mock策略原则

**Mock策略**: 外部完全Mock, 内部逻辑保留.

**正确做法**:
```python
# ✅ 保留真实业务逻辑，Mock外部依赖
def test_component_business_logic(self, test_user):
    mock_database = Mock()
    mock_database.query.return_value = {"id": "test", "data": "test_data"}
    component = BusinessComponent(database=mock_database)
    result = component.process_data("input", user_id=test_user)
    assert result.success is True
    assert result.processed_data == "expected_output"

# ❌ 错误：Mock业务逻辑
def test_component_business_logic(self):
    component = Mock()  # 错误：Mock了要测试的对象
```

## 3. 项目架构单元测试策略

> 本节只规定**测试原则与规范要求**, 不绑定具体类名或方法签名. 业务代码随版本演进, 而测试原则保持稳定; 具体写法以 `tests/unit/` 下已通过 CI 的真实测试为基准参考.

### 3.1 分层测试职责

项目按 "Service层 → 工具层 → Agent层 → 记忆层 → 配置层" 分层. 单元测试以**单一被测对象**为边界, 每层只验证本层业务逻辑, 跨层依赖一律 Mock.

| 架构层 | 测试职责(保留真实逻辑) | 依赖处理原则 |
|--------|------------------------|--------------|
| **Service层** | 业务规则、数据组装、跨DAO编排 | Mock DAO / 会话工厂 / 外部服务 |
| **工具层** | 工具的业务处理、参数校验、结果格式化 | Mock 被工具调用的Service / 记忆 / 外部接口 |
| **Agent层** | 创建流程、配置装配、依赖编排 | Mock LLM/Embedding加载 / 具体Agent实现 |
| **记忆层** | 记忆组装、检索排序、上下文构建 | Mock 向量库 / 数据库 / Embedding模型 |
| **配置层** | 配置解析、校验、合并、默认值 | Mock 配置文件内容 / 环境变量 |

**当前架构重点**: Service层是业务逻辑主战场, 应优先保证充分覆盖; DAO的内部细节由Service层测试间接覆盖, 原则上不为纯DAO直接访问单独编写测试.

### 3.2 异步测试范式(强制)

项目业务代码全面异步(`async`/`await`), 单元测试必须遵循异步范式:

- **异步被测方法**: 测试函数标注 `@pytest.mark.asyncio`, 用 `await` 调用被测方法
- **异步依赖Mock**: 一律使用 `AsyncMock`, 禁止用同步 `Mock` 模拟异步方法
- **异步调用断言**: 异步方法的调用校验使用 `assert_awaited` / `assert_awaited_once_with`
- **禁止**: 遗漏 `@pytest.mark.asyncio`、用同步Mock模拟异步方法、在异步测试中 `await` 真实外部资源

### 3.3 依赖注入与Mock策略

被测对象与依赖的边界, 按以下优先级处理:

1. **构造注入优先**: 被测对象通过构造函数接收依赖时, 直接传入 Mock 实例(最清晰, 推荐)
2. **内部依赖用patch**: 依赖在模块内部创建或为单例时, 用 `patch` 替换其引用路径

> 不Mock被测对象本身(见 §2.2); 测试结构(AAA)见 §5.3.

### 3.4 单元测试边界(禁止项)

以下行为会使测试降级为集成测试, 单元测试中禁止出现:

- **禁止跨组件协作**: 一个测试只验证一个被测对象, 其他组件一律 Mock
- **禁止真实外部资源**: 数据库、网络、文件系统、向量库、LLM/Embedding 调用全部 Mock
- **禁止真实时间与随机性**: 时间、定时器、随机数、UUID 生成需固定或 Mock
- **禁止性能测试**: 执行时间、内存占用等属于专项测试, 不在单元测试范围
- **禁止依赖执行顺序**: 每个测试必须相互独立, 不依赖前序测试的副作用
- **禁止 mock 测 mock**: Mock 被测对象的依赖后, 若唯一断言是 `mock.assert_called_with(...)` 而不验证被测代码自身的业务转换逻辑, 等于在测试 mock 配置而非被测代码——被测代码是 trivial 薄包装时应由调用方间接覆盖(§3.1), 不单独测试

### 3.5 单元测试豁免情形

§2.2 "外部完全Mock" 与 §3.4 禁止项是硬性原则, 不降级为使用真实外部服务.
但以下情形, 可以**不设计该单元测试**(豁免 = 跳过测试设计, ≠ 允许用真实服务):

**情形1: 测试对象实为框架/外部行为本身**
若测试核心断言验证的是框架返回值结构、框架内部行为或语言构造本身(而非被测代码的业务转换逻辑),
该测试不应存在——它不是单元测试的职责, 是在测框架/语言本身.
- 判断标准: 移除被测代码、替换为框架原生调用后, 断言仍成立 → 测的是框架而非业务
- 典型: 断言枚举字面值(`assert ModelType.CHAT == "chat"`)、断言 `lru_cache`/单例装饰器保证的身份(`assert get_x() is get_x()`)

**情形2: 外部行为难以干净 mock**
当被测对象内部启动 LangChain Agent(如专家工具 deep 模式), 其编排行为(工具选择、多轮决策、stream 事件协议)依赖
LangGraph 运行时, 无法在不引入脆弱性的前提下干净 mock 时, 跳过该单元测试设计,
patch 入口函数留痕并由集成测试承接.

**情形3: 测试对象实为 trivial 代码**
若被测代码是字段赋值、构造函数存储、无逻辑 getter或
参数透传薄包装——移除被测代码后 Python 语义仍保证
断言成立——该测试不应单独存在, 由调用方的测试间接覆盖.

- 判断标准: 同情形1——"移除被测代码、替换为最小 Python 语义后, 断言仍成立"
- 与情形1的区别: 情形1聚焦框架行为, 情形3聚焦被测代码自身复杂度不足以支撑独立测试价值

> 注意: 仅"调用 create_agent"不构成豁免——create_agent 本身可 patch. 豁免的是 Agent 运行时的编排行为.

**留痕约定**: 对豁免的被测对象, 在源码处标注标准化注释(英文前缀便于 grep):
`# UNIT_TEST_EXEMPT: <中文原因>. 集成测试: <承接的 test 文件>.`
集成测试设计者据此重点覆盖. 源码注释即为**唯一索引**, 以 `grep -r UNIT_TEST_EXEMPT src/` 查询;
不另建文档登记表 (源码是 SSOT, 文档登记表会与之漂移).

## 4. 测试数据隔离规范

> 项目通过 `tests/conftest.py` 提供统一的测试身份 fixture, 实现 user_id / thread_id 的统一获取与并发安全. 严禁在测试中硬编码身份字面量.

### 4.1 统一测试身份

测试所需的用户ID、线程ID必须通过 fixture 获取, 由 pytest 自动注入. 具体 fixture 名称与行为见 `tests/conftest.py`.

**正确**: 测试函数声明 `test_user` / `test_thread_id` 形参, 由 fixture 注入.
**错误**: 在测试体内硬编码 `user_id = "test_user"` 等字面量(违反统一管理、破坏并发隔离).

### 4.2 并发隔离与清理

- pytest-xdist 并发下, user_id 自带进程后缀, thread_id 绑定测试函数名, 保证跨 worker 唯一
- 禁止多个测试共享可变的真实数据目录; 涉及路径隔离的测试应验证身份落到隔离目录(用户维度 user_id / 线程维度 thread_id)
- 测试临时数据由 fixture(会话级/函数级)自动清理; 长期残留的测试线程目录由清理工具按时间阈值回收

## 5. 测试质量标准

### 5.1 命名规范

**标准格式**: `test_[功能]_[场景]_[期望结果]`

```python
# ✅ 正确的命名
def test_todo_create_should_return_success_when_valid_data(self, test_user):
    """测试TODO创建：有效数据时应返回成功"""
    pass

# ❌ 模糊的命名
def test_todo_1(self, test_user):
    """不清晰的测试名称"""
    pass
```

### 5.2 文档字符串规范

测试应通过 docstring 说明意图, 便于维护与协作:

**方法级**:
- **测试目的**: 简述验证什么功能
- **业务价值**: 说明重要性和影响
- **验证要点**: 1,2,3 列表形式的关键验证点

**类级**:
- **测试职责**: 这个测试类的职责范围
- **测试覆盖**: 覆盖的功能和场景
- **Mock策略**: Mock 的使用策略和边界

### 5.3 测试结构标准

**AAA模式**: Arrange(准备)、Act(执行)、Assert(验证)

```python
def test_component_processing_standard_template(self, test_user):
    """标准测试结构模板"""

    # Arrange - 准备测试数据和Mock
    input_data = {"key": "value"}
    mock_external = Mock()
    mock_external.process.return_value = {"result": "processed"}
    component = TestComponent(external_service=mock_external)

    # Act - 执行测试操作
    result = component.process_data(input_data, user_id=test_user)

    # Assert - 验证结果
    assert result.success is True
    assert result.output["result"] == "processed"
    mock_external.process.assert_called_once_with(input_data)
```

### 5.4 覆盖率要求

单元测试覆盖率不作为独立 CI 门禁(CI 门禁是 unit+integration 合并覆盖率, 见 testing.md 质量门禁表). 单元测试追求高覆盖作为设计目标.

### 5.5 性能要求

执行时间上限见 testing.md 质量门禁表. 单元测试应保持毫秒级, 不引入重量级资源.

### 5.6 断言深度要求

断言须验证**具体业务结果**(值/状态/副作用), 以下作为**唯一断言**时禁止:

- **禁止恒真断言**: `assert True` / `assert x is x` / 注释"不抛异常即成功"
- **禁止裸存在性断言**: `assert result is not None` 作为唯一断言(不验证内容)
- **禁止裸类型断言**: `assert isinstance(result, list)` 作为唯一断言(不验证元素)
- **禁止裸调用断言**: `assert mock.called` 作为唯一断言(不验证参数/返回值/副作用)

> 上述形式可作为**辅助断言**配合实质断言使用, 但不可单独构成测试的全部验证.

### 5.7 配置值断言规范

**可调配置值**——来自 config.yaml/agent.yaml 的默认值或代码硬编码常量(timeout/cache_size/model_id/URL/budget/threshold)——禁止精确断言(`==`), 须用**守卫断言**(类型/非空/范围).

原因: 这些值"本来就该可调", 精确断言使测试与配置耦合, 配置合理调整时假阳性失败——测试从"保护网"退化为"绊脚石".

```python
# ✅ 守卫断言: 验证默认值存在且合理, 不锁定具体值
assert isinstance(config.timeout, int) and config.timeout > 0
assert isinstance(config.model_id, str) and ":" in config.model_id

# ❌ 脆弱断言: 锁定可调配置默认值
assert config.timeout == 120          # 改默认超时即假阳性失败
assert config.model_id == "deepseek:deepseek-v4-pro"  # 换模型即假阳性失败
```

**常量驱动的行为契约——引用常量而非字面值(第三路径)**: 当断言守护的是"截断/回退/上限"等由常量驱动的**行为契约**(而非默认值本身), 守卫断言过弱(`timeout > 0` 验证不到"截断到上限"这个行为). 此时将源码内联魔法数字提取为模块级命名常量, 测试 import 该常量引用——既守住行为契约, 改常量时测试又自动跟随:

```python
# 源码 src/tools/skills/skill_executor_tool.py
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 120.0
def _normalize_timeout(self, t): return min(DEFAULT_TIMEOUT if t is None else t, MAX_TIMEOUT)

# 测试: 引用常量, 验证"超过上限即截断"行为, 不锁数值
from src.tools.skills.skill_executor_tool import MAX_TIMEOUT
assert tool._normalize_timeout(999) == MAX_TIMEOUT
```

适用判定: 断言对象是"行为契约"(如截断/回退/边界)且该契约由某个常量参数化时, 优先用常量引用; 断言对象是"默认值合理性"时用守卫断言.

**例外** — 以下情况精确断言合理:
- 测试**自行设置的值**(`Config(timeout=60); assert config.timeout == 60`)
- 测试**覆盖/解析逻辑**(monkeypatch env → 断言 env 值生效; `from_dict({...})` → 断言解析正确)
- 测试**业务规则的不变量**(如"默认启用"的语义关键: `assert config.enabled is True`)

## 6. 最佳实践

**参数化测试**
```python
@pytest.mark.parametrize("input_data,expected_result", [
    ("valid_input", "success"),
    ("invalid_input", "error"),
    ("empty_input", "error"),
    ("null_input", "error")
])
def test_input_validation(self, input_data, expected_result, test_user):
    """参数化测试多种输入情况"""
    component = TestComponent()
    result = component.validate_input(input_data, user_id=test_user)
    assert result.status == expected_result
```

**避免重复测试**

- **同文件近似**: 多个测试用例覆盖同一被测代码路径、仅输入/输出不同时, 合并为参数化测试(如上), 不要复制粘贴成 N 个独立函数.
- **跨文件重复(新增测试前必查)**: 新增测试文件前, 先 `grep`/`Glob` 同名被测对象是否已有覆盖文件. 跨目录整文件级重复(如 `test_unified_cache.py` 与 `test_cache_v2.py` 完全重叠)是最高代价的重复——既浪费维护成本, 又在重构时需多处同步修改. 判定: 若新文件的全部断言都能在既有文件中找到等价覆盖, 不应新增.

## 7. Mock 体系

项目 Mock 遵循"按需使用, 重复三次再抽象"原则, 提供三层方案:

| 层级 | 工具 | 适用场景 | 源码 |
|------|------|---------|------|
| 内联 Mock (90%) | `AsyncMock()` / `Mock()` + `@patch` | 大部分场景 | `unittest.mock` |
| ServiceMockFactory | `ServiceMockFactory` | Service 层依赖 | `tests/mocks/service_mock_factory.py` |
| UnifiedMockFactory | `UnifiedMockFactory` | LLM/Embedding/VectorStore | `tests/mocks/unified_factory.py` |

**使用原则**:
- 同一文件内重复 3 次以上的 Mock 模式才提取到对应层级 conftest
- 不新建全局 Mock Factory
- ServiceMockFactory 与 UnifiedMockFactory 的 API 签名、默认返回值、覆盖方式以源码为准

### 7.1 断言规范

项目**不提供全局断言辅助模块**, 断言直接编写在测试函数的 Assert 段, 保持就近可读:

- **直接断言优先**: 业务结果用 `assert` 直接验证, 不引入额外抽象层
- **局部辅助按需提取**: 某层(如记忆层)存在多处重复断言时, 可在测试基类中定义局部 `assert_xxx` 方法, 遵循"重复三次再抽象"原则
- **禁止**: 引用不存在的全局断言模块; 为单次使用的断言提前封装辅助函数

---

**最后更新**: 2026-07-02
**架构版本**: v1.9.0 分层架构(Service层 + 工具层 + Agent层 + 记忆层 + 配置层) + 双路检索记忆
**适用范围**: Personal Agent Assistant项目所有单元测试

**重要提醒**: 本文档是单元测试的**唯一权威设计规范**, 所有单元测试的设计和开发都必须严格遵循本文档标准.
